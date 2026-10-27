"""D49 double bottom vs inverse head and shoulders vs buy-and-hold: report tables.

  python -m backtest.db_report [--workers 15] [--seeds 20] [--tag db]

Inputs (no detector re-run): results/<tag>/<market>/{signals,trades}/*.csv (D40 walk-forward driver with
`--families db`), results/main/trades_all.csv (the D40 IHS trades), results/exits/trades.csv.gz (D43 IHS trades with
the benchmark return per trade), the 1d / 1w K-line cache, data/benchmark/0050.TW_1d.csv and the cached BTCUSDT 1d.
Outputs: results/<tag>/report_tables.md, results/<tag>/trades_exits.csv.gz and
C:\\trading-data\\ihs-scanner-v2\\output\\雙底回測_<date>.xlsx (not in git).

Periods (D44): training 2017-11-01 (crypto 2019-09-01) .. 2021-12-31, test 2022-01-01 .. data end; trades are split by
the entry bar's close time. Account curves (D43 / D44 settings): 1% equity risk per trade over the stop distance,
at most 10 positions, idle cash in 0050 (adjclose) / BTC, same-day order random (median over seeds), TW 1d+1w and
crypto 1d, entry mode A (breakout confirmed).
"""
from __future__ import annotations

import argparse
import multiprocessing as mp
import sys
import time
import zlib
from pathlib import Path

import numpy as np
import pandas as pd

from backtest import data as D
from backtest import edges_run as ER
from backtest import engine as E
from backtest import exits as X
from backtest import exits_run as XR
from backtest import followup as F
from backtest.followup_run import load_benchmarks, md_table
from backtest.report import OUTPUT_DIR, write_excel
from ihs_scanner.detector import wilder_atr

RULES = ("TP1", "TP2", "HALF", "ATR3_C")
TFS = {"tw": ("1d", "1w"), "crypto": ("1d",)}  # same as D43
TRAIN_START = ER.TRAIN_START
TEST_START = ER.TEST_START
PORTS = [("台股 1d+1w", "tw", ("1d", "1w")), ("加密 1d", "crypto", ("1d",))]
RANDOM_K = 5
MKT_LABEL = {"tw": "台股", "crypto": "加密"}


def _log(tag: str, text: str) -> None:
    line = f"{time.strftime('%H:%M:%S')} {text}"
    print(line, flush=True)
    d = D.RESULTS_DIR / tag
    d.mkdir(parents=True, exist_ok=True)
    with open(d / "report.log", "a", encoding="utf-8") as fh:
        fh.write(line + "\n")


# ------------------------------------------------------------------ W trades through the D43 exit machinery

