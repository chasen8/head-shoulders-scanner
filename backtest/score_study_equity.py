"""D50: does a better score help when the score only orders same-day signals (D44 equity-curve framework)?

  python -m backtest.score_study_equity [--workers 14] [--seeds 20]

Reuses backtest.edges_run (account simulation, train 2017-11 / crypto 2019-09 .. 2021, frozen test 2022 .. 2026-10).
Same-day order: random (20 seeds, the D43/D44 baseline) vs current pattern_score vs the training-selected new score,
and "only the top 30% by score" (cut = training percentile of the same market and timeframe).
Output: C:\\trading-data\\ihs-scanner-v2\\output\\score-study\\tables_equity.md
"""
from __future__ import annotations

import os

for _v in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_v, "1")

import argparse
import multiprocessing as mp
import sys
import time

import numpy as np
import pandas as pd

from backtest import data as D
from backtest import edges_run as ER
from backtest import exits_run as XR
from backtest.score_study_run import OUT_DIR

PORTS = [("台股 1d+1w 全部", "tw"), ("加密 1d 全部", "crypto")]
RULES = ("TP2", "TP1")
SCORES = {"score_cur": "pattern_score", "score_new": "new_loose"}


def train_percentile(df: pd.DataFrame, col: str) -> np.ndarray:
    """Mid-rank ECDF of `col` among the TRAINING rows of the same (market, timeframe)."""
    out = np.full(len(df), np.nan)
    for _k, g in df.groupby(["market", "timeframe"]):
        tr = np.sort(g.loc[(g["period"] == "train") & g[col].notna(), col].to_numpy(float))
        if tr.size < 20:
            continue
        v = g[col].to_numpy(float)
        q = (np.searchsorted(tr, v, side="left") + np.searchsorted(tr, v, side="right")) / 2.0 / tr.size
        q[~np.isfinite(v)] = np.nan
        out[df.index.get_indexer(g.index)] = q
    return out


