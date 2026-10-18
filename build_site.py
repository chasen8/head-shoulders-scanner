"""Build the static site's data: fetch Binance public klines, scan, write site/data/.

Output (front end only draws these values; every number it shows is computed here):
  site/data/scan.json            metadata + one summary row per pattern (no bars)
  site/data/bars/<file>.json     chart data for one pattern (bars, five points, neckline, breakout),
                                 loaded by the page only when that pattern is opened

Partial success: a symbol/timeframe that fails to fetch or scan is listed in scan.json `failures`.
Only when more than MAX_FAILURE_RATIO of the series fail does the build exit non-zero and leave
the previous scan.json and bars/ untouched.

Example (Git Bash, project root):
    /c/trading-data/ihs-scanner-v2/venv/Scripts/python.exe build_site.py            # all USDT pairs
    /c/trading-data/ihs-scanner-v2/venv/Scripts/python.exe build_site.py --symbols BTCUSDT,ETHUSDT
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

import pandas as pd
import requests

from ihs_scanner.config import TIMEFRAMES, merge_config
from ihs_scanner.data_binance import (BinanceFetchError, WeightLimiter, fetch_klines, fetch_quote_volumes,
                                      list_usdt_symbols)
from ihs_scanner.neckline import calculate_neckline
from ihs_scanner.scanner import OUTPUT_FIELDS, _to_jsonable, scan_symbols, sort_results
from ihs_scanner.swings import get_timestamps

DEFAULT_OUT = Path(__file__).resolve().parent / "site" / "data" / "scan.json"
BARS_DIRNAME = "bars"
BARS_BEFORE_LS = 15  # chart context to the left of the left shoulder
# Weekly history is short (BTC has < 500 weekly bars) and patterns span <= 80 bars.
LIMIT_CAP_BY_TIMEFRAME = {"1w": 500}
MAX_FAILURE_RATIO = 0.20
MAX_WORKERS = 4
ERROR_CHARS = 240
# Must match .github/workflows/scan-and-deploy.yml: cron "7 */4 * * *"
SCAN_EVERY_HOURS = 4
SCAN_MINUTE = 7
SCHEMA_VERSION = 2
POINT_NAMES = ("LS", "N1", "H", "N2", "RS")


def _csv_list(text: str) -> list[str]:
    return [s.strip() for s in text.split(",") if s.strip()]


def _iso(ts: Any) -> str | None:
    return _to_jsonable(pd.Timestamp(ts)) if ts is not None else None


def _unix(ts: Any) -> int:
    t = pd.Timestamp(ts)
    t = t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")
    return int(t.value // 1_000_000_000)


def _num(x: Any) -> float | None:
    f = float(x)
    return f if math.isfinite(f) else None


def _short_error(exc: BaseException | str) -> str:
    text = exc if isinstance(exc, str) else f"{type(exc).__name__}: {exc}"
    text = " ".join(str(text).split())
    return text if len(text) <= ERROR_CHARS else text[: ERROR_CHARS - 1] + "…"


def next_scan_time(now: datetime) -> datetime:
    """First cron slot (minute SCAN_MINUTE of every SCAN_EVERY_HOURS-th UTC hour) after `now`."""
    now = now.astimezone(timezone.utc)
    slot_hour = now.hour - now.hour % SCAN_EVERY_HOURS
    slot = now.replace(hour=slot_hour, minute=SCAN_MINUTE, second=0, microsecond=0)
    while slot <= now:
        slot += timedelta(hours=SCAN_EVERY_HOURS)
    return slot


def limit_for(timeframe: str, limit: int) -> int:
    return min(limit, LIMIT_CAP_BY_TIMEFRAME.get(timeframe, limit))


def chart_file_name(result: Mapping[str, Any]) -> str:
    """Filesystem/URL-safe, unique file name for one pattern's chart data.

    Symbols can be non-ASCII (e.g. 币安人生USDT), so unsafe characters become "_" and a short
    hash of pattern_id keeps names unique."""
    safe_sym = re.sub(r"[^A-Za-z0-9]", "_", str(result["symbol"])).strip("_") or "SYM"
    digest = hashlib.sha1(str(result["pattern_id"]).encode("utf-8")).hexdigest()[:10]
    return f"{safe_sym}_{result['timeframe']}_{_unix(result['LS_time'])}_{digest}.json"


def chart_for(result: Mapping[str, Any], df: pd.DataFrame, angle_scale: float) -> dict[str, Any]:
    """Everything the chart needs, in UNIX seconds (UTC).

    bars: [[time, open, high, low, close, volume], ...] from BARS_BEFORE_LS bars before LS
    through the last closed bar. neckline: the N1-N2 line evaluated (neckline_price_at) at
    the first bar of the LS and at the last bar, so the front end draws a straight segment.
    """
    ts = get_timestamps(df)
    pos = {name: int(ts.get_loc(pd.Timestamp(result[f"{name}_time"]))) for name in POINT_NAMES}
    last = len(df) - 1
    start = max(0, pos["LS"] - BARS_BEFORE_LS)
    neck = calculate_neckline((pos["N1"], result["N1_price"]), (pos["N2"], result["N2_price"]), angle_scale)

    seg = df.iloc[start:last + 1]
    bars = [
        [_unix(t), _num(o), _num(h), _num(lo), _num(c), _num(v)]
        for t, o, h, lo, c, v in zip(ts[start:last + 1], seg["open"], seg["high"], seg["low"],
                                     seg["close"], seg["volume"])
    ]
    points = [{"label": name, "time": _unix(result[f"{name}_time"]), "price": _num(result[f"{name}_price"])}
              for name in POINT_NAMES]
    breakout = None
    if result.get("breakout_detected"):
        breakout = {"time": _unix(result["breakout_time"]), "price": _num(result["breakout_price"])}
    return {
        "bars": bars,
        "points": points,
        "neckline": [
            {"time": _unix(ts[pos["LS"]]), "price": _num(neck.neckline_price_at(pos["LS"]))},
            {"time": _unix(ts[last]), "price": _num(neck.neckline_price_at(last))},
        ],
        "breakout": breakout,
    }


def shape_for(chart: Mapping[str, Any]) -> dict[str, Any]:
    """Tiny summary for the list silhouette: [time, price] pairs only (no bars)."""
    last = chart["bars"][-1]
    brk = chart["breakout"]
    return {
        "points": [[p["time"], p["price"]] for p in chart["points"]],
        "neckline": [[p["time"], p["price"]] for p in chart["neckline"]],
        "breakout": [brk["time"], brk["price"]] if brk else None,
        "last": [last[0], last[4]],
    }


def _neckline_side(r: Mapping[str, Any]) -> str:
    close, neck = float(r["current_close"]), float(r["current_neckline_price"])
    return "above" if close > neck else ("below" if close < neck else "at")


def build_payload(symbol_data: Mapping[str, Mapping[str, pd.DataFrame]], cfg: Mapping[str, Any], *,
                  symbols: list[str], timeframes: list[str], limit: int,
                  generated_at: datetime | None = None, scheduled: bool = False,
                  failures: list[dict[str, Any]] | None = None,
                  skipped: list[dict[str, Any]] | None = None,
                  symbols_source: str = "list") -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """Returns (scan.json payload, {bars file name: chart data}).

    Each symbol/timeframe is scanned on its own; a detector exception is recorded in `failures`
    (stage "scan") instead of aborting the build. scheduled=False -> next_scan_at is null."""
    generated_at = generated_at or datetime.now(timezone.utc)
    failures = list(failures or [])
    results: list[dict[str, Any]] = []
    for sym, by_tf in symbol_data.items():
        for tf, df in by_tf.items():
            try:
                results.extend(scan_symbols({sym: {tf: df}}, cfg))
            except Exception as exc:  # one bad series must not sink the whole site
                failures.append({"symbol": sym, "timeframe": tf, "stage": "scan", "error": _short_error(exc)})
    results = sort_results(results)

    angle_scale = float(cfg["angle_scale"])
    rows, charts = [], {}
    for r in results:
        try:
            chart = chart_for(r, symbol_data[r["symbol"]][r["timeframe"]], angle_scale)
        except Exception as exc:
            failures.append({"symbol": r["symbol"], "timeframe": r["timeframe"], "stage": "chart",
                             "error": _short_error(exc)})
            continue
        keys = OUTPUT_FIELDS + [k for k in r if k not in OUTPUT_FIELDS]
        row = {k: _to_jsonable(r.get(k)) for k in keys}
        row["rank"] = len(rows) + 1
        row["neckline_side"] = _neckline_side(r)
        name = chart_file_name(r)
        row["chart_file"] = f"{BARS_DIRNAME}/{name}"
        row["shape"] = shape_for(chart)
        charts[name] = {"schema_version": SCHEMA_VERSION, "pattern_id": row["pattern_id"],
                        "symbol": row["symbol"], "timeframe": row["timeframe"], **chart}
        rows.append(row)

    last_closed: dict[str, str | None] = {}
    for tf in timeframes:
        stamps = [get_timestamps(by_tf[tf])[-1] for by_tf in symbol_data.values()
                  if tf in by_tf and len(by_tf[tf])]
        last_closed[tf] = _iso(max(stamps)) if stamps else None
    scanned = sorted(sym for sym, by_tf in symbol_data.items() if by_tf)
    series = sum(len(by_tf) for by_tf in symbol_data.values())
    payload = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "next_scan_at": next_scan_time(generated_at).strftime("%Y-%m-%dT%H:%M:%SZ") if scheduled else None,
        "settings": {
            "symbols_source": symbols_source,
            "symbols": symbols,
            "timeframes": timeframes,
            "mode": "breakout" if cfg["enable_breakout_filter"] else "candidate",
            "include_stale": bool(cfg["include_stale"]),
            "volume_filter": bool(cfg["enable_volume_filter"]),
            "min_pattern_score": float(cfg["min_pattern_score"]),
            "bars_per_series": {tf: limit_for(tf, limit) for tf in timeframes},
            "data_source": "Binance public klines (data-api.binance.vision), closed bars only",
        },
        "requested_symbols": len(symbols),
        "scanned_symbols": len(scanned),
        "series_total": len(symbols) * len(timeframes),
        "series_scanned": series,
        "failures": failures,
        "skipped": list(skipped or []),
        "last_closed": last_closed,
        "results": rows,
    }
    return payload, charts


def _dump(obj: Any, path: Path) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _replace_dir(src: Path, dst: Path, attempts: int = 5) -> None:
    """Swap src into dst. Retries because OneDrive/antivirus can briefly lock files on Windows."""
    for i in range(attempts):
        try:
            if dst.exists():
                shutil.rmtree(dst)
            src.replace(dst)
            return
        except OSError:
            if i == attempts - 1:
                raise
            time.sleep(0.5 * (i + 1))


def write_site(payload: Mapping[str, Any], charts: Mapping[str, Mapping[str, Any]], out: Path) -> dict[str, Any]:
    """Write bars/ (fresh: old files never linger) then scan.json, both swapped in at the end.

    Nothing is deleted before all new files are written, so a crash mid-build leaves the previous
    site intact. Returns sizes in bytes."""
    out.parent.mkdir(parents=True, exist_ok=True)
    bars_dir = out.parent / BARS_DIRNAME
    tmp_dir = out.parent / (BARS_DIRNAME + ".tmp")
    if tmp_dir.exists():
        shutil.rmtree(tmp_dir)
    tmp_dir.mkdir()
    total = 0
    for name, chart in charts.items():
        path = tmp_dir / name
        _dump(chart, path)
        total += path.stat().st_size
    tmp = out.with_suffix(out.suffix + ".tmp")
    _dump(payload, tmp)
    _replace_dir(tmp_dir, bars_dir)
    tmp.replace(out)
    return {"scan_json": out.stat().st_size, "bars": total, "bars_files": len(charts)}


def write_payload(payload: Mapping[str, Any], out: Path) -> Path:
    """Write only scan.json (kept for callers that already have a payload)."""
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(out.suffix + ".tmp")
    _dump(payload, tmp)
    tmp.replace(out)
    return out


def fetch_all(symbols: list[str], timeframes: list[str], limit: int, *, limiter: WeightLimiter,
              workers: int = MAX_WORKERS, progress_every: int = 150):
    """Fetch every symbol x timeframe with <= `workers` threads sharing one weight limiter.

    Returns (symbol_data, failures, skipped); empty series (e.g. a pair listed this week has no
    closed weekly bar yet) are `skipped`, not failures."""
    local = threading.local()

    def session():
        if not hasattr(local, "s"):
            local.s = requests.Session()
        return local.s

    def job(sym: str, tf: str):
        return fetch_klines(sym, tf, limit_for(tf, limit), session=session(), limiter=limiter)

    tasks = [(s, tf) for s in symbols for tf in timeframes]
    symbol_data: dict[str, dict[str, pd.DataFrame]] = {}
    failures: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    done = 0
    with ThreadPoolExecutor(max_workers=max(1, min(workers, MAX_WORKERS))) as pool:
        futs = {pool.submit(job, s, tf): (s, tf) for s, tf in tasks}
        for fut in as_completed(futs):
            sym, tf = futs[fut]
            done += 1
            try:
                df = fut.result()
            except (BinanceFetchError, ValueError, requests.RequestException) as exc:
                failures.append({"symbol": sym, "timeframe": tf, "stage": "fetch", "error": _short_error(exc)})
                print(f"[fetch FAIL] {sym} {tf}: {_short_error(exc)}", file=sys.stderr)
            else:
                if df.empty:
                    skipped.append({"symbol": sym, "timeframe": tf, "reason": "no closed klines yet"})
                else:
                    symbol_data.setdefault(sym, {})[tf] = df
            if done % progress_every == 0 or done == len(tasks):
                print(f"[fetch] {done}/{len(tasks)} series, failures={len(failures)}, "
                      f"max used weight/min={limiter.max_seen}")
    # deterministic order (as_completed is not)
    order = {s: i for i, s in enumerate(symbols)}
    symbol_data = {s: {tf: symbol_data[s][tf] for tf in timeframes if tf in symbol_data[s]}
                   for s in sorted(symbol_data, key=order.get)}
    key = lambda f: (order.get(f["symbol"], 0), timeframes.index(f["timeframe"]))  # noqa: E731
    return symbol_data, sorted(failures, key=key), sorted(skipped, key=key)


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Scan Binance public klines and write site/data/")
    p.add_argument("--symbols", default="all",
                   help='"all" = every Binance spot USDT pair trading (default), or comma separated list')
    p.add_argument("--timeframes", default="4h,1d,1w", help="comma separated subset of 4h,1d,1w")
    p.add_argument("--limit", type=int, default=1000,
                   help="closed klines per symbol/timeframe (1w is capped at %d)" % LIMIT_CAP_BY_TIMEFRAME["1w"])
    p.add_argument("--min-quote-volume", type=float, default=0.0,
                   help="skip symbols whose 24h quote volume (USDT) is below this; 0 = no filter")
    p.add_argument("--workers", type=int, default=MAX_WORKERS, help=f"fetch threads (max {MAX_WORKERS})")
    p.add_argument("--weight-threshold", type=int, default=4500,
                   help="pause until the next minute when used weight/min would exceed this (IP limit 6000)")
    p.add_argument("--include-stale", action="store_true",
                   help="also include stale patterns (for local preview; production leaves this off)")
    p.add_argument("--out", default=str(DEFAULT_OUT), help="output JSON path (default site/data/scan.json)")
    p.add_argument("--scheduled", action="store_true",
                   help="a cron runs this every 4h: publish next_scan_at (automatic when GITHUB_EVENT_NAME=schedule)")
    return p.parse_args(argv)


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass
    t0 = time.perf_counter()
    args = parse_args(argv)
    timeframes = _csv_list(args.timeframes)
    bad = [tf for tf in timeframes if tf not in TIMEFRAMES]
    if not timeframes or bad or args.limit < 1:
        print(f"error: need timeframes from {TIMEFRAMES} and limit >= 1; bad timeframes: {bad}", file=sys.stderr)
        return 2
    limiter = WeightLimiter(threshold=args.weight_threshold)

    use_all = args.symbols.strip().lower() == "all"
    try:
        symbols = list_usdt_symbols(limiter=limiter) if use_all else \
            list(dict.fromkeys(s.upper() for s in _csv_list(args.symbols)))
        if args.min_quote_volume > 0:
            vols = fetch_quote_volumes(limiter=limiter)
            before = len(symbols)
            symbols = [s for s in symbols if vols.get(s, 0.0) >= args.min_quote_volume]
            print(f"[symbols] {len(symbols)} of {before} have 24h quote volume >= {args.min_quote_volume:,.0f} USDT")
    except (BinanceFetchError, requests.RequestException) as exc:
        print(f"error: could not get the symbol list: {_short_error(exc)}; {args.out} was NOT updated",
              file=sys.stderr)
        return 1
    if not symbols:
        print("error: no symbols to scan", file=sys.stderr)
        return 2

    symbol_data, failures, skipped = fetch_all(symbols, timeframes, args.limit, limiter=limiter,
                                               workers=args.workers)
    t_fetch = time.perf_counter() - t0

    cfg = merge_config({"include_stale": bool(args.include_stale)})
    scheduled = bool(args.scheduled) or os.environ.get("GITHUB_EVENT_NAME") == "schedule"
    payload, charts = build_payload(symbol_data, cfg, symbols=symbols, timeframes=timeframes, limit=args.limit,
                                    scheduled=scheduled, failures=failures, skipped=skipped,
                                    symbols_source="all" if use_all else "list")
    total = payload["series_total"]
    failed_series = {(f["symbol"], f["timeframe"]) for f in payload["failures"]}
    ratio = len(failed_series) / total if total else 1.0
    if ratio > MAX_FAILURE_RATIO:
        print(f"error: {len(failed_series)} of {total} series failed ({ratio:.0%} > {MAX_FAILURE_RATIO:.0%}); "
              f"{args.out} was NOT updated", file=sys.stderr)
        return 1

    sizes = write_site(payload, charts, Path(args.out))
    counts: dict[str, int] = {}
    for r in payload["results"]:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    elapsed = time.perf_counter() - t0
    print(f"\nscanned {payload['scanned_symbols']} symbols / {payload['series_scanned']} of {total} series "
          f"in {elapsed:.1f}s (fetch {t_fetch:.1f}s); failures={len(payload['failures'])} "
          f"skipped={len(payload['skipped'])}")
    print(f"max X-MBX-USED-WEIGHT-1M seen: {limiter.max_seen}; throttle waits: {limiter.throttle_waits} "
          f"({limiter.slept:.1f}s)")
    print(f"wrote {args.out} ({sizes['scan_json'] / 1024:.0f} KB) + {sizes['bars_files']} bars files "
          f"({sizes['bars'] / 1024:.0f} KB): {len(payload['results'])} patterns {counts}; "
          f"next scan ~{payload['next_scan_at'] or 'not scheduled'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
