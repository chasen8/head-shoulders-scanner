"""CLI: fetch Binance public klines and scan for inverse head-and-shoulders.

Example:
    venv/Scripts/python.exe scan.py --symbols BTCUSDT,ETHUSDT --timeframes 4h,1d,1w --mode candidate
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from ihs_scanner.config import DEFAULT_PROFILE, PROFILES, RULE_OFF_OVERRIDES, TIMEFRAMES, merge_config, rules_off_overrides
from ihs_scanner.data_binance import DEFAULT_CACHE_DIR, BinanceFetchError, fetch_klines
from ihs_scanner.scanner import export_results, scan_symbols

DEFAULT_SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT",
    "DOGEUSDT", "ADAUSDT", "AVAXUSDT", "LINKUSDT", "DOTUSDT",
]
DEFAULT_OUTPUT_DIR = Path(r"C:\trading-data\ihs-scanner-v2\output")


def _csv_list(text: str) -> list[str]:
    return [s.strip() for s in text.split(",") if s.strip()]


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Inverse Head and Shoulders scanner (Binance public klines)")
    p.add_argument("--symbols", default=",".join(DEFAULT_SYMBOLS), help="comma separated, e.g. BTCUSDT,ETHUSDT")
    p.add_argument("--timeframes", default="4h,1d,1w", help="comma separated subset of 4h,1d,1w")
    p.add_argument("--profile", choices=list(PROFILES), default=DEFAULT_PROFILE,
                   help="rule set (D22): template = calibrated on the user's examples, with forming right "
                        "shoulders and TP1/TP2; classic = D19/D21 rules")
    p.add_argument("--mode", choices=["candidate", "breakout"], default="candidate")
    p.add_argument("--volume-filter", action="store_true", help="breakout requires volume_ratio >= min_volume_ratio")
    p.add_argument("--limit", type=int, default=1000, help="closed klines per symbol/timeframe")
    p.add_argument("--min-score", type=float, default=None, help="override min_pattern_score")
    p.add_argument("--include-stale", action="store_true", help="also output stale patterns")
    p.add_argument("--output", default=None, help=".csv or .json (default: output dir, UTC timestamped csv)")
    p.add_argument("--no-cache", action="store_true", help="do not write the kline CSV cache")
    p.add_argument("--no-balance", action="store_true", help="disable the shoulder balance rule (D19)")
    p.add_argument("--symmetry-ratio", type=float, default=None,
                   help="override max_time_symmetry_ratio; <= 0 disables the rule (D19)")
    p.add_argument("--pivot-windows", default=None, help="e.g. 4h=3,1d=3,1w=2 (default 5/5/3)")
    p.add_argument("--rules-off", default="",
                   help="comma separated D21 rule ids to disable, from " + ",".join(RULE_OFF_OVERRIDES)
                   + " (e.g. R1,R3); 'all' disables all five")
    return p.parse_args(argv)


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    args = parse_args(argv)
    symbols = [s.upper() for s in _csv_list(args.symbols)]
    timeframes = _csv_list(args.timeframes)
    bad = [tf for tf in timeframes if tf not in TIMEFRAMES]
    if bad:
        print(f"unsupported timeframes: {bad}", file=sys.stderr)
        return 2

    overrides = {
        "profile": args.profile,
        "enable_breakout_filter": args.mode == "breakout",
        "enable_volume_filter": bool(args.volume_filter),
        "include_stale": bool(args.include_stale),
    }
    if args.min_score is not None:
        overrides["min_pattern_score"] = args.min_score
    if args.no_balance:
        overrides["enable_balance_rule"] = False
    if args.symmetry_ratio is not None:
        overrides["max_time_symmetry_ratio"] = args.symmetry_ratio if args.symmetry_ratio > 0 else None
    if args.pivot_windows:
        try:
            overrides["pivot_window_by_timeframe"] = {
                k.strip(): int(v) for k, v in (item.split("=") for item in _csv_list(args.pivot_windows))}
        except ValueError:
            print(f"bad --pivot-windows: {args.pivot_windows!r}", file=sys.stderr)
            return 2
    if args.rules_off:
        ids = list(RULE_OFF_OVERRIDES) if args.rules_off.strip().lower() == "all" else _csv_list(args.rules_off)
        try:
            overrides.update(rules_off_overrides(ids))
        except KeyError as exc:
            print(f"bad --rules-off: {exc}", file=sys.stderr)
            return 2
    cfg = merge_config(overrides)

    cache_dir = None if args.no_cache else DEFAULT_CACHE_DIR
    symbol_data: dict = {}
    failures = []
    for sym in symbols:
        for tf in timeframes:
            t0 = time.perf_counter()
            try:
                df = fetch_klines(sym, tf, args.limit, cache_dir=cache_dir)
            except BinanceFetchError as exc:
                failures.append((sym, tf, str(exc)))
                print(f"[fetch FAIL] {sym} {tf}: {exc}", file=sys.stderr)
                continue
            symbol_data.setdefault(sym, {})[tf] = df
            last = df["timestamp"].iloc[-1] if len(df) else None
            print(f"[fetch] {sym:<10} {tf:<3} bars={len(df):<5} last_closed={last} ({time.perf_counter() - t0:.2f}s)")

    timings: dict = {}
    results = scan_symbols(symbol_data, cfg, timings=timings)
    for (sym, tf), (nbars, sec) in timings.items():
        print(f"[detect] {sym:<10} {tf:<3} bars={nbars:<5} {sec * 1000:.1f} ms")

    if args.output:
        out_path = Path(args.output)
    else:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        out_path = DEFAULT_OUTPUT_DIR / f"results_{stamp}.csv"
    export_results(results, out_path)

    print(f"\nprofile={args.profile} mode={args.mode} volume_filter={args.volume_filter} "
          f"patterns={len(results)} -> {out_path}")
    header = (f"{'#':>2} {'symbol':<10} {'tf':<3} {'score':>6} {'status':<9} {'brk':<5} {'dist':>7} {'vol_r':>6}"
              f" {'tp1':>10} {'tp2':>10}  RS_time")
    print(header)
    for i, r in enumerate(results[:20], 1):
        vr = "" if r["volume_ratio"] is None else f"{r['volume_ratio']:.2f}"
        print(f"{i:>2} {r['symbol']:<10} {r['timeframe']:<3} {r['pattern_score']:>6.2f} {r['status']:<9} "
              f"{str(r['breakout_detected']):<5} {r['distance_to_neckline']:>7.4f} {vr:>6}"
              f" {r['tp1']:>10.4g} {r['tp2']:>10.4g}  {r['RS_time']}")
    if failures:
        print(f"\n{len(failures)} fetch failures (see stderr)")
        return 1 if not symbol_data else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