def aligned_trades(tr: pd.DataFrame, grid: dict, sym_row: dict) -> dict:
    """Same filter / sort / d0>=0 rows as exits_run.build_sim_trades, keeping the identifying columns."""
    out = {}
    for pname, market, sub, tfs in XR.PORTFOLIOS:
        g0 = tr[(tr["market"] == market) & (tr["subset"] == sub) & tr["timeframe"].isin(tfs)]
        gd = grid[market]
        for rule, g in g0.groupby("rule"):
            g = g.assign(_sym=[sym_row[market].get((s, str(y)), -1) for s, y in zip(g["source"], g["symbol"])])
            g = g[g["_sym"] >= 0].sort_values(["entry_ct", "timeframe", "symbol"])
            d0 = np.searchsorted(gd, g["entry_ct"].to_numpy(np.int64), side="right") - 1
            out[(pname, rule)] = g[d0 >= 0].reset_index(drop=True)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=14)
    ap.add_argument("--seeds", type=int, default=20)
    args = ap.parse_args(argv)
    t0 = time.time()
    bm = ER.load_benchmarks(offline=True)
    grid = {"tw": bm["tw"]["t"], "crypto": bm["crypto_1d"]["t"]}
    bench_v = {"tw": bm["tw"]["tr"], "crypto": bm["crypto_1d"]["tr"]}
    idx = lambda m, s: int(np.searchsorted(grid[m], pd.Timestamp(s, tz="UTC").value))
    bounds = {m: {"train": (idx(m, ER.TRAIN_START[m]), idx(m, ER.TEST_START) - 1),
                  "test": (idx(m, ER.TEST_START), len(grid[m]) - 1),
                  "full": (idx(m, ER.TRAIN_START[m]), len(grid[m]) - 1)} for m in grid}
    tasks = []
    for market in ("tw", "crypto"):
        for p in sorted((D.RESULTS_DIR / "main" / market / "signals").glob("*.csv")):
            source, symbol = p.stem.split("_", 1)
            tasks.append((market, source, symbol, grid[market]))
    rows = {"tw": [], "crypto": []}
    sym_row = {"tw": {}, "crypto": {}}
    with mp.Pool(args.workers) as pool:
        for market, source, symbol, v in pool.imap_unordered(ER.px_worker, tasks, chunksize=8):
            if v is not None:
                sym_row[market][(source, str(symbol))] = len(rows[market])
                rows[market].append(v)
    px = {m: np.vstack(rows[m]) for m in rows}
    bench_eq = {m: bench_v[m] / bench_v[m][0] for m in px}
    print(f"prices {time.time() - t0:.0f}s", flush=True)

    use = ["market", "source", "symbol", "timeframe", "subset", "rule", "pattern_id", "entry_price", "risk_pct",
           "net_ret", "is_open", "entry_ct", "n_legs", "leg1_frac", "leg1_ct", "leg1_px", "leg2_frac", "leg2_ct",
           "leg2_px"]
    tr = pd.read_csv(ER.EXITS_TRADES, encoding="utf-8", usecols=use, low_memory=False)
    tr = tr[tr["rule"].isin(RULES)].copy()
    tr["is_open"] = tr["is_open"].astype(str).str.lower().eq("true")
    sim_all, _pm = XR.build_sim_trades(tr, grid, sym_row)
    al = aligned_trades(tr, grid, sym_row)

    sc = pd.read_csv(OUT_DIR / "merged_with_scores.csv.gz", low_memory=False)
    sc = sc[sc["exit"] == "TP1"].drop_duplicates(["market", "source", "symbol", "timeframe", "pattern_id"])
    sc = sc.reset_index(drop=True)
    for k, col in SCORES.items():
        sc[k] = sc[col]
        sc["p_" + k] = train_percentile(sc, k)
    sc["symbol"] = sc["symbol"].astype(str)
    keep = ["market", "source", "symbol", "timeframe", "pattern_id"] + list(SCORES) + ["p_" + k for k in SCORES]
    sim, calib = {}, []
    for (pname, rule), g in al.items():
        if (pname, rule) not in sim_all or pname not in dict(PORTS):
            continue
        g = g.assign(symbol=g["symbol"].astype(str)).merge(sc[keep], on=["market", "source", "symbol", "timeframe",
                                                                         "pattern_id"], how="left")
        arr = dict(sim_all[(pname, rule)])
        assert len(g) == len(arr["sym"]), (pname, rule, len(g), len(arr["sym"]))
        for k in SCORES:
            arr[k] = g[k].to_numpy(float)
            arr["p_" + k] = g["p_" + k].to_numpy(float)
        sim[(pname, rule)] = arr
        calib.append({"組合": pname, "出場": rule, "交易數": len(g),
                      "現行分數缺值": int(np.isnan(arr["score_cur"]).sum()),
                      "新分數缺值": int(np.isnan(arr["score_new"]).sum())})
    print(pd.DataFrame(calib).to_string(), flush=True)

    bench_perf = {}
    for pname, market in PORTS:
        for per, (lo, hi) in bounds[market].items():
            eq = bench_v[market] / bench_v[market][lo] * (1 - ER.F.BENCH_COSTS[market]["buy"])
            bench_perf[(pname, per)] = ER.X.perf(eq, grid[market], lo, hi)
    state = {"px": px, "grid": grid, "bench_v": bench_v, "bench_eq": bench_eq,
             "regime": {m: {} for m in px}, "acct_mult": {m: np.ones(len(grid[m])) for m in px},
             "bounds": bounds, "sim": sim, "port_market": dict(PORTS)}
    ER._init(state)
    items = [("S", "隨機同日排序(D43)", ER.cfg()),
             ("S", "同日依現行分數排序", ER.cfg(order="score_cur")),
             ("S", "只做現行分數前30%", ER.cfg(order="score_cur", filt=0.3)),
             ("S", "同日依新分數排序", ER.cfg(order="score_new")),
             ("S", "只做新分數前30%", ER.cfg(order="score_new", filt=0.3))]
    jobs = []
    for method, name, c in items:
        for pname, rule_ in [(p, r) for p, _m in PORTS for r in RULES]:
            if c["order"] == "score_new" and pname.startswith("加密"):
                continue  # no feature passed the training rule for crypto
            sd = list(range(args.seeds)) if c["order"] == "random" else [None]
            for per in ("train", "test"):
                for s in sd:
                    jobs.append((method, name, c, pname, rule_, per, s, 0))
    with mp.Pool(args.workers, initializer=ER._init, initargs=(state,)) as pool:
        res = pool.map(ER.sim_task, jobs, chunksize=4)
    a = ER.aggregate(res, bench_perf)
    out = ["# score-study 資金曲線（同日排序）\n"]
    for per, tag in (("train", "訓練"), ("test", "測試")):
        rows_ = []
        for pname, _m in PORTS:
            bp = bench_perf[(pname, per)]
            rows_.append({"組合": pname, "出場": "—", "方案": "100% 持有基準", "年化%": f"{100 * bp['cagr']:.1f}",
                          "最大回撤%": f"{100 * bp['mdd']:.1f}", "夏普": f"{bp['sharpe']:.2f}", "進場筆數": "—"})
            for rule_ in RULES:
                for _m_, name, _c in items:
                    r = a[(a["portfolio"] == pname) & (a["rule"] == rule_) & (a["name"] == name) & (a["period"] == per)]
                    if not len(r):
                        continue
                    r = r.iloc[0]
                    rows_.append({"組合": pname, "出場": rule_, "方案": name, "年化%": f"{100 * r['cagr']:.1f}",
                                  "最大回撤%": f"{100 * r['mdd']:.1f}", "夏普": f"{r['sharpe']:.2f}",
                                  "進場筆數": f"{r['taken']:.0f}"})
        out.append(f"## {tag}期\n\n" + ER.md_table(pd.DataFrame(rows_), list(rows_[0])) + "\n")
    (OUT_DIR / "tables_equity.md").write_text("\n".join(out), encoding="utf-8")
    a.to_csv(OUT_DIR / "equity_runs.csv", index=False, encoding="utf-8")
    print("wrote tables_equity.md", f"{time.time() - t0:.0f}s", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
