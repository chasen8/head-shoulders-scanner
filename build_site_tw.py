"""Taiwan-stock half of build_site.py (charter D29): `build_site.py --market tw` runs main_tw().

Stock list: TWSE + TPEx OpenAPI (common stocks; --tw-include-etf adds ETFs). Klines: Yahoo Finance chart
API, two requests per stock (1d -> 1d + 1w, 60m -> 4h), see ihs_scanner/data_tw.py. The detector,
the payload format and the partial-success rules are the crypto ones (build_site.build_payload, D18):
more than MAX_FAILURE_RATIO failed series -> exit 1 and the previous site/data/tw/ stays untouched.

Output: site/data/tw/scan.json + site/data/tw/bars/ (never touches the crypto site/data/scan.json).
Schedule (.github/workflows/scan-tw.yml): weekdays 06:10 UTC = 14:10 Taipei, 40 minutes after the close.

Example (Git Bash, project root):
    /c/trading-data/ihs-scanner-v2/venv/Scripts/python.exe build_site.py --market tw
    /c/trading-data/ihs-scanner-v2/venv/Scripts/python.exe build_site.py --market tw --symbols 2330,6488 --include-stale
"""
from __future__ import annotations

import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Sequence

import requests

import build_site as bs
from ihs_scanner import data_tw
from ihs_scanner.config import TIMEFRAMES, market_config
from ihs_scanner.sources import Instrument, SourceFetchError

# Must match .github/workflows/scan-tw.yml: cron "10 6 * * 1-5"
SCAN_HOUR_UTC = 6
SCAN_MINUTE_UTC = 10
TW_EXCHANGES = ("twse", "tpex")


def next_tw_scan_time(now: datetime) -> datetime:
    """Next weekday 06:10 UTC after `now` (Taiwan holidays still run; they just add no new bar)."""
    now = now.astimezone(timezone.utc)
    slot = now.replace(hour=SCAN_HOUR_UTC, minute=SCAN_MINUTE_UTC, second=0, microsecond=0)
    while slot <= now or slot.weekday() >= 5:
        slot += timedelta(days=1)
    return slot


def list_tw_instruments(session, *, include_etf: bool, lister: Callable = data_tw.list_instruments
                        ) -> tuple[list[Instrument], dict[str, int], list[dict[str, Any]]]:
    """Both exchanges; one failing exchange is recorded in source_errors (the other is still scanned)."""
    out: list[Instrument] = []
    listed: dict[str, int] = {}
    errors: list[dict[str, Any]] = []
    for ex in TW_EXCHANGES:
        try:
            insts = lister(session, ex, include_etf=include_etf)
        except (SourceFetchError, ValueError, requests.RequestException) as exc:
            errors.append({"source": f"{ex}-stock", "stage": "list", "error": bs._short_error(exc)})
            print(f"[source FAIL] {ex}: could not list stocks: {bs._short_error(exc)}", file=sys.stderr)
            continue
        listed[f"{ex}-stock"] = len(insts)
        out.extend(insts)
    seen: set[str] = set()
    uniq = []
    for i in out:  # a code is unique across the two exchanges; keep the first just in case
        if i.symbol not in seen:
            seen.add(i.symbol)
            uniq.append(i)
    return uniq, listed, errors


def fetch_all_tw(instruments: Sequence[Instrument], timeframes: Sequence[str], limit: int,
                 client: data_tw.YahooClient, *, workers: int = bs.MAX_WORKERS, progress_every: int = 200,
                 fetch: Callable = data_tw.fetch_series):
    """Every stock with <= workers threads (one task = one stock = up to two Yahoo requests).
    Returns (symbol_data, failures, skipped) in the same format as build_site.fetch_all."""
    symbol_data: dict[str, dict[str, Any]] = {}
    failures: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    lock = threading.Lock()
    done = 0
    t0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=max(1, min(workers, bs.MAX_WORKERS))) as pool:
        futs = {pool.submit(fetch, inst, list(timeframes), limit, client): inst for inst in instruments}
        for fut in as_completed(futs):
            inst = futs[fut]
            try:
                frames, errors = fut.result()
            except Exception as exc:  # unexpected: count every timeframe as failed
                frames, errors = {}, {tf: f"{type(exc).__name__}: {exc}" for tf in timeframes}
            with lock:
                done += 1
                for tf, err in errors.items():
                    failures.append({"symbol": inst.symbol, "timeframe": tf, "stage": "fetch",
                                     "exchange": inst.exchange, "market": inst.market,
                                     "error": bs._short_error(err)})
                for tf, df in frames.items():
                    if df.empty:
                        skipped.append({"symbol": inst.symbol, "timeframe": tf, "exchange": inst.exchange,
                                        "market": inst.market, "reason": "no closed bars"})
                    else:
                        symbol_data.setdefault(inst.symbol, {})[tf] = df
                if done % progress_every == 0 or done == len(instruments):
                    print(f"[fetch] {done}/{len(instruments)} stocks, failed series={len(failures)}, "
                          f"yahoo requests={client.requests} (429: {client.rate_limited}), "
                          f"{time.perf_counter() - t0:.0f}s")
    order = {i.symbol: n for n, i in enumerate(instruments)}
    symbol_data = {s: {tf: symbol_data[s][tf] for tf in timeframes if tf in symbol_data[s]}
                   for s in sorted(symbol_data, key=order.get)}
    key = lambda f: (order.get(f["symbol"], 0), list(timeframes).index(f["timeframe"]))  # noqa: E731
    return symbol_data, sorted(failures, key=key), sorted(skipped, key=key)