def db_worker(task: tuple) -> tuple[list[dict], tuple, np.ndarray | None]:
    market, source, symbol, sig_path, grid_ns, bench_v = task
    frames = {}
    for tf in TFS[market]:
        p = D.cache_file(market, source, symbol, tf)
        if p.exists():
            df = D.load_frame(p)
            if len(df):
                frames[tf] = df
    px = None
    if "1d" in frames:
        d1 = frames["1d"]
        ct1 = d1["close_time"].astype("datetime64[ns, UTC]").astype("int64").to_numpy()
        px = F.asof_values(ct1, d1["close"].to_numpy(float), grid_ns)
    out: list[dict] = []
    rng = np.random.default_rng(zlib.crc32(f"{market}|{source}|{symbol}".encode("utf-8")))
    try:
        sg = pd.read_csv(sig_path, encoding="utf-8", low_memory=False)
    except (pd.errors.EmptyDataError, FileNotFoundError):
        return out, (market, source, symbol), px
    if not len(sg) or not {"family", "mode", "timeframe"} <= set(sg.columns):
        return out, (market, source, symbol), px
    sg = sg[(sg["family"] == "db") & (sg["mode"] == "A") & (sg["timeframe"].isin(TFS[market]))]
    for tf, g in sg.groupby("timeframe"):
        df = frames.get(tf)
        if df is None:
            continue
        b = E.Bars.of(df)
        atr = wilder_atr(df, 14)
        ct = df["close_time"].astype("datetime64[ns, UTC]").astype("int64").to_numpy()
        # random-entry baseline: same %-stop / %-target, entries drawn in the same period (train / test)
        lo_ns, mid_ns = (pd.Timestamp(TRAIN_START[market], tz="UTC").value, pd.Timestamp(TEST_START, tz="UTC").value)
        j_lo, j_mid = int(np.searchsorted(ct, lo_ns)), int(np.searchsorted(ct, mid_ns))
        sigs = [{"t": int(s.t), "pattern_id": s.pattern_id, "RS_price": s.invalidation_price, "tp1": s.tp1,
                 "tp2": s.tp2} for s in g.itertuples(index=False) if int(s.t) < b.n]
        if not sigs:
            continue
        for key in RULES:
            rule = X.RULE_BY_KEY[key]
            trades, _sk = X.series_trades(b, sigs, rule, tf, market, atr)
            for tr in trades:
                legs = tr.pop("legs")
                e0 = int(ct[tr["t"]])
                leg_ct = [int(ct[i]) if r != "open" else -1 for _f, i, _p, r in legs]
                bench = None
                if not tr["is_open"]:
                    br = [F.bench_return(grid_ns, bench_v, np.array([e0]), np.array([c]), market)[0] for c in leg_ct]
                    bench = float(sum(f * r for (f, _i, _p, _r), r in zip(legs, br)))
                rand_ret = rand_win = None
                if key in ("TP1", "TP2") and not tr["is_open"]:
                    tp_px = tr["tp1"] if key == "TP1" else tr["tp2"]
                    in_test = e0 >= mid_ns
                    st = F.draw_starts(rng, max(E.START[tf], j_mid if in_test else j_lo),
                                       b.n - 2 if in_test else min(j_mid - 1, b.n - 2), RANDOM_K)
                    rr, ww = F.random_bracket(b, st, float(tr["risk_pct"]), float(tp_px) / tr["entry_price"] - 1.0,
                                              E.MAX_HOLD[tf], market)
                    if rr:
                        rand_ret, rand_win = float(np.mean(rr)), float(np.mean(ww))
                rec = {"market": market, "source": source, "symbol": symbol, "timeframe": tf, "subset": "全部",
                       "rand_ret": rand_ret, "rand_win": rand_win,
                       "rule": key, **tr, "entry_ct": e0, "exit_ct": int(ct[tr["exit_bar"]]),
                       "days_held": (int(ct[tr["exit_bar"]]) - e0) / 86400e9, "bench_tr": bench, "n_legs": len(legs)}
                for j, (f, _i, p, r) in enumerate(legs, 1):
                    rec.update({f"leg{j}_frac": f, f"leg{j}_ct": leg_ct[j - 1], f"leg{j}_px": p, f"leg{j}_reason": r})
                out.append(rec)
    return out, (market, source, symbol), px


# ------------------------------------------------------------------ per-trade statistics

def period_of(entry_ct: pd.Series, market: str) -> pd.Series:
    t = pd.to_datetime(entry_ct, utc=True)
    lo, mid = pd.Timestamp(TRAIN_START[market], tz="UTC"), pd.Timestamp(TEST_START, tz="UTC")
    return pd.Series(np.where(t < lo, "pre", np.where(t < mid, "train", "test")), index=entry_ct.index)


STAT_COLS = ["筆數", "勝率%（淨報酬>0）", "停利%", "平均報酬%（期望值）", "中位數報酬%", "平均R", "獲利因子",
             "最大連續虧損（筆）", "平均持有天數", "同期基準平均%", "平均超額%", "超額 p 值",
             "隨機進場 停利%", "隨機進場 平均報酬%"]


