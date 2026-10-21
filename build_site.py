"""Build the static site's data: fetch exchange public klines, scan, write site/data/.

Sources (--sources, charter D22): binance-spot, binance-perp (opt-in: fapi answers 451 on US runners),
okx-spot, okx-perp, gate-perp. A base coin is scanned from ONE source only, by priority
binance-spot > binance-perp > okx-spot > okx-perp > gate-perp. Every result carries exchange, market,
tv_symbol; pattern_id is prefixed "<exchange>|<market>|".

Output (front end only draws these values; every number it shows is computed here):
  site/data/scan.json            metadata + one summary row per pattern (no bars)
  site/data/bars/<file>.json     chart data for one pattern (bars, five points, neckline, breakout,
                                 TP1/TP2/invalidation levels), loaded by the page only when opened

Partial success: a symbol/timeframe that fails to fetch or scan is listed in scan.json `failures`.
Only when more than MAX_FAILURE_RATIO of the series fail does the build exit non-zero and leave
the previous scan.json and bars/ untouched.

Example (Git Bash, project root):
    /c/trading-data/ihs-scanner-v2/venv/Scripts/python.exe build_site.py            # all default sources
    /c/trading-data/ihs-scanner-v2/venv/Scripts/python.exe build_site.py --sources binance-spot,okx-perp
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
from typing import Any, Callable, Mapping, Sequence

import pandas as pd
import requests

from ihs_scanner import data_binance_futures, data_gate, data_okx
from ihs_scanner.config import TIMEFRAMES, merge_config
from ihs_scanner.data_binance import (BinanceFetchError, WeightLimiter, fetch_klines, fetch_quote_volumes,
                                      list_usdt_symbols)
from ihs_scanner.keys import pattern_key
from ihs_scanner.neckline import calculate_neckline
from ihs_scanner.scanner import (OUTPUT_FIELDS, _to_jsonable, scan_symbols, sort_results,
                                 supersede_by_higher_timeframe)
from ihs_scanner.sources import SOURCE_PRIORITY, Instrument, SourceFetchError, merge_instruments
from ihs_scanner.swings import get_timestamps
from ihs_scanner.validation import CHECKS, shape_checks

DEFAULT_OUT = Path(__file__).resolve().parent / "site" / "data" / "scan.json"
# D29: Taiwan stocks are a separate market with their own scan.json + bars/ (written by build_site_tw.py),
# so the crypto and the Taiwan scans never overwrite each other.
DEFAULT_OUT_TW = Path(__file__).resolve().parent / "site" / "data" / "tw" / "scan.json"
MARKETS = ("crypto", "tw")
BARS_DIRNAME = "bars"
BARS_BEFORE_LS = 15  # chart context to the left of the left shoulder
# Weekly history is short (BTC has < 500 weekly bars) and patterns span <= 80 bars.
LIMIT_CAP_BY_TIMEFRAME = {"1w": 500}
MAX_FAILURE_RATIO = 0.20
MAX_WORKERS = 4
ERROR_CHARS = 240
# Must match .github/workflows/scan-and-notify.yml: cron "7 */4 * * *"
SCAN_EVERY_HOURS = 4
SCAN_MINUTE = 7
SCHEMA_VERSION = 8  # 3: bars files carry `levels`, `levels_estimated`, `rs_provisional`;
#                     4: rows carry `shape_checks`, settings `shape_validation` (D26);
#                     5: rows carry `pattern_key` (D24 key, no RS) for the site watchlist;
#                     6: rows carry `neckline_change_pct` and `neckline_trend`, settings `neckline_trend_threshold`;
#                     7: payload `market` ("crypto" | "tw"), rows (and bars files) may carry `name` (D29 Taiwan stocks)
#                     8: rows carry `neckline_touches`, `breakout_basis`; payload `superseded`, settings
#                        `breakout_price` (D36)
# Neckline N1->N2 total change: <= -T is "down", >= +T is "up", otherwise "flat" (front end only filters on it).
NECKLINE_TREND_THRESHOLD = 0.03
POINT_NAMES = ("LS", "N1", "H", "N2", "RS")
# Horizontal levels drawn on the chart (values come straight from the detector's output fields).
LEVELS = (("tp1", "TP1"), ("tp2", "TP2"), ("invalidation_price", "失效"))
# fapi.binance.com is HTTP 451 from GitHub's US runners, so binance-perp is not in the default set.
DEFAULT_SOURCES = ("binance-spot", "okx-spot", "okx-perp", "gate-perp")
ALL_SOURCES = SOURCE_PRIORITY
FETCH_ERRORS = (BinanceFetchError, SourceFetchError, ValueError, requests.RequestException)


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
    src = f"{result.get('exchange', 'binance')}_{result.get('market', 'spot')}"
    digest = hashlib.sha1(str(result["pattern_id"]).encode("utf-8")).hexdigest()[:10]
    return f"{src}_{safe_sym}_{result['timeframe']}_{_unix(result['LS_time'])}_{digest}.json"


def chart_for(result: Mapping[str, Any], df: pd.DataFrame, angle_scale: float) -> dict[str, Any]:
    """Everything the chart needs, in UNIX seconds (UTC).

    bars: [[time, open, high, low, close, volume], ...] from BARS_BEFORE_LS bars before LS
    through the last closed bar. neckline: the N1-N2 line evaluated (neckline_price_at) at
    the first bar of the LS and at the last bar, so the front end draws a straight segment.
    levels: TP1 / TP2 / invalidation (H.low) as horizontal lines; levels_estimated = not broken out yet
    (targets then use the current neckline price). rs_provisional: the right shoulder is still forming
    (no RS_confirm_time), so the RS point moves with new bars; the RS point carries "provisional".
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
    provisional = rs_provisional(result)
    points = [{"label": name, "time": _unix(result[f"{name}_time"]), "price": _num(result[f"{name}_price"])}
              for name in POINT_NAMES]
    if provisional:
        points[-1]["provisional"] = True
    levels = [{"key": key, "label": label, "price": _num(result[key])}
              for key, label in LEVELS if result.get(key) is not None and _num(result[key]) is not None]
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
        "levels": levels,
        "levels_estimated": not result.get("breakout_detected"),
        "rs_provisional": provisional,
    }


