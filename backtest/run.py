"""Backtest CLI (charter D40).

  python -m backtest.run fetch  --market crypto|tw [--max-symbols N] [--workers 8]
  python -m backtest.run run    --market crypto|tw [--families ihs,tri] [--timeframes 4h,1d,1w]
                                [--step 4h=5,1d=5,1w=3 | --step-mult 2] [--max-symbols N] [--workers K] [--tag main]
  python -m backtest.run report [--tag main]

fetch: lists the universe (saved to data/<market>/instruments.csv) and caches every missing series.
run:   one task per symbol (multiprocessing, default cores - 1); each finished symbol writes
       results/<tag>/<market>/{trades,skips,signals}/<source>_<symbol>.csv and done/<source>_<symbol>.json, so an
       interrupted run continues where it stopped. The log (results/<tag>/run_<market>.log) has the timing.
report: backtest/report.py.
"""
from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pandas as pd
import requests

from backtest import data as D
from backtest import engine as E

FETCH_TFS = ("4h", "1d", "1w")


def _log(path: Path, text: str) -> None:
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {text}"
    print(line, flush=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")


# ------------------------------------------------------------------ fetch

def cmd_fetch(args) -> int:
    log = D.DATA_DIR / f"fetch_{args.market}.log"
    session = requests.Session()
    if args.market == "crypto":
        insts, errors = D.list_crypto(session)
        for k, v in errors.items():
            _log(log, f"[list FAIL] {k}: {v}")
    else:
        insts = D.list_tw(session)
    D.save_instruments(args.market, insts)
    if args.max_symbols:
        insts = insts[: args.max_symbols]
    _log(log, f"[fetch] {args.market}: {len(insts)} instruments")
    local = threading.local()

    def sess():
        if not hasattr(local, "s"):
            local.s = requests.Session()
        return local.s

    failures = []
    t0 = time.time()
    if args.market == "crypto":
        fetcher = D.CryptoFetcher()
        tasks = [(i, tf) for i in insts for tf in FETCH_TFS
                 if not D.cache_file("crypto", D.source_of(i), i.symbol, tf).exists()]

        def job(inst, tf):
            df = fetcher.fetch(inst, tf, sess())
            D.save_frame(df, D.cache_file("crypto", D.source_of(inst), inst.symbol, tf))
            return len(df)
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futs = {pool.submit(job, i, tf): (i, tf) for i, tf in tasks}
            for k, fut in enumerate(as_completed(futs), 1):
                inst, tf = futs[fut]
                try:
                    fut.result()
                except Exception as exc:
                    failures.append((inst.symbol, tf, repr(exc)[:200]))
                    _log(log, f"[fetch FAIL] {inst.source} {inst.symbol} {tf}: {repr(exc)[:200]}")
                if k % 300 == 0 or k == len(tasks):
                    _log(log, f"[fetch] {k}/{len(tasks)} series, failures={len(failures)}, {time.time() - t0:.0f}s")
    else:
        from ihs_scanner import data_tw
        client = data_tw.YahooClient()
        client.refresh_crumb()
        tasks = [i for i in insts if not all(D.cache_file("tw", D.source_of(i), i.symbol, tf).exists()
                                             for tf in FETCH_TFS)]

        def job(inst):
            frames, errors = D.fetch_tw_long(inst, client)
            for tf, df in frames.items():
                D.save_frame(df, D.cache_file("tw", D.source_of(inst), inst.symbol, tf))
            return errors
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futs = {pool.submit(job, i): i for i in tasks}
            for k, fut in enumerate(as_completed(futs), 1):
                inst = futs[fut]
                try:
                    errs = fut.result()
                except Exception as exc:
                    errs = {"all": repr(exc)[:200]}
                for tf, e in errs.items():
                    failures.append((inst.symbol, tf, e))
                    _log(log, f"[fetch FAIL] {inst.symbol} {tf}: {e[:200]}")
                if k % 200 == 0 or k == len(tasks):
                    _log(log, f"[fetch] {k}/{len(tasks)} stocks, failures={len(failures)}, "
                              f"yahoo requests={client.requests} (429: {client.rate_limited}), {time.time() - t0:.0f}s")
    _log(log, f"[fetch] done in {time.time() - t0:.0f}s; failures={len(failures)}")
    return 0


# ------------------------------------------------------------------ run

def _out_dir(tag: str, market: str) -> Path:
    return D.RESULTS_DIR / tag / market


def _worker(task: tuple) -> dict:
    """One symbol: load cached frames, run every family x timeframe, write the result files."""
    inst_d, market, tag, families, timeframes, steps = task
    from ihs_scanner.sources import Instrument
    inst = Instrument(**inst_d)
    src = D.source_of(inst)
    key = f"{src}_{inst.symbol}"
    out = _out_dir(tag, market)
    t0 = time.perf_counter()
    frames, bars = {}, {}
    for tf in timeframes:
        p = D.cache_file(market, src, inst.symbol, tf)
        if p.exists():
            df = D.load_frame(p)
            if len(df):
                frames[tf] = df
                bars[tf] = {"bars": len(df), "first": str(df["timestamp"].iloc[0]),
                            "last": str(df["timestamp"].iloc[-1])}
    meta = {"source": src, "exchange": inst.exchange, "name": inst.name}
    try:
        res = E.run_symbol(frames, market, symbol=inst.symbol, meta=meta, families=families,
                           timeframes=timeframes, steps=steps)
        error = None
    except Exception as exc:  # recorded, the run continues
        import traceback
        res, error = {"trades": [], "skips": [], "signals": []}, traceback.format_exc()[-1500:]
    for name in ("trades", "skips", "signals"):
        d = out / name
        d.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(res[name]).to_csv(d / f"{key}.csv", index=False, encoding="utf-8")
    info = {"key": key, "symbol": inst.symbol, "source": src, "bars": bars, "seconds": time.perf_counter() - t0,
            "trades": len(res["trades"]), "signals": len(res["signals"]), "error": error,
            "eval_errors": len(res.get("eval_errors", [])),
            "eval_error_samples": [f"{e['family']} {e['timeframe']} t={e['t']}: {e['error']}"
                                   for e in res.get("eval_errors", [])[:3]]}
    (out / "done").mkdir(parents=True, exist_ok=True)
    with open(out / "done" / f"{key}.json", "w", encoding="utf-8") as fh:
        json.dump(info, fh, ensure_ascii=False)
    return info


def parse_steps(text: str | None, mult: int) -> dict[str, int]:
    steps = {tf: E.default_step(tf) * mult for tf in E.TIMEFRAMES}
    for part in (text or "").split(","):
        if "=" in part:
            k, v = part.split("=", 1)
            steps[k.strip()] = int(v)
    return steps


def cmd_run(args) -> int:
    market = args.market
    out = _out_dir(args.tag, market)
    log = D.RESULTS_DIR / args.tag / f"run_{market}.log"
    insts = D.load_instruments(market)
    if args.symbols:
        want = {s.strip().upper() for s in args.symbols.split(",")}
        insts = [i for i in insts if i.symbol.upper() in want]
    if args.max_symbols:
        insts = insts[: args.max_symbols]
    families = [f.strip() for f in args.families.split(",") if f.strip()]
    timeframes = [t.strip() for t in args.timeframes.split(",") if t.strip()]
    steps = parse_steps(args.step, args.step_mult)
    done_dir = out / "done"
    todo = [i for i in insts if not (done_dir / f"{D.source_of(i)}_{i.symbol}.json").exists()]
    workers = args.workers or max(1, (os.cpu_count() or 2) - 1)
    _log(log, f"[run] {market} tag={args.tag} families={families} tfs={timeframes} steps={steps} "
              f"symbols={len(insts)} todo={len(todo)} workers={workers}")
    tasks = [({"base": i.base, "symbol": i.symbol, "native": i.native, "exchange": i.exchange, "market": i.market,
               "name": i.name}, market, args.tag, families, timeframes, steps) for i in todo]
    t0 = time.time()
    n_tr = n_err = 0
    secs = []
    with mp.Pool(workers) as pool:
        for k, info in enumerate(pool.imap_unordered(_worker, tasks), 1):
            n_tr += info["trades"]
            secs.append(info["seconds"])
            if info["error"]:
                n_err += 1
                _log(log, f"[run ERROR] {info['key']}: {info['error'].splitlines()[-1]}")
            if k % 25 == 0 or k == len(tasks):
                el = time.time() - t0
                eta = el / k * (len(tasks) - k)
                _log(log, f"[run] {k}/{len(tasks)} symbols, trades={n_tr}, errors={n_err}, elapsed {el:.0f}s, "
                          f"mean {sum(secs) / len(secs):.1f}s/symbol/worker, eta {eta:.0f}s")
    el = time.time() - t0
    _log(log, f"[run] done {len(tasks)} symbols in {el:.0f}s ({el / max(1, len(tasks)):.2f}s wall/symbol); "
              f"trades={n_tr}, errors={n_err}")
    if args.max_symbols and len(insts) < len(D.load_instruments(market)):
        total = len(D.load_instruments(market))
        _log(log, f"[estimate] full {market} universe {total} symbols at this rate: "
                  f"{el / max(1, len(tasks)) * total / 3600:.2f} h by wall time per symbol, "
                  f"{sum(secs) / max(1, len(secs)) * total / workers / 3600:.2f} h by CPU time per symbol / {workers} workers")
    return 0


def cmd_report(args) -> int:
    from backtest import report
    return report.main(args.tag, steps_note=args.steps_note)


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch")
    f.add_argument("--market", choices=("crypto", "tw"), required=True)
    f.add_argument("--max-symbols", type=int, default=0)
    f.add_argument("--workers", type=int, default=8)
    r = sub.add_parser("run")
    r.add_argument("--market", choices=("crypto", "tw"), required=True)
    r.add_argument("--families", default="ihs,tri")
    r.add_argument("--timeframes", default="4h,1d,1w")
    r.add_argument("--step", default=None, help="e.g. 4h=5,1d=5,1w=3 (default: pivot window)")
    r.add_argument("--step-mult", type=int, default=1)
    r.add_argument("--max-symbols", type=int, default=0)
    r.add_argument("--symbols", default="")
    r.add_argument("--workers", type=int, default=0)
    r.add_argument("--tag", default="main")
    s = sub.add_parser("report")
    s.add_argument("--tag", default="main")
    s.add_argument("--steps-note", default="")
    return p.parse_args(argv)


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    args = parse_args(argv)
    return {"fetch": cmd_fetch, "run": cmd_run, "report": cmd_report}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
