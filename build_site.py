"""Build the static site's data file: fetch Binance public klines, scan, write site/data/scan.json.

The front end only draws what is in this JSON; every number it shows (scores, neckline
end points, which side of the neckline price is on, next scan time) is computed here.

Example (Git Bash, project root):
    /c/trading-data/ihs-scanner-v2/venv/Scripts/python.exe build_site.py --include-stale
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

import pandas as pd

from ihs_scanner.config import TIMEFRAMES, merge_config
from ihs_scanner.data_binance import BinanceFetchError, fetch_klines
from ihs_scanner.neckline import calculate_neckline
from ihs_scanner.scanner import OUTPUT_FIELDS, _to_jsonable, scan_symbols
from ihs_scanner.swings import get_timestamps

DEFAULT_SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT",
    "DOGEUSDT", "ADAUSDT", "AVAXUSDT", "LINKUSDT", "DOTUSDT",
]  # same list as scan.py
DEFAULT_OUT = Path(__file__).resolve().parent / "site" / "data" / "scan.json"
BARS_BEFORE_LS = 15  # chart context to the left of the left shoulder
# Must match .github/workflows/scan-and-deploy.yml: cron "7 */4 * * *"
SCAN_EVERY_HOURS = 4
SCAN_MINUTE = 7
SCHEMA_VERSION = 1


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


def next_scan_time(now: datetime) -> datetime:
    """First cron slot (minute SCAN_MINUTE of every SCAN_EVERY_HOURS-th UTC hour) after `now`."""
    now = now.astimezone(timezone.utc)
    slot_hour = now.hour - now.hour % SCAN_EVERY_HOURS
    slot = now.replace(hour=slot_hour, minute=SCAN_MINUTE, second=0, microsecond=0)
    while slot <= now:
        slot += timedelta(hours=SCAN_EVERY_HOURS)
    return slot


def chart_for(result: Mapping[str, Any], df: pd.DataFrame, angle_scale: float) -> dict[str, Any]:
    """Everything the chart needs, in UNIX seconds (UTC).

    bars: [[time, open, high, low, close, volume], ...] from BARS_BEFORE_LS bars before LS
    through the last closed bar. neckline: the N1-N2 line evaluated (neckline_price_at) at
    the first bar of the LS and at the last bar, so the front end draws a straight segment.
    """
    ts = get_timestamps(df)
    pos = {name: int(ts.get_loc(pd.Timestamp(result[f"{name}_time"])))
           for name in ("LS", "N1", "H", "N2", "RS")}
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
              for name in ("LS", "N1", "H", "N2", "RS")]
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


def _neckline_side(r: Mapping[str, Any]) -> str:
    close, neck = float(r["current_close"]), float(r["current_neckline_price"])
    return "above" if close > neck else ("below" if close < neck else "at")


def build_payload(symbol_data: Mapping[str, Mapping[str, pd.DataFrame]], cfg: Mapping[str, Any], *,
                  symbols: list[str], timeframes: list[str], limit: int,
                  generated_at: datetime | None = None, scheduled: bool = False) -> dict[str, Any]:
    """scheduled=False -> next_scan_at is null (no cron is running; the page says updates are manual)."""
    generated_at = generated_at or datetime.now(timezone.utc)
    results = scan_symbols(symbol_data, cfg)
    angle_scale = float(cfg["angle_scale"])
    rows = []
    for rank, r in enumerate(results, 1):
        keys = OUTPUT_FIELDS + [k for k in r if k not in OUTPUT_FIELDS]
        row = {k: _to_jsonable(r.get(k)) for k in keys}
        row["rank"] = rank
        row["neckline_side"] = _neckline_side(r)
        row["chart"] = chart_for(r, symbol_data[r["symbol"]][r["timeframe"]], angle_scale)
        rows.append(row)

    last_closed = {
        sym: {tf: (_iso(get_timestamps(df)[-1]) if len(df) else None) for tf, df in by_tf.items()}
        for sym, by_tf in symbol_data.items()
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "next_scan_at": next_scan_time(generated_at).strftime("%Y-%m-%dT%H:%M:%SZ") if scheduled else None,
        "settings": {
            "symbols": symbols,
            "timeframes": timeframes,
            "mode": "breakout" if cfg["enable_breakout_filter"] else "candidate",
            "include_stale": bool(cfg["include_stale"]),
            "volume_filter": bool(cfg["enable_volume_filter"]),
            "min_pattern_score": float(cfg["min_pattern_score"]),
            "bars_per_series": limit,
            "data_source": "Binance public klines (data-api.binance.vision), closed bars only",
        },
        "last_closed": last_closed,
        "results": rows,
    }


def write_payload(payload: Mapping[str, Any], out: Path) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(out.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    tmp.replace(out)
    return out


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Scan Binance public klines and write site/data/scan.json")
    p.add_argument("--symbols", default=",".join(DEFAULT_SYMBOLS), help="comma separated, e.g. BTCUSDT,ETHUSDT")
    p.add_argument("--timeframes", default="4h,1d,1w", help="comma separated subset of 4h,1d,1w")
    p.add_argument("--limit", type=int, default=1000, help="closed klines per symbol/timeframe")
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
    args = parse_args(argv)
    symbols = [s.upper() for s in _csv_list(args.symbols)]
    timeframes = _csv_list(args.timeframes)
    bad = [tf for tf in timeframes if tf not in TIMEFRAMES]
    if not symbols or not timeframes or bad:
        print(f"error: need symbols and timeframes from {TIMEFRAMES}; bad timeframes: {bad}", file=sys.stderr)
        return 2

    cfg = merge_config({"include_stale": bool(args.include_stale)})
    symbol_data: dict[str, dict[str, pd.DataFrame]] = {}
    failures = []
    for sym in symbols:
        for tf in timeframes:
            try:
                df = fetch_klines(sym, tf, args.limit)
            except (BinanceFetchError, ValueError) as exc:
                failures.append(f"{sym} {tf}: {exc}")
                print(f"[fetch FAIL] {sym} {tf}: {exc}", file=sys.stderr)
                continue
            if df.empty:
                failures.append(f"{sym} {tf}: no closed klines returned")
                print(f"[fetch FAIL] {sym} {tf}: no closed klines returned", file=sys.stderr)
                continue
            symbol_data.setdefault(sym, {})[tf] = df
            print(f"[fetch] {sym:<10} {tf:<3} bars={len(df):<5} last_closed={df['timestamp'].iloc[-1]}")

    if failures:
        print(f"error: {len(failures)} of {len(symbols) * len(timeframes)} fetches failed; "
              f"{args.out} was NOT updated", file=sys.stderr)
        return 1

    scheduled = bool(args.scheduled) or os.environ.get("GITHUB_EVENT_NAME") == "schedule"
    payload = build_payload(symbol_data, cfg, symbols=symbols, timeframes=timeframes, limit=args.limit,
                            scheduled=scheduled)
    out = write_payload(payload, Path(args.out))
    counts: dict[str, int] = {}
    for r in payload["results"]:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    size_kb = out.stat().st_size / 1024
    print(f"\nwrote {out} ({size_kb:.0f} KB): {len(payload['results'])} patterns {counts}; "
          f"next scan ~{payload['next_scan_at'] or 'not scheduled'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