def main_tw(args, t0: float | None = None, *, client: data_tw.YahooClient | None = None,
            lister: Callable = data_tw.list_instruments, fetch: Callable = data_tw.fetch_series) -> int:
    t0 = time.perf_counter() if t0 is None else t0
    timeframes = bs._csv_list(args.timeframes)
    bad = [tf for tf in timeframes if tf not in TIMEFRAMES]
    if not timeframes or bad or args.limit < 1:
        print(f"error: need timeframes from {TIMEFRAMES} and limit >= 1; bad timeframes: {bad}", file=sys.stderr)
        return 2
    out = Path(args.out)
    insts, listed, source_errors = list_tw_instruments(requests.Session(), include_etf=bool(args.tw_include_etf),
                                                       lister=lister)
    if not insts:
        print(f"error: could not get any Taiwan stock list; {out} was NOT updated", file=sys.stderr)
        return 1
    use_all = args.symbols.strip().lower() == "all"
    if not use_all:
        wanted = [s.strip().upper() for s in bs._csv_list(args.symbols)]
        by_code = {i.symbol: i for i in insts}
        missing = [w for w in wanted if w not in by_code]
        if missing:
            print(f"[symbols] not in the stock list (ignored): {missing}", file=sys.stderr)
        insts = [by_code[w] for w in dict.fromkeys(wanted) if w in by_code]
        if not insts:
            print("error: no symbols to scan", file=sys.stderr)
            return 2
    print(f"[symbols] scanning {len(insts)} stocks (" +
          ", ".join(f"{k}: {sum(1 for i in insts if i.exchange == k)}" for k in TW_EXCHANGES) + ")")

    if client is None:
        rps = args.tw_rps or data_tw.REQUESTS_PER_SECOND
        client = data_tw.YahooClient(limiter=data_tw.new_limiter(rps))
        ok = client.refresh_crumb(min_age=0)
        print(f"[yahoo] crumb {'ok' if ok else 'NOT available (requests may be rate limited)'}; pacing {rps:g} req/s")
    symbol_data, failures, skipped = fetch_all_tw(insts, timeframes, args.limit, client,
                                                  workers=args.workers, fetch=fetch)
    t_fetch = time.perf_counter() - t0

    cfg = market_config("tw", {"include_stale": bool(args.include_stale)})  # D33: TW 4h max 300 bars
    scheduled = bool(args.scheduled) or os.environ.get("GITHUB_EVENT_NAME") == "schedule"
    universe = "common+etf" if args.tw_include_etf else "common"
    payload, charts = bs.build_payload(
        symbol_data, cfg, symbols=[i.symbol for i in insts], timeframes=timeframes, limit=args.limit,
        scheduled=scheduled, failures=failures, skipped=skipped, symbols_source="all" if use_all else "list",
        instruments={i.symbol: i for i in insts}, sources=[f"{ex}-stock" for ex in TW_EXCHANGES],
        source_listed=listed, source_errors=source_errors, market="tw", next_scan=next_tw_scan_time,
        data_source="Yahoo Finance chart API (1d; 1w from 1d; 4h from 60m in 09:00-13:00 / 13:00-13:30 "
                    "Asia/Taipei sessions), stock list from TWSE / TPEx OpenAPI; closed bars only",
        extra_settings={"universe": universe, "currency": "TWD", "timezone": data_tw.TZ})
    total = payload["series_total"]
    failed_series = {(f["symbol"], f["timeframe"]) for f in payload["failures"]}
    ratio = len(failed_series) / total if total else 1.0
    if ratio > bs.MAX_FAILURE_RATIO:
        print(f"error: {len(failed_series)} of {total} series failed ({ratio:.0%} > {bs.MAX_FAILURE_RATIO:.0%}); "
              f"{out} was NOT updated", file=sys.stderr)
        return 1

    sizes = bs.write_site(payload, charts, out)
    counts: dict[str, int] = {}
    by_tf: dict[str, dict[str, int]] = {}
    for r in payload["results"]:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
        by_tf.setdefault(r["timeframe"], {}).setdefault(r["status"], 0)
        by_tf[r["timeframe"]][r["status"]] += 1
    elapsed = time.perf_counter() - t0
    print(f"\nscanned {payload['scanned_symbols']} stocks / {payload['series_scanned']} of {total} series "
          f"in {elapsed:.1f}s (fetch {t_fetch:.1f}s); failures={len(payload['failures'])} "
          f"skipped={len(payload['skipped'])}; yahoo requests={client.requests} (429: {client.rate_limited})")
    for tf in timeframes:
        print(f"  {tf}: {by_tf.get(tf, {})}")
    print(f"wrote {out} ({sizes['scan_json'] / 1024:.0f} KB) + {sizes['bars_files']} bars files "
          f"({sizes['bars'] / 1024:.0f} KB): {len(payload['results'])} patterns {counts}; "
          f"next scan ~{payload['next_scan_at'] or 'not scheduled'}")
    return 0