def exits_table(tr: pd.DataFrame, family: str) -> pd.DataFrame:
    rows = []
    for market in ("tw", "crypto"):
        for rule in RULES:
            for per in ("train", "test"):
                for name, tfs in XR.tf_groups(market):
                    g = tr[(tr["market"] == market) & (tr["rule"] == rule) & tr["timeframe"].isin(tfs)
                           & ~tr["is_open"]]
                    g = g[period_of(g["entry_ct"], market).to_numpy() == per]
                    if not len(g):
                        continue
                    extra = {}
                    if "rand_ret" in g.columns and g["rand_ret"].notna().any():
                        extra = {"隨機進場 停利%": 100 * g["rand_win"].mean(), "隨機進場 平均報酬%": 100 * g["rand_ret"].mean()}
                    rows.append({"型態": family, "市場": MKT_LABEL[market], "出場": rule, "期間": per, "週期": name,
                                 **XR.trade_stats(g), **extra})
    return pd.DataFrame(rows)


def engine_stats(g: pd.DataFrame) -> dict:
    n = len(g)
    r = g["net_ret"].astype(float)
    ok = g["risk_pct"] >= XR.MIN_RISK_FOR_R
    R = g.loc[ok, "R"].astype(float)
    return {"筆數": n, "停利勝率%": 100 * g["tp_hit"].astype(bool).mean() if n else None,
            "淨報酬>0 %": 100 * (r > 0).mean() if n else None, "平均報酬%": 100 * r.mean() if n else None,
            "中位數報酬%": 100 * r.median() if n else None, "平均R": R.mean() if len(R) else None,
            "最大連續虧損": X.max_losing_streak(r.to_numpy())}


def load_engine_trades(tag: str) -> pd.DataFrame:
    use = ["market", "symbol", "family", "timeframe", "mode", "exit", "entry_time", "net_ret", "risk_pct", "R",
           "tp_hit", "is_open"]
    parts = []
    for market in ("crypto", "tw"):
        for p in (D.RESULTS_DIR / tag / market / "trades").glob("*.csv"):
            try:
                parts.append(pd.read_csv(p, encoding="utf-8", usecols=lambda c: c in use))
            except (pd.errors.EmptyDataError, ValueError):
                continue
    return pd.concat(parts, ignore_index=True)


