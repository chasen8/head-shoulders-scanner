"""D43: exit / stop management on the D40 IHS A entries (TW 1d + 1w, crypto 1d), per-trade statistics vs the
benchmark and a simple portfolio (equity curve) simulation.

  python -m backtest.exits_run [--workers 15] [--seeds 20] [--max-symbols N] [--tag main]

Inputs (no detector re-run): results/<tag>/<market>/signals/*.csv (D40 signals), the kline cache
data/<market>/<source>/<symbol>_<tf>.csv, data/benchmark/0050.TW_1d.csv (adjclose) and the cached BTCUSDT 1d.
Outputs (not in git): results/exits/{trades.csv.gz,summary.md,run.log} and
C:\\trading-data\\ihs-scanner-v2\\output\\出場方式比較_<date>.xlsx. Pure logic lives in backtest/exits.py.
"""
from __future__ import annotations

import argparse
import multiprocessing as mp
import sys
import time

import numpy as np
import pandas as pd

from backtest import data as D
from backtest import engine as E
from backtest import exits as X
from backtest import followup as F
from backtest.followup_run import HW_ENTRY, HW_NECK, load_benchmarks, md_table
from backtest.report import OUTPUT_DIR, write_excel
from ihs_scanner.detector import wilder_atr

OUT_DIR = D.RESULTS_DIR / "exits"
MIN_RISK_FOR_R = 0.005
TFS = {"tw": ("1d", "1w"), "crypto": ("1d",)}
SUBSETS = ("全部", "高勝率篩選")
PERIODS = (("全期", 0, 9999), ("2016–2022", 2016, 2022), ("2023–2026", 2023, 2026))
SIM_START = {"tw": "2016-11-01", "crypto": "2018-09-01"}
SPLIT = "2023-01-01"
PORTFOLIOS = [  # name, market, subset, timeframes
    ("台股 1d+1w 全部", "tw", "全部", ("1d", "1w")),
    ("台股 1d+1w 高勝率篩選", "tw", "高勝率篩選", ("1d", "1w")),
    ("台股 1d 全部", "tw", "全部", ("1d",)),
    ("台股 1w 全部", "tw", "全部", ("1w",)),
    ("加密 1d 全部", "crypto", "全部", ("1d",)),
    ("加密 1d 高勝率篩選", "crypto", "高勝率篩選", ("1d",)),
]
DEFAULT_M, DEFAULT_RISK = 10, 0.01
SENS_M, SENS_RISK = (5, 10, 20), (0.005, 0.01, 0.02)
DROPS = (5, 20)  # equity runs without the N best trades of the rule's list