def rs_provisional(result: Mapping[str, Any]) -> bool:
    """True while the right shoulder is still forming (D23 status "forming": no RS_confirm_time)."""
    return result.get("RS_confirm_time") is None


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


def neckline_trend(r: Mapping[str, Any]) -> tuple[float | None, str | None]:
    """(N2_price / N1_price - 1, "down" | "flat" | "up"); D17: computed here, the front end only filters."""
    n1, n2 = r.get("N1_price"), r.get("N2_price")
    if not n1 or n2 is None or n1 <= 0:
        return None, None
    chg = float(n2) / float(n1) - 1.0
    t = NECKLINE_TREND_THRESHOLD
    return chg, ("down" if chg <= -t else "up" if chg >= t else "flat")


def _neckline_side(r: Mapping[str, Any]) -> str:
    close, neck = float(r["current_close"]), float(r["current_neckline_price"])
    return "above" if close > neck else ("below" if close < neck else "at")


def _default_instrument(symbol: str) -> Instrument:
    base = symbol[:-4] if symbol.endswith("USDT") and len(symbol) > 4 else symbol
    return Instrument(base=base, symbol=symbol, native=symbol, exchange="binance", market="spot")


def build_payload(symbol_data: Mapping[str, Mapping[str, pd.DataFrame]], cfg: Mapping[str, Any], *,
                  symbols: list[str], timeframes: list[str], limit: int,
                  generated_at: datetime | None = None, scheduled: bool = False,
                  failures: list[dict[str, Any]] | None = None,
                  skipped: list[dict[str, Any]] | None = None,
                  symbols_source: str = "list",
                  instruments: Mapping[str, Instrument] | None = None,
                  sources: Sequence[str] | None = None,
                  source_listed: Mapping[str, int] | None = None,
                  source_errors: list[dict[str, Any]] | None = None,
                  market: str = "crypto",
                  next_scan: Callable[[datetime], datetime] | None = None,
                  data_source: str | None = None,
                  extra_settings: Mapping[str, Any] | None = None) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """Returns (scan.json payload, {bars file name: chart data}).

    Each symbol/timeframe is scanned on its own; a detector exception is recorded in `failures`
    (stage "scan") instead of aborting the build. scheduled=False -> next_scan_at is null.
    market: "crypto" or "tw" (D29); next_scan: the market's cron schedule (default: every 4 h);
    instruments with a `name` (Taiwan stocks) put it on every row and bars file."""
    next_scan = next_scan or next_scan_time
    generated_at = generated_at or datetime.now(timezone.utc)
    failures = list(failures or [])
    instruments = instruments or {}

    def inst_of(sym: str) -> Instrument:
        return instruments.get(sym) or _default_instrument(sym)

    results: list[dict[str, Any]] = []
    for sym, by_tf in symbol_data.items():
        for tf, df in by_tf.items():
            try:
                results.extend(scan_symbols({sym: {tf: df}}, cfg))
            except Exception as exc:  # one bad series must not sink the whole site
                failures.append({"symbol": sym, "timeframe": tf, "stage": "scan", "error": _short_error(exc)})
    for r in results:  # tag the origin; pattern_id gets a prefix so ids never collide across sources
        inst = inst_of(r["symbol"])
        r["exchange"], r["market"], r["tv_symbol"] = inst.exchange, inst.market, inst.tv_symbol
        if inst.name:
            r["name"] = inst.name
        r["pattern_id"] = f"{inst.exchange}|{inst.market}|{r['pattern_id']}"
    superseded: list[dict[str, Any]] = []
    if cfg["supersede_by_higher_timeframe"]:  # D36: across timeframes, so after every series is scanned
        results, gone = supersede_by_higher_timeframe(results)
        superseded = [{"pattern_id": g["pattern_id"], "superseded_by": g["superseded_by"]} for g in gone]
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
        row["pattern_key"] = pattern_key(row)  # same function as notify_discord (D24); row times are strings
        row["neckline_side"] = _neckline_side(r)
        row["rs_provisional"] = rs_provisional(r)
        row["neckline_change_pct"], row["neckline_trend"] = neckline_trend(row)
        row["shape_checks"] = shape_checks(r, cfg)  # D26: value / limit / pass decided here (D17)
        name = chart_file_name(r)
        row["chart_file"] = f"{BARS_DIRNAME}/{name}"
        row["shape"] = shape_for(chart)
        charts[name] = {"schema_version": SCHEMA_VERSION, "pattern_id": row["pattern_id"],
                        "symbol": row["symbol"], "exchange": row["exchange"], "market": row["market"],
                        "timeframe": row["timeframe"], **({"name": row["name"]} if row.get("name") else {}), **chart}
        rows.append(row)

    last_closed: dict[str, str | None] = {}
    for tf in timeframes:
        stamps = [get_timestamps(by_tf[tf])[-1] for by_tf in symbol_data.values()
                  if tf in by_tf and len(by_tf[tf])]
        last_closed[tf] = _iso(max(stamps)) if stamps else None
    scanned = sorted(sym for sym, by_tf in symbol_data.items() if by_tf)
    series = sum(len(by_tf) for by_tf in symbol_data.values())
    for f in failures:
        inst = inst_of(f["symbol"])
        f.setdefault("exchange", inst.exchange)
        f.setdefault("market", inst.market)
    blank = {"listed": 0, "symbols": 0, "scanned_symbols": 0, "series_scanned": 0, "failed_series": 0}
    source_counts: dict[str, dict[str, int]] = {}
    for sym in symbols:
        source_counts.setdefault(inst_of(sym).source, dict(blank))["symbols"] += 1
    for sym, by_tf in symbol_data.items():
        c = source_counts.setdefault(inst_of(sym).source, dict(blank))
        c["scanned_symbols"] += 1 if by_tf else 0
        c["series_scanned"] += len(by_tf)
    for sym, _tf in {(f["symbol"], f["timeframe"]) for f in failures}:
        source_counts.setdefault(inst_of(sym).source, dict(blank))["failed_series"] += 1
    for key, n in (source_listed or {}).items():
        source_counts.setdefault(key, dict(blank))["listed"] = n
    payload = {
        "schema_version": SCHEMA_VERSION,
        "market": market,
        "generated_at": generated_at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "next_scan_at": next_scan(generated_at).strftime("%Y-%m-%dT%H:%M:%SZ") if scheduled else None,
        "settings": {
            "symbols_source": symbols_source,
            "symbols": symbols,
            "timeframes": timeframes,
            "mode": "breakout" if cfg["enable_breakout_filter"] else "candidate",
            "include_stale": bool(cfg["include_stale"]),
            "volume_filter": bool(cfg["enable_volume_filter"]),
            "min_pattern_score": float(cfg["min_pattern_score"]),
            "profile": str(cfg["profile"]),
            "neckline_trend_threshold": NECKLINE_TREND_THRESHOLD,
            "breakout_price": str(cfg["breakout_price"]),  # D36: "high" (wick) or "close"; front end wording
            # D26 thresholds (None = check off); the per-row values and pass flags are in shape_checks
            "shape_validation": {cid: (None if cfg[key] is None else float(cfg[key]))
                                 for cid, _f, key, _k, _t in CHECKS},
            "bars_per_series": {tf: limit_for(tf, limit) for tf in timeframes},
            "sources": list(sources) if sources else sorted(source_counts),
            "data_source": data_source or
                           "exchange public klines (" + ", ".join(sorted(source_counts)) + "), closed bars only",
            **dict(extra_settings or {}),
        },
        "source_counts": {k: source_counts[k] for k in sorted(source_counts)},
        "source_errors": list(source_errors or []),
        "requested_symbols": len(symbols),
        "scanned_symbols": len(scanned),
        "series_total": len(symbols) * len(timeframes),
        "series_scanned": series,
        "failures": failures,
        "skipped": list(skipped or []),
        "superseded": superseded,  # D36: smaller-timeframe patterns replaced by a larger timeframe (not in results)
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


Fetcher = Callable[[Instrument, str, int, Any], pd.DataFrame]


def _interleave(tasks_by_source: Mapping[str, list]) -> list:
    """Round-robin across sources so each source's own rate limiter works in parallel with the others."""
    out, lists = [], [list(v) for v in tasks_by_source.values()]
    for i in range(max((len(v) for v in lists), default=0)):
        out.extend(v[i] for v in lists if i < len(v))
    return out


def fetch_all(instruments: list[Instrument], timeframes: list[str], limit: int, *,
              fetchers: Mapping[str, Fetcher], workers: int = MAX_WORKERS, progress_every: int = 300,
              status: Callable[[], str] | None = None):
    """Fetch every instrument x timeframe with <= `workers` threads; each source has its own fetcher
    (and therefore its own rate limiter).

    Returns (symbol_data, failures, skipped); empty series (e.g. a pair listed this week has no
    closed weekly bar yet) are `skipped`, not failures. Failures record exchange and market."""
    local = threading.local()

    def session():
        if not hasattr(local, "s"):
            local.s = requests.Session()
        return local.s

    def job(inst: Instrument, tf: str):
        return fetchers[inst.source](inst, tf, limit_for(tf, limit), session())

    by_source: dict[str, list] = {}
    for inst in instruments:
        by_source.setdefault(inst.source, []).extend((inst, tf) for tf in timeframes)
    tasks = _interleave(by_source)
    symbol_data: dict[str, dict[str, pd.DataFrame]] = {}
    failures: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    done = 0
    with ThreadPoolExecutor(max_workers=max(1, min(workers, MAX_WORKERS))) as pool:
        futs = {pool.submit(job, inst, tf): (inst, tf) for inst, tf in tasks}
        for fut in as_completed(futs):
            inst, tf = futs[fut]
            sym = inst.symbol
            done += 1
            try:
                df = fut.result()
            except FETCH_ERRORS as exc:
                failures.append({"symbol": sym, "timeframe": tf, "stage": "fetch", "exchange": inst.exchange,
                                 "market": inst.market, "error": _short_error(exc)})
                print(f"[fetch FAIL] {inst.source} {sym} {tf}: {_short_error(exc)}", file=sys.stderr)
            else:
                if df.empty:
                    skipped.append({"symbol": sym, "timeframe": tf, "exchange": inst.exchange,
                                    "market": inst.market, "reason": "no closed klines yet"})
                else:
                    symbol_data.setdefault(sym, {})[tf] = df
            if done % progress_every == 0 or done == len(tasks):
                print(f"[fetch] {done}/{len(tasks)} series, failures={len(failures)}"
                      + (f", {status()}" if status else ""))
    # deterministic order (as_completed is not)
    order = {i.symbol: n for n, i in enumerate(instruments)}
    symbol_data = {s: {tf: symbol_data[s][tf] for tf in timeframes if tf in symbol_data[s]}
                   for s in sorted(symbol_data, key=order.get)}
    key = lambda f: (order.get(f["symbol"], 0), timeframes.index(f["timeframe"]))  # noqa: E731
    return symbol_data, sorted(failures, key=key), sorted(skipped, key=key)


def spot_instruments(symbols: Sequence[str]) -> list[Instrument]:
    return [Instrument(base=s[:-4] if s.endswith("USDT") and len(s) > 4 else s, symbol=s, native=s,
                       exchange="binance", market="spot") for s in symbols]


def make_sources(args, spot_limiter: WeightLimiter) -> dict[str, dict[str, Any]]:
    """key -> {"list": f(session) -> [Instrument], "fetch": f(inst, tf, limit, session) -> DataFrame,
    "limiter": rate limiter or None}. Looked up through module globals so tests can monkeypatch."""
    perp_limiter = WeightLimiter(threshold=data_binance_futures.DEFAULT_WEIGHT_THRESHOLD)
    okx_limiter, gate_limiter = data_okx.new_limiter(), data_gate.new_limiter()
    use_all = args.symbols.strip().lower() == "all"

    def list_spot(session):
        symbols = list_usdt_symbols(limiter=spot_limiter) if use_all else \
            list(dict.fromkeys(s.upper() for s in _csv_list(args.symbols)))
        if args.min_quote_volume > 0:
            vols = fetch_quote_volumes(limiter=spot_limiter)
            before = len(symbols)
            symbols = [s for s in symbols if vols.get(s, 0.0) >= args.min_quote_volume]
            print(f"[symbols] {len(symbols)} of {before} have 24h quote volume >= {args.min_quote_volume:,.0f} USDT")
        return spot_instruments(symbols)

    return {
        "binance-spot": {
            "list": list_spot, "limiter": spot_limiter,
            "fetch": lambda inst, tf, limit, sess: fetch_klines(inst.native, tf, limit, session=sess,
                                                                limiter=spot_limiter)},
        "binance-perp": {
            "list": lambda sess: data_binance_futures.list_instruments(sess, limiter=perp_limiter),
            "limiter": perp_limiter,
            "fetch": lambda inst, tf, limit, sess: data_binance_futures.fetch_klines(
                inst.native, tf, limit, session=sess, limiter=perp_limiter)},
        "okx-spot": {
            "list": lambda sess: data_okx.list_instruments(sess, "spot", limiter=okx_limiter),
            "limiter": okx_limiter,
            "fetch": lambda inst, tf, limit, sess: data_okx.fetch_klines(inst.native, tf, limit, session=sess,
                                                                         limiter=okx_limiter)},
        "okx-perp": {
            "list": lambda sess: data_okx.list_instruments(sess, "perp", limiter=okx_limiter),
            "limiter": okx_limiter,
            "fetch": lambda inst, tf, limit, sess: data_okx.fetch_klines(inst.native, tf, limit, session=sess,
                                                                         limiter=okx_limiter)},
        "gate-perp": {
            "list": lambda sess: data_gate.list_instruments(sess, limiter=gate_limiter),
            "limiter": gate_limiter,
            "fetch": lambda inst, tf, limit, sess: data_gate.fetch_klines(inst.native, tf, limit, session=sess,
                                                                          limiter=gate_limiter)},
    }


def parse_sources(text: str | None) -> list[str]:
    """None -> DEFAULT_SOURCES; "all" -> every source; else a comma list (priority order is fixed)."""
    if text is None or text.strip().lower() in ("", "default"):
        keys = list(DEFAULT_SOURCES)
    elif text.strip().lower() == "all":
        keys = list(ALL_SOURCES)
    else:
        keys = _csv_list(text.lower())
    bad = [k for k in keys if k not in ALL_SOURCES]
    if bad or not keys:
        raise ValueError(f"unknown sources {bad}; choose from {list(ALL_SOURCES)}, 'default' or 'all'")
    return [k for k in ALL_SOURCES if k in keys]


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Scan exchange public klines and write site/data/")
    p.add_argument("--market", choices=MARKETS, default="crypto",
                   help="crypto (default) or tw = Taiwan listed + OTC stocks (D29; written to site/data/tw/)")
    p.add_argument("--tw-include-etf", action="store_true",
                   help="tw: also scan ETFs (00xx codes); default is common stocks only")
    p.add_argument("--tw-rps", type=float, default=None,
                   help="tw: Yahoo requests per second over all workers (default 5)")
    p.add_argument("--sources", default=None,
                   help="comma list of %s; 'default' = %s, 'all' = every source (binance-perp is blocked "
                        "from US IPs). One source per base coin, by that priority." %
                        (",".join(ALL_SOURCES), ",".join(DEFAULT_SOURCES)))
    p.add_argument("--symbols", default="all",
                   help='"all" = every instrument of the chosen sources (default); a comma list of Binance spot '
                        'symbols (debug) restricts the scan to those, binance-spot only; tw: stock codes, e.g. 2330,6488')
    p.add_argument("--timeframes", default="4h,1d,1w", help="comma separated subset of 4h,1d,1w")
    p.add_argument("--limit", type=int, default=1000,
                   help="closed klines per symbol/timeframe (1w is capped at %d)" % LIMIT_CAP_BY_TIMEFRAME["1w"])
    p.add_argument("--min-quote-volume", type=float, default=0.0,
                   help="binance-spot only: skip symbols whose 24h quote volume (USDT) is below this; 0 = no filter")
    p.add_argument("--workers", type=int, default=MAX_WORKERS, help=f"fetch threads (max {MAX_WORKERS})")
    p.add_argument("--weight-threshold", type=int, default=4500,
                   help="binance-spot: pause until the next minute when used weight/min would exceed this "
                        "(IP limit 6000)")
    p.add_argument("--include-stale", action="store_true",
                   help="also include stale patterns (for local preview; production leaves this off)")
    p.add_argument("--out", default=None,
                   help="output JSON path (default site/data/scan.json; --market tw: site/data/tw/scan.json)")
    p.add_argument("--scheduled", action="store_true",
                   help="a cron runs this every 4h: publish next_scan_at (automatic when GITHUB_EVENT_NAME=schedule)")
    args = p.parse_args(argv)
    if args.out is None:
        args.out = str(DEFAULT_OUT_TW if args.market == "tw" else DEFAULT_OUT)
    return args


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass
    t0 = time.perf_counter()
    args = parse_args(argv)
    if args.market == "tw":  # D29: separate pipeline (stock list + Yahoo), same detector and payload
        import build_site_tw
        return build_site_tw.main_tw(args, t0)
    timeframes = _csv_list(args.timeframes)
    bad = [tf for tf in timeframes if tf not in TIMEFRAMES]
    if not timeframes or bad or args.limit < 1:
        print(f"error: need timeframes from {TIMEFRAMES} and limit >= 1; bad timeframes: {bad}", file=sys.stderr)
        return 2
    use_all = args.symbols.strip().lower() == "all"
    try:
        source_keys = parse_sources(args.sources) if use_all else ["binance-spot"]
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    spot_limiter = WeightLimiter(threshold=args.weight_threshold)
    sources = make_sources(args, spot_limiter)

    # 1) list each source's instruments; only binance-spot (the primary source, D3) is fatal.
    list_session = requests.Session()
    by_source: dict[str, list[Instrument]] = {}
    source_errors: list[dict[str, Any]] = []
    for key in source_keys:
        try:
            by_source[key] = sources[key]["list"](list_session)
        except FETCH_ERRORS as exc:
            if key == "binance-spot":
                print(f"error: could not get the symbol list: {_short_error(exc)}; {args.out} was NOT updated",
                      file=sys.stderr)
                return 1
            source_errors.append({"source": key, "stage": "list", "error": _short_error(exc)})
            print(f"[source FAIL] {key}: could not list instruments: {_short_error(exc)}", file=sys.stderr)
    merged = merge_instruments(by_source)
    if not merged:
        print("error: no symbols to scan", file=sys.stderr)
        return 2
    source_listed = {k: len(v) for k, v in by_source.items()}
    kept = {k: sum(1 for i in merged if i.source == k) for k in by_source}
    print("[sources] " + ", ".join(f"{k}: {source_listed[k]} listed -> {kept[k]} scanned" for k in by_source)
          + f"; {len(merged)} unique coins")
    symbols = [i.symbol for i in merged]
    instruments = {i.symbol: i for i in merged}

    fetchers = {k: sources[k]["fetch"] for k in by_source}
    symbol_data, failures, skipped = fetch_all(
        merged, timeframes, args.limit, fetchers=fetchers, workers=args.workers,
        status=lambda: f"binance max weight/min={spot_limiter.max_seen}")
    t_fetch = time.perf_counter() - t0

    cfg = merge_config({"include_stale": bool(args.include_stale)})
    scheduled = bool(args.scheduled) or os.environ.get("GITHUB_EVENT_NAME") == "schedule"
    payload, charts = build_payload(symbol_data, cfg, symbols=symbols, timeframes=timeframes, limit=args.limit,
                                    scheduled=scheduled, failures=failures, skipped=skipped,
                                    symbols_source="all" if use_all else "list", instruments=instruments,
                                    sources=source_keys, source_listed=source_listed,
                                    source_errors=source_errors)
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
    for key, c in payload["source_counts"].items():
        print(f"  {key}: {c['symbols']} symbols, {c['series_scanned']} series scanned, "
              f"{c['failed_series']} failed series")
    print(f"max X-MBX-USED-WEIGHT-1M seen: {spot_limiter.max_seen}; throttle waits: {spot_limiter.throttle_waits} "
          f"({spot_limiter.slept:.1f}s)")
    print(f"wrote {args.out} ({sizes['scan_json'] / 1024:.0f} KB) + {sizes['bars_files']} bars files "
          f"({sizes['bars'] / 1024:.0f} KB): {len(payload['results'])} patterns {counts}; "
          f"next scan ~{payload['next_scan_at'] or 'not scheduled'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