def engine_table(db: pd.DataFrame, ihs: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for fam, df in (("W 底", db), ("頭肩底", ihs)):
        df = df[~df["is_open"].astype(bool)].copy()
        df["entry_time"] = pd.to_datetime(df["entry_time"], utc=True, format="ISO8601")
        for market in ("tw", "crypto"):
            for mode in ("A", "B"):
                for tf in ("4h", "1d", "1w"):
                    for ex in ("TP1", "TP2", "half"):
                        g = df[(df["market"] == market) & (df["mode"] == mode) & (df["timeframe"] == tf)
                               & (df["exit"] == ex)]
                        lo, mid = pd.Timestamp(TRAIN_START[market], tz="UTC"), pd.Timestamp(TEST_START, tz="UTC")
                        for per, gg in (("train", g[(g["entry_time"] >= lo) & (g["entry_time"] < mid)]),
                                        ("test", g[g["entry_time"] >= mid])):
                            if len(gg):
                                rows.append({"型態": fam, "市場": MKT_LABEL[market], "進場": mode, "週期": tf,
                                             "出場": ex, "期間": per, **engine_stats(gg)})
    return pd.DataFrame(rows)


# ------------------------------------------------------------------ account curves

_G: dict = {}


def _init(state: dict) -> None:
    _G.update(state)


def sim_task(task: tuple) -> dict:
    fam, pname, rule, period, seed = task
    market = next(m for n, m, _t in PORTS if n == pname)
    arr = _G["sim"][fam][(pname + " 全部", rule)]
    key = (fam, pname, rule)
    cache = _G.setdefault("_unp", {})
    if key not in cache:
        cache.clear()
        cache[key] = XR.unpack(arr)
    trades = cache[key]
    lo, hi = _G["bounds"][market][period]
    order = np.random.default_rng(seed).permutation(len(trades)) if seed is not None else None
    res = X.equity_curve(_G["px"][market][:, :hi + 1], trades, bench=_G["bench_v"][market][:hi + 1], risk=0.01,
                         max_pos=10, costs=E.COSTS[market], bench_costs=F.BENCH_COSTS[market], start=lo, order=order)
    pf = X.perf(res["equity"], _G["grid"][market][:hi + 1], lo, hi)
    return {"型態": fam, "組合": pname, "出場": rule, "期間": period, "seed": seed, **pf,
            "taken": res["taken"], "exposure": float(np.nanmean(res["exposure"][lo:hi + 1]))}


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--tag", default="db")
    p.add_argument("--workers", type=int, default=15)
    p.add_argument("--seeds", type=int, default=20)
    return p.parse_args(argv)


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    args = parse_args(argv)
    tag = args.tag
    t0 = time.time()
    bm = load_benchmarks(offline=True)
    grid = {"tw": bm["tw"]["t"], "crypto": bm["crypto_1d"]["t"]}
    bench_v = {"tw": bm["tw"]["tr"], "crypto": bm["crypto_1d"]["tr"]}
    idx = lambda m, s: int(np.searchsorted(grid[m], pd.Timestamp(s, tz="UTC").value))
    bounds = {m: {"train": (idx(m, TRAIN_START[m]), idx(m, TEST_START) - 1),
                  "test": (idx(m, TEST_START), len(grid[m]) - 1)} for m in grid}

    # ---- W trades (D43 exit machinery) + price matrices
    tasks = []
    for market in ("tw", "crypto"):
        for p in sorted((D.RESULTS_DIR / tag / market / "signals").glob("*.csv")):
            source, symbol = p.stem.split("_", 1)
            tasks.append((market, source, symbol, str(p), grid[market], bench_v[market]))
    recs, rows = [], {"tw": [], "crypto": []}
    sym_row = {"tw": {}, "crypto": {}}
    with mp.Pool(args.workers) as pool:
        for k, (a, (market, source, symbol), px) in enumerate(pool.imap_unordered(db_worker, tasks, chunksize=4), 1):
            recs.extend(a)
            if px is not None:
                sym_row[market][(source, str(symbol))] = len(rows[market])
                rows[market].append(px)
    _log(tag, f"[report] W trades {len(recs)} from {len(tasks)} symbols ({time.time() - t0:.0f}s)")
    tr_db = pd.DataFrame(recs)
    tr_db["is_open"] = tr_db["is_open"].astype(bool)
    tr_db.to_csv(D.RESULTS_DIR / tag / "trades_exits.csv.gz", index=False, encoding="utf-8", compression="gzip")
    px = {m: np.vstack(rows[m]) for m in rows}

    # ---- IHS baseline = D43 trades (same exit machinery, same universe)
    use = list(tr_db.columns[:0]) + ["market", "source", "symbol", "timeframe", "subset", "rule", "t", "entry_price",
                                      "risk_pct", "net_ret", "R", "exit_reason", "is_open", "entry_ct", "exit_ct",
                                      "days_held", "bench_tr", "n_legs", "leg1_frac", "leg1_ct", "leg1_px",
                                      "leg2_frac", "leg2_ct", "leg2_px"]
    tr_ihs = pd.read_csv(XR.D.RESULTS_DIR / "exits" / "trades.csv.gz", encoding="utf-8", usecols=use, low_memory=False)
    tr_ihs = tr_ihs[(tr_ihs["subset"] == "全部") & tr_ihs["rule"].isin(RULES)].copy()
    tr_ihs["is_open"] = tr_ihs["is_open"].astype(str).str.lower().eq("true")
    _log(tag, f"[report] IHS baseline trades {len(tr_ihs)}")

    tab_ex = pd.concat([exits_table(tr_db, "W 底"), exits_table(tr_ihs, "頭肩底")], ignore_index=True)

    # ---- engine tables (all timeframes, both entry modes, D40 engine exits)
    eng_db = load_engine_trades(tag)
    main_all = pd.read_csv(D.RESULTS_DIR / "main" / "trades_all.csv", encoding="utf-8", low_memory=False,
                           usecols=["market", "symbol", "family", "timeframe", "mode", "exit", "entry_time", "net_ret",
                                    "risk_pct", "R", "tp_hit", "is_open"])
    eng_ihs = main_all[main_all["family"] == "ihs"]
    tab_eng = engine_table(eng_db, eng_ihs)
    _log(tag, f"[report] engine tables done ({time.time() - t0:.0f}s)")

    # ---- account curves
    sim = {}
    for fam, tr in (("W 底", tr_db), ("頭肩底", tr_ihs)):
        tr = tr.copy()
        tr = tr[tr["rule"].isin(RULES)]
        s, _pm = XR.build_sim_trades(tr, grid, sym_row)
        sim[fam] = s
    state = {"px": px, "grid": grid, "bench_v": bench_v, "bounds": bounds, "sim": sim}
    jobs = []
    for fam in sim:
        for pname, _m, _t in PORTS:
            for rule in RULES:
                if (pname + " 全部", rule) not in sim[fam]:
                    continue
                for per in ("train", "test"):
                    jobs.append((fam, pname, rule, per, None))
                    jobs += [(fam, pname, rule, per, s) for s in range(args.seeds)]
    _log(tag, f"[report] {len(jobs)} equity runs")
    with mp.Pool(args.workers, initializer=_init, initargs=(state,)) as pool:
        res = pool.map(sim_task, jobs, chunksize=4)
    df = pd.DataFrame(res)
    acc = (df[df["seed"].notna()].groupby(["型態", "組合", "出場", "期間"], sort=False)
           .agg(年化中位數=("cagr", "median"), 年化最小=("cagr", "min"), 年化最大=("cagr", "max"),
                最大回撤中位數=("mdd", "median"), 夏普中位數=("sharpe", "median"), 進場筆數=("taken", "median"),
                平均持股比例=("exposure", "mean")).reset_index())
    for c in ("年化中位數", "年化最小", "年化最大", "最大回撤中位數", "平均持股比例"):
        acc[c] = 100 * acc[c]
    # ---- buy and hold
    bh = []
    for pname, market, _t in PORTS:
        for per, (lo, hi) in bounds[market].items():
            eq = bench_v[market] / bench_v[market][lo] * (1 - F.BENCH_COSTS[market]["buy"])
            pf = X.perf(eq, grid[market], lo, hi)
            span = f"{pd.to_datetime(grid[market][lo], utc=True).date()}～{pd.to_datetime(grid[market][hi], utc=True).date()}"
            bh.append({"組合": pname, "基準": "0050 還原" if market == "tw" else "BTC", "期間": per, "區間": span,
                       "年化%": 100 * pf["cagr"], "最大回撤%": 100 * pf["mdd"], "夏普": pf["sharpe"],
                       "總報酬%": 100 * pf["total"]})
    tab_bh = pd.DataFrame(bh)

    out_md = D.RESULTS_DIR / tag / "report_tables.md"
    parts = ["## A. 單筆統計（D43 出場機制，進場 A 突破確認；1d／1w；含同期基準）",
             md_table(tab_ex, ["型態", "市場", "出場", "期間", "週期"] + STAT_COLS),
             "", "## B. D40 引擎統計（進場 A／B × 週期 × 引擎出場）", md_table(tab_eng, list(tab_eng.columns)),
             "", "## C. 帳戶曲線（1% 風險、最多 10 筆、閒置放基準；種子中位數）", md_table(acc, list(acc.columns)),
             "", "## D. 買進持有基準", md_table(tab_bh, list(tab_bh.columns))]
    out_md.write_text("\n".join(parts), encoding="utf-8")
    xl = OUTPUT_DIR / f"雙底回測_{time.strftime('%Y%m%d')}.xlsx"
    write_excel(xl, {"單筆_D43出場": tab_ex, "單筆_D40引擎": tab_eng, "帳戶曲線": acc, "買進持有": tab_bh,
                     "帳戶曲線_全部種子": df},
                [("說明", "D49 W 底回測；方法見 docs/backtest-double-bottom.md")])
    _log(tag, f"[report] done in {time.time() - t0:.0f}s -> {out_md}, {xl}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