def _log(text: str) -> None:
    line = f"{time.strftime('%H:%M:%S')} {text}"
    print(line, flush=True)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(OUT_DIR / "run.log", "a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def hw_flag(neck_change, close_t, level) -> bool:
    try:
        return HW_NECK[0] <= float(neck_change) <= HW_NECK[1] and float(close_t) >= HW_ENTRY * float(level)
    except (TypeError, ValueError):
        return False


# ------------------------------------------------------------------ per-symbol worker

def worker(task: tuple) -> tuple[list[dict], dict, tuple, np.ndarray | None]:
    market, source, symbol, sig_path, grid_ns, bench_t, bench_v = task
    skips: dict = {}
    out: list[dict] = []
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
    try:
        sg = pd.read_csv(sig_path, encoding="utf-8", low_memory=False)
    except (pd.errors.EmptyDataError, FileNotFoundError):
        sg = pd.DataFrame()
    if not len(sg) or not {"family", "mode", "timeframe"} <= set(sg.columns):
        return out, skips, (market, source, symbol), px
    sg = sg[(sg["family"] == "ihs") & (sg["mode"] == "A") & (sg["timeframe"].isin(TFS[market]))]
    for tf, g in sg.groupby("timeframe"):
        df = frames.get(tf)
        if df is None:
            continue
        b = E.Bars.of(df)
        atr = wilder_atr(df, 14)
        ct = df["close_time"].astype("datetime64[ns, UTC]").astype("int64").to_numpy()
        sigs = []
        for s in g.itertuples(index=False):
            t = int(s.t)
            if t >= b.n:
                continue
            sigs.append({"t": t, "pattern_id": s.pattern_id, "RS_price": s.RS_price, "tp1": s.tp1, "tp2": s.tp2,
                         "hw": hw_flag(s.neckline_change, b.close[t], s.breakout_level)})
        for sub in SUBSETS:
            ss = sigs if sub == "全部" else [s for s in sigs if s["hw"]]
            if not ss:
                continue
            for rule in X.RULES:
                trades, sk = X.series_trades(b, ss, rule, tf, market, atr)
                for k, v in sk.items():
                    key = (market, tf, sub, rule.key, k)
                    skips[key] = skips.get(key, 0) + v
                for tr in trades:
                    legs = tr.pop("legs")
                    e0 = int(ct[tr["t"]])
                    leg_ct = [int(ct[i]) if r != "open" else -1 for _f, i, _p, r in legs]
                    bench = None
                    if not tr["is_open"]:
                        br = [F.bench_return(bench_t, bench_v, np.array([e0]), np.array([c]), market)[0]
                              for c in leg_ct]
                        bench = float(sum(f * r for (f, _i, _p, _r), r in zip(legs, br)))
                    rec = {"market": market, "source": source, "symbol": symbol, "timeframe": tf, "subset": sub,
                           "rule": rule.key, **tr, "entry_ct": e0, "exit_ct": int(ct[tr["exit_bar"]]),
                           "days_held": (int(ct[tr["exit_bar"]]) - e0) / 86400e9, "bench_tr": bench,
                           "n_legs": len(legs)}
                    for j, (f, _i, p, r) in enumerate(legs, 1):
                        rec.update({f"leg{j}_frac": f, f"leg{j}_ct": leg_ct[j - 1], f"leg{j}_px": p,
                                    f"leg{j}_reason": r})
                    out.append(rec)
    return out, skips, (market, source, symbol), px


# ------------------------------------------------------------------ statistics

def trade_stats(g: pd.DataFrame) -> dict:
    g = g.sort_values(["entry_ct", "symbol"])
    n = len(g)
    r = g["net_ret"].astype(float)
    R = g.loc[g["risk_pct"] >= MIN_RISK_FOR_R, "R"].astype(float)
    rr = X.rr_stats(R)
    ex = (g["net_ret"] - g["bench_tr"]).dropna()
    m, _t, p = F.one_sample_t(ex)
    reasons = g["exit_reason"].value_counts(normalize=True)
    return {"筆數": n, "勝率%（淨報酬>0）": 100 * (r > 0).mean() if n else None,
            "達+1R以上%": 100 * (R >= 1).mean() if len(R) else None,
            "平均R": R.mean() if len(R) else None, "中位數R": R.median() if len(R) else None,
            "平均報酬%（期望值）": 100 * r.mean() if n else None, "中位數報酬%": 100 * r.median() if n else None,
            "獲利因子": F.profit_factor(r) if n else None,
            "平均賺R": rr["avg_win_R"], "平均賠R": rr["avg_loss_R"], "真實RR比（賺R/賠R）": rr["rr"],
            "最大連續虧損（筆）": X.max_losing_streak(r.to_numpy()),
            "平均持有天數": g["days_held"].mean() if n else None,
            "≥3R%": 100 * (R >= 3).mean() if len(R) else None, "≥5R%": 100 * (R >= 5).mean() if len(R) else None,
            "≥10R%": 100 * (R >= 10).mean() if len(R) else None,
            "≤−1.2R%（跳空等）": 100 * (R <= -1.2).mean() if len(R) else None,
            "剔除最佳1%後 平均報酬%": 100 * r.sort_values().iloc[: n - max(1, n // 100)].mean() if n > 1 else None,
            "剔除最佳1%後 平均R": R.sort_values().iloc[: len(R) - max(1, len(R) // 100)].mean() if len(R) > 1 else None,
            "停損%": 100 * reasons.get("stop", 0.0), "保本%": 100 * reasons.get("breakeven", 0.0),
            "移動停損%": 100 * reasons.get("trail", 0.0), "停利%": 100 * reasons.get("tp", 0.0),
            "時間停損%": 100 * reasons.get("time", 0.0), "到期%": 100 * reasons.get("timeout", 0.0),
            "同期基準平均%": 100 * g["bench_tr"].dropna().mean() if len(ex) else None,
            "平均超額%": 100 * m if m is not None else None,
            "超額>0比例%": 100 * (ex > 0).mean() if len(ex) else None, "超額 p 值": p}


def tf_groups(market: str) -> list[tuple[str, tuple]]:
    return [("1d", ("1d",)), ("1w", ("1w",)), ("1d+1w", ("1d", "1w"))] if market == "tw" else [("1d", ("1d",))]


def stats_table(tr: pd.DataFrame) -> pd.DataFrame:
    rows = []
    closed = tr[~tr["is_open"]]
    for (market, sub), g0 in closed.groupby(["market", "subset"], sort=False):
        for tfl, tfs in tf_groups(market):
            g1 = g0[g0["timeframe"].isin(tfs)]
            for rule in X.RULES:
                g2 = g1[g1["rule"] == rule.key]
                for pname, lo, hi in PERIODS:
                    g = g2[(g2["year"] >= lo) & (g2["year"] <= hi)]
                    if not len(g):
                        continue
                    rows.append({"市場": {"tw": "台股", "crypto": "加密"}[market], "子集合": sub, "週期": tfl,
                                 "類別": rule.group, "出場代號": rule.key, "出場方式": rule.label, "期間": pname,
                                 **trade_stats(g)})
    return pd.DataFrame(rows)


def baseline_check(tr: pd.DataFrame, tag: str) -> tuple[pd.DataFrame, dict]:
    """Our D40 baselines vs results/<tag>/trades_all.csv, trade by trade."""
    cols = ["market", "symbol", "source", "family", "timeframe", "mode", "direction", "exit", "pattern_id", "t",
            "is_open", "net_ret", "exit_bar"]
    d40 = pd.read_csv(D.RESULTS_DIR / tag / "trades_all.csv", encoding="utf-8", usecols=cols, low_memory=False)
    d40 = d40[(d40["family"] == "ihs") & (d40["mode"] == "A") & (d40["direction"] == 1)
              & (((d40["market"] == "tw") & d40["timeframe"].isin(["1d", "1w"]))
                 | ((d40["market"] == "crypto") & (d40["timeframe"] == "1d")))]
    d40 = d40.assign(is_open=d40["is_open"].astype(str).str.lower().eq("true"),
                     rule=d40["exit"].map({"TP1": "TP1", "TP2": "TP2", "half": "HALF"}),
                     symbol=d40["symbol"].astype(str))
    ours = tr[(tr["subset"] == "全部") & tr["rule"].isin(["TP1", "TP2", "HALF"])].copy()
    ours["symbol"] = ours["symbol"].astype(str)
    present = set(zip(tr["source"], tr["symbol"].astype(str)))
    d40 = d40[[k in present for k in zip(d40["source"], d40["symbol"])]]
    key = ["market", "source", "symbol", "timeframe", "rule", "pattern_id", "t"]
    m = ours.merge(d40[key + ["net_ret", "exit_bar", "is_open"]], on=key, how="outer", suffixes=("", "_d40"),
                   indicator=True)
    both = m[m["_merge"] == "both"]
    info = {"ours_only": int((m["_merge"] == "left_only").sum()), "d40_only": int((m["_merge"] == "right_only").sum()),
            "matched": len(both),
            "net_ret_mismatch": int(((both["net_ret"] - both["net_ret_d40"]).abs() > 1e-9).sum()),
            "exit_bar_mismatch": int((both["exit_bar"] != both["exit_bar_d40"]).sum())}
    rows = []
    for (market, tf, rule), g in m.groupby(["market", "timeframe", "rule"]):
        a = g[(g["_merge"] != "right_only") & (~g["is_open"].astype(bool))]
        b = g[(g["_merge"] != "left_only") & (~g["is_open_d40"].astype(bool))]
        rows.append({"市場": market, "週期": tf, "出場": rule, "本次 筆數": len(a), "D40 筆數": len(b),
                     "本次 平均報酬%": 100 * a["net_ret"].mean(), "D40 平均報酬%": 100 * b["net_ret_d40"].mean()})
    return pd.DataFrame(rows), info


# ------------------------------------------------------------------ equity simulation (pool globals)

_G: dict = {}


def _init_sim(state: dict) -> None:
    _G.update(state)


def sim_task(task: tuple) -> dict:
    pname, rule, seed, m, risk, cash_mode, drop = task
    market = _G["port_market"][pname]
    cache = _G.setdefault("_unpacked", {})
    if (pname, rule, drop) not in cache:
        cache.clear()  # keep one unpacked list per worker (memory)
        arr = _G["sim_trades"][(pname, rule)]
        if drop:  # leave out the `drop` best trades (by net return) of the whole list: tail dependence
            keep = np.ones(len(arr["net"]), dtype=bool)
            keep[np.argsort(-np.nan_to_num(arr["net"], nan=-np.inf))[:drop]] = False
            arr = {k: v[keep] for k, v in arr.items()}
        cache[(pname, rule, drop)] = unpack(arr)
    trades = cache[(pname, rule, drop)]
    order = None
    if seed is not None:
        order = np.random.default_rng(seed).permutation(len(trades))
    bench = _G["bench_v"][market] if cash_mode == "bench" else None
    res = X.equity_curve(_G["px"][market], trades, bench=bench, risk=risk, max_pos=m, costs=E.COSTS[market],
                         bench_costs=F.BENCH_COSTS[market], start=_G["start"][market], order=order)
    days = _G["grid"][market]
    s, sp = _G["start"][market], _G["split"][market]
    out = {"portfolio": pname, "rule": rule, "seed": seed, "M": m, "risk": risk, "cash": cash_mode, "drop": drop,
           "taken": res["taken"], "scaled": res["scaled"], "skipped_full": res["skipped_full"],
           "exposure": float(np.nanmean(res["exposure"][s:]))}
    for tag, lo, hi in (("全期", s, len(days) - 1), ("2016–2022", s, sp - 1), ("2023–2026", sp - 1, len(days) - 1)):
        out[tag] = X.perf(res["equity"], days, lo, hi)
    if seed is None and m == DEFAULT_M and risk == DEFAULT_RISK and cash_mode == "bench" and not drop:
        out["equity"] = res["equity"]
    return out


def build_sim_trades(tr: pd.DataFrame, grid: dict, sym_row: dict) -> tuple[dict, dict]:
    sim, port_market = {}, {}
    for pname, market, sub, tfs in PORTFOLIOS:
        port_market[pname] = market
        g0 = tr[(tr["market"] == market) & (tr["subset"] == sub) & tr["timeframe"].isin(tfs)]
        gd = grid[market]
        for rule, g in g0.groupby("rule"):
            g = g.assign(_sym=[sym_row[market].get((s, str(y)), -1) for s, y in zip(g["source"], g["symbol"])])
            g = g[g["_sym"] >= 0].sort_values(["entry_ct", "timeframe", "symbol"])
            arr = {"sym": g["_sym"].to_numpy(np.int64),
                   "d0": np.searchsorted(gd, g["entry_ct"].to_numpy(np.int64), side="right") - 1,
                   "entry": g["entry_price"].to_numpy(float), "risk": g["risk_pct"].to_numpy(float),
                   "n_legs": g["n_legs"].to_numpy(np.int64),
                   "net": np.where(g["is_open"].to_numpy(bool), np.nan, g["net_ret"].to_numpy(float))}
            for j in (1, 2):
                c = g[f"leg{j}_ct"].fillna(-1).to_numpy(np.int64) if f"leg{j}_ct" in g else np.full(len(g), -1)
                arr[f"f{j}"] = g[f"leg{j}_frac"].fillna(0).to_numpy(float) if f"leg{j}_frac" in g else np.zeros(len(g))
                arr[f"x{j}"] = g[f"leg{j}_px"].fillna(0).to_numpy(float) if f"leg{j}_px" in g else np.zeros(len(g))
                arr[f"d{j}"] = np.where(c >= 0, np.searchsorted(gd, c, side="right") - 1, -1)
            ok = arr["d0"] >= 0
            sim[(pname, rule)] = {k: v[ok] for k, v in arr.items()}
    return sim, port_market


def unpack(arr: dict) -> list[X.SimTrade]:
    out = []
    for k in range(len(arr["sym"])):
        legs = [(float(arr[f"f{j}"][k]), int(arr[f"d{j}"][k]), float(arr[f"x{j}"][k]))
                for j in range(1, int(arr["n_legs"][k]) + 1)]
        out.append(X.SimTrade(sym=int(arr["sym"][k]), d0=int(arr["d0"][k]), entry=float(arr["entry"][k]),
                              risk_pct=float(arr["risk"][k]), legs=legs, key=(k,)))
    return out


def perf_rows(results: list[dict], bh: dict) -> pd.DataFrame:
    rows = []
    for r in results:
        rec = {"組合": r["portfolio"], "出場代號": r["rule"],
               "出場方式": X.RULE_BY_KEY[r["rule"]].label if r["rule"] in X.RULE_BY_KEY else r["rule"],
               "同時最多持有": r["M"], "每筆風險%": 100 * r["risk"],
               "閒置資金": {"bench": "放基準", "cash": "現金 0%"}[r["cash"]], "排序種子": r["seed"],
               "剔除最佳筆數": r.get("drop", 0),
               "進場筆數": r["taken"], "資金不足縮小筆數": r["scaled"], "滿倉略過筆數": r["skipped_full"],
               "平均持股比例%": 100 * r["exposure"]}
        for tag in ("全期", "2016–2022", "2023–2026"):
            p, b = r[tag], bh[(r["portfolio"], tag)]
            pre = "" if tag == "全期" else f"[{tag}] "
            rec.update({pre + "年化報酬%": _pct(p["cagr"]), pre + "最大回撤%": _pct(p["mdd"]),
                        pre + "夏普": p["sharpe"], pre + "基準年化%": _pct(b["cagr"]),
                        pre + "基準最大回撤%": _pct(b["mdd"]), pre + "基準夏普": b["sharpe"],
                        pre + "年化差（策略−基準）": (_pct(p["cagr"]) - _pct(b["cagr"]))
                        if p["cagr"] is not None and b["cagr"] is not None else None})
        rows.append(rec)
    return pd.DataFrame(rows)


def _pct(v):
    return None if v is None else 100 * v


def yearly_table(results: list[dict], grid: dict, start: dict, bench_v: dict, keys: list[str]) -> pd.DataFrame:
    rows = []
    for r in results:
        if "equity" not in r or r["rule"] not in keys:
            continue
        market = next(m for n, m, _s, _t in PORTFOLIOS if n == r["portfolio"])
        ts = pd.to_datetime(grid[market], utc=True)
        e = pd.Series(r["equity"], index=ts).iloc[start[market]:]
        bv = pd.Series(bench_v[market], index=ts).iloc[start[market]:]
        ye, yb = e.groupby(e.index.year).last(), bv.groupby(bv.index.year).last()
        prev_e, prev_b = e.iloc[0], bv.iloc[0]
        for y in ye.index:
            rows.append({"組合": r["portfolio"], "出場代號": r["rule"], "年": int(y),
                         "策略年報酬%": 100 * (ye[y] / prev_e - 1), "基準年報酬%": 100 * (yb[y] / prev_b - 1)})
            prev_e, prev_b = ye[y], yb[y]
    return pd.DataFrame(rows)


# ------------------------------------------------------------------ main

def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--tag", default="main")
    p.add_argument("--workers", type=int, default=15)
    p.add_argument("--seeds", type=int, default=20)
    p.add_argument("--max-symbols", type=int, default=0)
    return p.parse_args(argv)


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    args = parse_args(argv)
    t0 = time.time()
    bm = load_benchmarks(offline=True)
    grid = {"tw": bm["tw"]["t"], "crypto": bm["crypto_1d"]["t"]}
    bench_v = {"tw": bm["tw"]["tr"], "crypto": bm["crypto_1d"]["tr"]}
    start = {m: int(np.searchsorted(grid[m], pd.Timestamp(SIM_START[m], tz="UTC").value)) for m in grid}
    split = {m: int(np.searchsorted(grid[m], pd.Timestamp(SPLIT, tz="UTC").value)) for m in grid}
    tasks = []
    for market in ("tw", "crypto"):
        for p in sorted((D.RESULTS_DIR / args.tag / market / "signals").glob("*.csv")):
            source, symbol = p.stem.split("_", 1)
            tasks.append((market, source, symbol, str(p), grid[market], grid[market], bench_v[market]))
    if args.max_symbols:
        tasks = tasks[:: max(1, len(tasks) // args.max_symbols)][: args.max_symbols]
    _log(f"[exits] {len(tasks)} symbols, {len(X.RULES)} rules, workers={args.workers}")
    recs, skips = [], {}
    px_rows = {"tw": [], "crypto": []}
    sym_row = {"tw": {}, "crypto": {}}
    with mp.Pool(args.workers) as pool:
        for k, (a, sk, (market, source, symbol), px) in enumerate(pool.imap_unordered(worker, tasks, chunksize=4), 1):
            recs.extend(a)
            for kk, vv in sk.items():
                skips[kk] = skips.get(kk, 0) + vv
            if px is not None:
                sym_row[market][(source, str(symbol))] = len(px_rows[market])
                px_rows[market].append(px)
            if k % 500 == 0 or k == len(tasks):
                _log(f"[exits] {k}/{len(tasks)} symbols, {len(recs)} trades, {time.time() - t0:.0f}s")
    tr = pd.DataFrame(recs)
    tr["is_open"] = tr["is_open"].astype(bool)
    tr["year"] = pd.to_datetime(tr["entry_ct"], utc=True).dt.year
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    tr.to_csv(OUT_DIR / "trades.csv.gz", index=False, encoding="utf-8", compression="gzip")
    t_sim = time.time()
    _log(f"[exits] simulation {t_sim - t0:.0f}s; aggregating")

    base_tab, base_info = baseline_check(tr, args.tag)
    _log(f"[exits] baseline vs D40: {base_info}")
    st = stats_table(tr)
    sk_df = pd.DataFrame([{"市場": k[0], "週期": k[1], "子集合": k[2], "出場代號": k[3], "略過原因": k[4], "筆數": v}
                          for k, v in sorted(skips.items())])

    # ---- equity curves
    px = {m: (np.vstack(px_rows[m]) if px_rows[m] else np.zeros((0, len(grid[m])))) for m in grid}
    sim, port_market = build_sim_trades(tr, grid, sym_row)
    state = {"px": px, "grid": grid, "bench_v": bench_v, "start": start, "split": split, "sim_trades": sim,
             "port_market": port_market}
    bh = {}
    for pname, market, _s, _t in PORTFOLIOS:
        bv = bench_v[market].astype(float).copy()
        eq = bv / bv[start[market]] * (1 - F.BENCH_COSTS[market]["buy"])
        s, sp = start[market], split[market]
        for tag, lo, hi in (("全期", s, len(bv) - 1), ("2016–2022", s, sp - 1), ("2023–2026", sp - 1, len(bv) - 1)):
            bh[(pname, tag)] = X.perf(eq, grid[market], lo, hi)
    rules_all = [r.key for r in X.RULES]
    default_tasks = [(p, r, None, DEFAULT_M, DEFAULT_RISK, "bench", 0) for p, *_ in PORTFOLIOS for r in rules_all
                     if (p, r) in sim]
    with mp.Pool(args.workers, initializer=_init_sim, initargs=(state,)) as pool:
        res_default = pool.map(sim_task, default_tasks, chunksize=4)
        p_def = perf_rows(res_default, bh)
        seed_tasks = [(p, r, s, DEFAULT_M, DEFAULT_RISK, "bench", 0) for p, r, *_ in default_tasks
                      for s in range(args.seeds)]
        res_seed = pool.map(sim_task, seed_tasks, chunksize=8)
        sd = perf_rows(res_seed, bh)
        seed_tab = (sd.groupby(["組合", "出場代號", "出場方式"], sort=False)
                    .agg(**{"種子數": ("排序種子", "count"),
                            "年化% 中位數": ("年化報酬%", "median"), "年化% 最小": ("年化報酬%", "min"),
                            "年化% 最大": ("年化報酬%", "max"),
                            "最大回撤% 中位數": ("最大回撤%", "median"), "夏普 中位數": ("夏普", "median"),
                            "平均持股比例% 中位數": ("平均持股比例%", "median"),
                            "基準年化%": ("基準年化%", "first"), "基準最大回撤%": ("基準最大回撤%", "first"),
                            "基準夏普": ("基準夏普", "first"),
                            "贏基準年化的種子比例%": ("年化差（策略−基準）", lambda v: 100 * (v > 0).mean()),
                            "[2016–2022] 年化% 中位數": ("[2016–2022] 年化報酬%", "median"),
                            "[2016–2022] 基準年化%": ("[2016–2022] 基準年化%", "first"),
                            "[2023–2026] 年化% 中位數": ("[2023–2026] 年化報酬%", "median"),
                            "[2023–2026] 基準年化%": ("[2023–2026] 基準年化%", "first")})
                    .reset_index())
        # sensitivity / cash / leave-out-best for the baselines and the 5 best rules (seed median) per portfolio
        top = {}
        for pname, *_ in PORTFOLIOS:
            g = seed_tab[seed_tab["組合"] == pname].sort_values("年化% 中位數", ascending=False)
            top[pname] = list(dict.fromkeys(["TP1", "TP2", "HALF"] + g["出場代號"].head(5).tolist()))
        more = []
        for pname, keys in top.items():
            for r in keys:
                if (pname, r) not in sim:
                    continue
                for m in SENS_M:
                    for rk in SENS_RISK:
                        if (m, rk) != (DEFAULT_M, DEFAULT_RISK):
                            more.append((pname, r, None, m, rk, "bench", 0))
                more.append((pname, r, None, DEFAULT_M, DEFAULT_RISK, "cash", 0))
                for drop in DROPS:
                    more.append((pname, r, None, DEFAULT_M, DEFAULT_RISK, "bench", drop))
        res_more = pool.map(sim_task, more, chunksize=4)
    p_sens = perf_rows(res_more + [r for r in res_default if any(r["rule"] in v for k, v in top.items()
                                                                  if k == r["portfolio"])], bh)
    keys_year = list(dict.fromkeys(["TP1", "TP2", "HALF"] + [k for v in top.values() for k in v[3:5]]))
    ytab = yearly_table(res_default, grid, start, bench_v, keys_year)
    rule_tab = pd.DataFrame([{"類別": r.group, "代號": r.key, "出場方式": r.label,
                              "持有上限（日／週）": f"{r.max_hold('1d')}／{r.max_hold('1w')}",
                              "停利": r.engine_exit or r.target or (f"50% 在 {r.partial}" if r.partial else "無"),
                              "移動停損": (f"ATR×{r.atr_k:g}" if r.atr_k else "") + ("前低 N" if r.chandelier else ""),
                              "觸發": r.trigger if (r.atr_k or r.chandelier) else "",
                              "保本": f"收盤 ≥ +{r.be_R:g}R" if r.be_R else "",
                              "時間停損": f"日 {X.TIME_N['1d']}／週 {X.TIME_N['1w']} 根內收盤未達 +{X.TIME_R}R"
                              if r.time_stop else ""} for r in X.RULES])

    bt = bm["tw"]
    notes = [
        ("用途", "D43：用 D40 的頭肩底 A（突破確認）進場訊號與 K 線快取，比較不同出場／停損管理。未重跑偵測；未改網站、掃描器、"
                 "通知、workflow。回測只描述過去資料，不是投資建議。"),
        ("對象", "台股頭肩底 A 1d、1w；加密頭肩底 A 1d。進場＝訊號根 t 收盤價，初始停損＝RS 低點（訊號值），TP1／TP2＝訊號值。"
                 "同序列×同出場規則一次一倉（訊號 t ≤ 上一筆最後出場根即略過，同 D40）。高勝率篩選＝頸線 N2/N1−1 在 −3%～+20% 且"
                 "進場價 ≥ 突破當根頸線價×1.02，**先篩選再套一次一倉**（D42 是先一次一倉再篩選，所以筆數略不同）。"),
        ("出場規則（共通）", "逐根模擬 t+1 起，每根要用的停損水準只用到前一根為止的資料。初始停損（及保本後的進場價）一律盤中觸發："
                         "low ≤ 停損即出場，以 min(開盤, 停損) 成交（跳空以開盤價）。同根同時碰停損與停利→算停損。"
                         "移動停損：候選＝進場後（含進場根）到前一根的最高收盤 − k×ATR14（前一根的 Wilder ATR），或前 N 根最低價（日 10、週 5）；"
                         "取歷史最大值（只上移）。「收盤觸發」：收盤 < 移動停損→以收盤出場（初始停損仍盤中觸發，避免單筆虧損超過 1R 太多）；"
                         "「盤中觸發」：移動停損與初始停損合成一個盤中停損。保本：某根收盤 ≥ 進場＋1R（以收盤判定，避免盤中先後順序不明）"
                         "，下一根起停損＝max(初始, 進場價)，同時才開始 ATR×3 移動。半部位：50% 在較近目標（= min(TP1,TP2)，同 D40 半半的第一目標）或 +2R，"
                         "另 50%（以及到達目標之前的整個部位）用 ATR×3 移動停損。時間停損：進場後日 20／週 8 根內收盤從未 ≥ 進場＋0.5R → 第 20／8 根收盤出場。"
                         "持有上限：D40 日 120、週 52 根；「持有上限放寬」版本日 250、週 104 根。到資料結束仍持有＝open，不列入統計（資金曲線照市值計）。"),
        ("成本", "同 D40：台股買 0.1425%、賣 0.1425%＋證交稅 0.3%；加密每邊 0.1%。分批出場每批各自扣賣出成本。"),
        ("統計", "勝率＝淨報酬 > 0 的比例；達 +1R＝實現 R ≥ 1 的比例；R＝淨報酬 ÷（進場到初始停損距離%），R 類統計排除停損距離 < 0.5% 的交易（同 D40）。"
                 "真實 RR 比＝平均賺 R ÷ 平均賠 R（只看 R>0 與 R<0 的交易）。最大連續虧損＝全部交易依進場時間排序。"
                 "期間依進場年份分 2016–2022、2023–2026（加密日 K 自 2018-08 起）。"),
        ("基準", f"每筆的同期基準報酬（D42 方法）：台股 0050.TW 還原權息 adjclose（{bt['first'].date()}～{bt['last'].date()}），"
                 "加密 BTCUSDT 1d；進場那根收盤時間到各批出場那根收盤時間（as-of），扣基準成本（0050 買 0.1425%、賣 0.1425%＋0.1%；BTC 每邊 0.1%），"
                 "分批出場按比例加權。超額＝策略淨報酬 − 基準報酬；單樣本 t 檢定雙尾 p（交易不獨立，p 偏樂觀）。"),
        ("資金曲線", f"每個組合一個帳戶，日網格＝基準交易日（台股 0050、加密 BTC），起點 台股 {SIM_START['tw']}、加密 {SIM_START['crypto']}，"
                     f"初始 100% 在基準。每筆進場以「當日帳戶淨值 × 每筆風險（預設 {100 * DEFAULT_RISK:g}%）÷ 停損距離%」為部位金額，"
                     f"不超過閒置資金（不融資）；同時最多 {DEFAULT_M} 筆，滿倉時略過；同日多筆依進場時間、週期、代號排序（另以 {args.seeds} 組隨機排序看離散）。"
                     "閒置資金放基準（台股 0050 還原、加密 BTC），每次基準與部位間移轉扣基準買賣成本；另有「現金 0%」版本。"
                     "持倉每日以該標的日 K 收盤（as-of）計市值；週線交易的出場在該週最後一個交易日認列。年化報酬、最大回撤、夏普（無風險利率 0，"
                     "以網格每年天數年化）；分段數字是同一條曲線切片（2023-01-01 前後），不是重新起算帳戶。比較對象＝同期間 100% 持有基準。"),
        ("敏感度", f"每個組合的 D40 三個基準＋隨機排序中位數最高的 5 種出場：同時最多 {SENS_M} 筆 × 每筆風險 {[100 * x for x in SENS_RISK]}%、"
                   f"閒置資金放現金 0%、以及剔除該出場規則全部交易中淨報酬最高的 {DROPS} 筆後重跑（看結果是否靠少數極端大賺）。"),
        ("D40 重現", f"本次 TP1／TP2／半半 vs results/{args.tag}/trades_all.csv 逐筆比對：{base_info}"),
        ("限制", "倖存者偏差（宇宙為目前上市／交易標的）；策略標的 K 線未還原權息（基準有）；無滑價、漲跌停、流動性；多重比較未校正"
                 "（約 39 種出場×多個子集合）；最佳規則為事後挑選，需前進測試；資金曲線的部位排序、M、風險比例皆為假設。"),
        ("執行", f"產生 {time.strftime('%Y-%m-%d %H:%M')}，逐筆模擬 {t_sim - t0:.0f} 秒，全部 {time.time() - t0:.0f} 秒。"),
    ]
    out = OUTPUT_DIR / f"出場方式比較_{time.strftime('%Y%m%d')}.xlsx"
    write_excel(out, {"出場規則定義": rule_tab, "逐筆統計": st, "資金曲線": p_def, "資金曲線敏感度": p_sens,
                      "隨機排序離散": seed_tab, "年度報酬": ytab, "D40重現": base_tab, "略過統計": sk_df}, notes)
    _log(f"[exits] wrote {out}")
    with open(OUT_DIR / "summary.md", "w", encoding="utf-8") as fh:
        fh.write(f"baseline {base_info}\n\n# stats\n\n" + md_table(st, list(st.columns))
                 + "\n\n# equity default\n\n" + md_table(p_def, list(p_def.columns))
                 + "\n\n# seeds\n\n" + md_table(seed_tab, list(seed_tab.columns))
                 + "\n\n# sensitivity\n\n" + md_table(p_sens, list(p_sens.columns))
                 + "\n\n# yearly\n\n" + md_table(ytab, list(ytab.columns)) + "\n")
    _log(f"[exits] done in {time.time() - t0:.0f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
