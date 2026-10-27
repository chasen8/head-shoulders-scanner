"""D50 score study runner.

  python -m backtest.score_study_run features [--workers 14] [--limit N]   # rebuild per-signal features (slow, ~1 h)
  python -m backtest.score_study_run analyze                              # tables -> docs/score-study.md input

Inputs: results/followup/trades_annotated.csv (D42, closed trades with the random-bracket control), the kline cache.
Outputs (not in git): C:\\trading-data\\ihs-scanner-v2\\output\\score-study\\{features.csv.gz, tables.md, ...}.
"""
from __future__ import annotations

import os

for _v in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_v, "1")

import argparse
import multiprocessing as mp
import sys
import time

import pandas as pd

from backtest import data as D
from backtest import score_study as S

OUT_DIR = D.ROOT.parent / "output" / "score-study"
TRADES = D.RESULTS_DIR / "followup" / "trades_annotated.csv"
FEATURES = OUT_DIR / "features.csv.gz"
KEY = ["market", "source", "symbol", "timeframe", "pattern_id"]


def load_signals() -> pd.DataFrame:
    tr = pd.read_csv(TRADES, encoding="utf-8", low_memory=False)
    tr = tr[(tr["family"] == "ihs") & (tr["mode"] == "A") & tr["exit"].isin(["TP1", "TP2"])]
    return tr.drop_duplicates(KEY)[KEY + ["t"]].reset_index(drop=True)


def worker(task: tuple) -> list[dict]:
    market, source, symbol, tf, sigs = task
    p = D.cache_file(market, source, symbol, tf)
    if not p.exists():
        return []
    df = D.load_frame(p)
    out = []
    for pid, t in sigs:
        try:
            f = S.signal_features(df, tf, market, symbol, int(t), pid)
        except Exception as exc:  # recorded as a missing row
            f = {"error": f"{type(exc).__name__}: {exc}"}
        if f is None:
            f = {"error": "pattern not in view"}
        out.append({"market": market, "source": source, "symbol": symbol, "timeframe": tf, "pattern_id": pid,
                    "t": int(t), **f})
    return out


def run_features(workers: int, limit: int | None) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    sg = load_signals()
    if limit:
        sg = sg.sample(limit, random_state=0)
    tasks = []
    for (m, s, sym, tf), g in sg.groupby(["market", "source", "symbol", "timeframe"]):
        tasks.append((m, s, sym, tf, list(zip(g["pattern_id"], g["t"]))))
    tasks.sort(key=lambda x: -len(x[4]))
    print(f"{len(sg)} signals, {len(tasks)} series", flush=True)
    rows, t0 = [], time.time()
    with mp.Pool(workers) as pool:
        for i, r in enumerate(pool.imap_unordered(worker, tasks), 1):
            rows.extend(r)
            if i % 50 == 0:
                print(f"{i}/{len(tasks)} series, {len(rows)} signals, {time.time() - t0:.0f}s", flush=True)
    df = pd.DataFrame(rows)
    df.to_csv(FEATURES, index=False, encoding="utf-8")
    print(f"wrote {FEATURES} rows={len(df)} errors={df['error'].notna().sum() if 'error' in df else 0}", flush=True)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("stage", choices=["features", "analyze"])
    ap.add_argument("--crypto-split", choices=["spec", "median"], default="spec")
    ap.add_argument("--workers", type=int, default=14)
    ap.add_argument("--limit", type=int, default=None)
    a = ap.parse_args(argv)
    if a.stage == "features":
        run_features(a.workers, a.limit)
    else:
        from backtest import score_study_report as R
        R.main(a.crypto_split)
    return 0


if __name__ == "__main__":
    sys.exit(main())
