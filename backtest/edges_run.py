"""D44: market regime switch, volatility-targeted sizing and relative-strength ranking on the D43 equity curve
(TW IHS A 1d+1w all / high-win subset, crypto IHS A 1d), 2017-11 (crypto 2019-09) .. 2021 training,
2022 .. 2026 frozen test.

  python -m backtest.edges_run [--workers 15] [--seeds 20]

Inputs (no detector / exit re-run): results/exits/trades.csv.gz (D43 trades), the 1d kline cache of every symbol
with a D40 signal file, data/benchmark/0050.TW_1d.csv (adjclose) and the cached BTCUSDT 1d.
Outputs (not in git): results/edges/{summary.md,run.log} and
C:\\trading-data\\ihs-scanner-v2\\output\\三方法測試_<date>.xlsx. Pure logic lives in backtest/edges.py.
"""
from __future__ import annotations

import argparse
import math
import multiprocessing as mp
import sys
import time

import numpy as np
import pandas as pd

from backtest import data as D
from backtest import edges as G
from backtest import engine as E
from backtest import exits as X
from backtest import followup as F
from backtest.exits_run import build_sim_trades, unpack
from backtest.followup_run import load_benchmarks, md_table
from backtest.report import OUTPUT_DIR, write_excel

OUT_DIR = D.RESULTS_DIR / "edges"
EXITS_TRADES = D.RESULTS_DIR / "exits" / "trades.csv.gz"
PORTS = [("台股 1d+1w 全部", "tw"), ("台股 1d+1w 高勝率篩選", "tw"), ("加密 1d 全部", "crypto")]
RULES = ("TP2", "ATR3_C", "TP1")
RULE_LABEL = {"TP2": "TP2", "ATR3_C": "ATR×3 收盤移動停損", "TP1": "TP1（對照）"}
TRAIN_START = {"tw": "2017-11-01", "crypto": "2019-09-01"}   # every feature (52-week high) exists from here
TEST_START = "2022-01-01"
LOOK = {"tw": {"rs": 126, "high": 252, "vol": 20, "ann": 252.0},
        "crypto": {"rs": 182, "high": 365, "vol": 20, "ann": 365.0}}
SMA_N, FABER_M = 200, 10
VOL_FLOOR, VOL_MAX_W, ACCT_BAND = 0.10, 0.40, 0.10
M, RISK = 10, 0.01
DROP = 5

BASE = {"switch": None, "off": "hold", "idle": "bench", "sizing": "fixed", "acct": False, "order": "random",
        "filt": None, "no_trades": False}


def cfg(**kw) -> dict:
    return {**BASE, **kw}


def candidates() -> list[tuple[str, str, dict]]:
    """(method, name, config): every candidate evaluated in the training period (all listed in the report)."""
    out = []
    for sw in ("sma200", "faber10"):
        for off in ("hold", "exit"):
            for idle in ("bench", "cash", "switch"):
                out.append(("1 大盤開關", f"{sw}|{off}|{idle}", cfg(switch=sw, off=off, idle=idle)))
    out += [("2 波動度部位", "vol|-", cfg(sizing="vol")), ("2 波動度部位", "fixed|acct", cfg(acct=True)),
            ("2 波動度部位", "vol|acct", cfg(sizing="vol", acct=True))]
    for o in ("rs", "near", "combo"):
        for f in (None, 0.5, 0.3):
            out.append(("3 RS排序", f"{o}|{'all' if f is None else f'top{int(f * 100)}'}", cfg(order=o, filt=f)))
    return out


REFS = [("0 對照", "D43", cfg()), ("0 對照", "D43|cash", cfg(idle="cash"))]
CONTROLS = [("0 對照", "bench|sma200", cfg(switch="sma200", idle="switch", no_trades=True)),
            ("0 對照", "bench|faber10", cfg(switch="faber10", idle="switch", no_trades=True)),
            ("0 對照", "bench|acct", cfg(acct=True, no_trades=True))]

NAME_LABEL = {
    "D43": "D43 原方案（固定 1% 風險、隨機同日排序、閒置放基準）", "D43|cash": "D43，閒置資金放現金",
    "bench|sma200": "只持有基準＋200 日線開關（線下轉現金）", "bench|faber10": "只持有基準＋10 月線開關（線下轉現金）",
    "bench|acct": "只持有基準＋帳戶波動度目標",
}
SW_L = {"sma200": "200 日線", "faber10": "10 月線"}
OFF_L = {"hold": "跌破只停新倉", "exit": "跌破全部出場"}
IDLE_L = {"bench": "閒置放基準", "cash": "閒置放現金", "switch": "閒置：線上放基準、線下現金"}
ORD_L = {"rs": "(a) 6 月相對強弱", "near": "(b) 距 52 週高", "combo": "(c) 綜合"}


def label(name: str, c: dict) -> str:
    if name in NAME_LABEL:
        return NAME_LABEL[name]
    parts = []
    if c["switch"]:
        parts.append(f"{SW_L[c['switch']]}開關、{OFF_L[c['off']]}、{IDLE_L[c['idle']]}")
    elif c["idle"] == "cash":
        parts.append("閒置放現金")
    if c["sizing"] == "vol":
        parts.append("每筆依 20 日波動度定部位")
    if c["acct"]:
        parts.append("帳戶波動度目標（依基準 20 日波動）")
    if c["order"] != "random":
        parts.append(f"同日依{ORD_L[c['order']]}排序" + ("" if c["filt"] is None else f"、只做前 {int(c['filt'] * 100)}%"))
    return "；".join(parts) or "D43 原方案"


def _log(text: str) -> None:
    line = f"{time.strftime('%H:%M:%S')} {text}"
    print(line, flush=True)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(OUT_DIR / "run.log", "a", encoding="utf-8") as fh:
        fh.write(line + "\n")


# ------------------------------------------------------------------ data

def px_worker(task: tuple) -> tuple[str, str, str, np.ndarray | None]:
    market, source, symbol, grid_ns = task
    p = D.cache_file(market, source, symbol, "1d")
    if not p.exists():
        return market, source, symbol, None
    df = D.load_frame(p)
    if not len(df):
        return market, source, symbol, None
    ct = df["close_time"].astype("datetime64[ns, UTC]").astype("int64").to_numpy()
    return market, source, symbol, F.asof_values(ct, df["close"].to_numpy(float), grid_ns)


def features(px: np.ndarray, bench: np.ndarray, market: str) -> dict[str, np.ndarray]:
    lk = LOOK[market]
    rs = G.rel_strength(px, bench, lk["rs"])
    nh = G.near_high(px, lk["high"])
    p_rs, p_nh = G.xs_percentile(rs), G.xs_percentile(nh)
    combo = np.where(np.isfinite(p_rs) & np.isfinite(p_nh), (p_rs + p_nh) / 2.0, np.nan)
    return {"rs": rs, "near": nh, "p_rs": p_rs, "p_near": p_nh, "combo": combo, "p_combo": G.xs_percentile(combo),
            "vol": G.realized_vol(px, lk["vol"], lk["ann"])}


# ------------------------------------------------------------------ simulation (pool globals)

_G: dict = {}


def _init(state: dict) -> None:
    _G.update(state)


def _trades(pname: str, rule: str) -> list[X.SimTrade]:
    cache = _G.setdefault("_unpacked", {})
    if (pname, rule) not in cache:
        if len(cache) > 3:
            cache.clear()
        cache[(pname, rule)] = unpack(_G["sim"][(pname, rule)])
    return cache[(pname, rule)]


def run_one(pname: str, rule: str, c: dict, period: str, seed: int | None, drop: int) -> dict:
    market = _G["port_market"][pname]
    lo, hi = _G["bounds"][market][period]
    px = _G["px"][market][:, :hi + 1]
    bench = _G["bench_v"][market][:hi + 1]
    reg = _G["regime"][market]
    mult_full = _G["acct_mult"][market]
    allow = None
    if c["switch"]:
        allow = np.nan_to_num(reg[c["switch"]], nan=1.0)[:hi + 1] > 0.5
    mult = mult_full[:hi + 1] if c["acct"] else None
    idle_w = np.ones(hi + 1)
    if c["idle"] == "switch":
        idle_w = allow.astype(float)
    if c["acct"]:
        idle_w = idle_w * mult
    use_bench = c["idle"] != "cash"
    if c["no_trades"]:
        trades, arr = [], None
    else:
        arr = _G["sim"][(pname, rule)]
        trades = _trades(pname, rule)
    keep = np.ones(len(trades), dtype=bool)
    if arr is not None and c["filt"] is not None:
        p = arr["p_" + c["order"]]
        keep &= np.isfinite(p) & (p >= 1.0 - c["filt"])
    # random same-day order: one permutation over the whole list, restricted to the kept trades, so leaving out
    # a few trades (robustness) does not reshuffle everybody else
    full_rank = np.random.default_rng(seed).permutation(len(trades)) if seed is not None else None

    def simulate(keep_mask: np.ndarray):
        ids = np.flatnonzero(keep_mask)
        sub = [trades[k] for k in ids]
        weights = None
        if arr is not None and c["sizing"] == "vol":
            weights = arr["w_vol"][ids]
        order = None
        if arr is not None and len(sub):
            if c["order"] == "random":
                if full_rank is not None:
                    order = np.argsort(full_rank[ids], kind="stable")
            else:
                order = G.score_order(arr[c["order"]][ids], arr["d0"][ids])
        r = G.equity_ext(px, sub, bench=bench if use_bench else None, risk=RISK, max_pos=M,
                         costs=E.COSTS[market], bench_costs=F.BENCH_COSTS[market], start=lo, order=order,
                         weights=weights, max_w=VOL_MAX_W if weights is not None else math.inf, allow=allow,
                         exit_on_off=c["off"] == "exit", idle_w=idle_w if use_bench else None,
                         band=ACCT_BAND if c["acct"] else 0.0, risk_mult=mult)
        return r, ids

    res, ids = simulate(keep)
    if arr is not None and drop:
        # leave out the `drop` best trades (net return) among those this configuration actually took in the period
        took = np.array([ids[k] for k, _w in res["taken_idx"]], dtype=np.int64)
        took = took[(arr["d0"][took] >= lo) & (arr["d0"][took] <= hi) & np.isfinite(arr["net"][took])]
        best = took[np.argsort(-arr["net"][took])[:drop]]
        keep = keep.copy()
        keep[best] = False
        res, ids = simulate(keep)
    days = _G["grid"][market][:hi + 1]
    pf = X.perf(res["equity"], days, lo, hi)
    r = G.daily_returns(res["equity"], lo, hi)
    rb = G.daily_returns(_G["bench_eq"][market], lo, hi)
    mo = G.moments(r)
    ma = G.moments(r - rb[: r.size] if rb.size >= r.size else r)
    return {"portfolio": pname, "rule": rule, "seed": seed, "period": period, "drop": drop,
            "cagr": pf["cagr"], "mdd": pf["mdd"], "sharpe": pf["sharpe"], "calmar": G.calmar(pf["cagr"], pf["mdd"]),
            "exposure": float(np.nanmean(res["exposure"][lo:hi + 1])),
            "bench_frac": float(np.nanmean(res["bench_frac"][lo:hi + 1])),
            "taken": res["taken"], "skipped_regime": res["skipped_regime"], "forced": res["forced_exits"],
            "n_trades_list": int(((arr["d0"][ids] >= lo) & (arr["d0"][ids] <= hi)).sum()) if arr is not None else 0,
            "sr_d": mo["sr"], "skew": mo["skew"], "kurt": mo["kurt"], "n_d": mo["n"],
            "act_sr_d": ma["sr"], "act_skew": ma["skew"], "act_kurt": ma["kurt"], "act_n": ma["n"]}


def sim_task(task: tuple) -> dict:
    method, name, c, pname, rule, period, seed, drop = task
    out = run_one(pname, rule, c, period, seed, drop)
    out.update({"method": method, "name": name})
    return out


def tasks_for(items, ports_rules, periods, seeds: int, drop: int = 0) -> list[tuple]:
    out = []
    for method, name, c in items:
        sd = list(range(seeds)) if (c["order"] == "random" and not c["no_trades"]) else [None]
        for pname, rule in ports_rules:
            for per in periods:
                for s in sd:
                    out.append((method, name, c, pname, rule, per, s, drop))
    return out


# ------------------------------------------------------------------ aggregation

METRICS = ("cagr", "mdd", "sharpe", "calmar", "exposure", "bench_frac", "taken", "skipped_regime", "forced")


def aggregate(res: list[dict], bench_perf: dict) -> pd.DataFrame:
    df = pd.DataFrame(res)
    rows = []
    for (method, name, pname, rule, per, drop), g in df.groupby(["method", "name", "portfolio", "rule", "period", "drop"],
                                                               sort=False, dropna=False):
        g = g.reset_index(drop=True)
        med = {m: g[m].median() for m in METRICS}
        k = int(np.argsort(g["sharpe"].fillna(-9).to_numpy())[len(g) // 2])  # the median-Sharpe seed
        bp = bench_perf[(pname, per)]
        rows.append({"method": method, "name": name, "portfolio": pname, "rule": rule, "period": per, "drop": drop,
                     "n_seeds": len(g), **med, "cagr_min": g["cagr"].min(), "cagr_max": g["cagr"].max(),
                     "beat_bench_pct": 100 * (g["cagr"] > bp["cagr"]).mean(),
                     **{c: g.loc[k, c] for c in ("sr_d", "skew", "kurt", "n_d", "act_sr_d", "act_skew", "act_kurt",
                                                 "act_n")}})
    return pd.DataFrame(rows)


def psr_cols(a: pd.DataFrame) -> pd.DataFrame:
    a = a.copy()
    a["psr0"] = [G.psr(s, int(n), k, u) for s, n, k, u in zip(a["sr_d"], a["n_d"], a["skew"], a["kurt"])]
    a["psr_act"] = [G.psr(s, int(n), k, u) for s, n, k, u in zip(a["act_sr_d"], a["act_n"], a["act_skew"],
                                                                     a["act_kurt"])]
    return a


def _p(v):
    return None if v is None or (isinstance(v, float) and not math.isfinite(v)) else 100 * v


def wide(a: pd.DataFrame, bench_perf: dict, d43: dict, methods: list[str], names_cfg: dict) -> pd.DataFrame:
    rows = []
    for (method, name, pname, rule), g in a[a["drop"] == 0].groupby(["method", "name", "portfolio", "rule"],
                                                                     sort=False):
        if method not in methods:
            continue
        rec = {"組合": pname, "出場": RULE_LABEL.get(rule, rule) if not names_cfg[name]["no_trades"] else "—",
               "方法": method, "代號": name, "方案": label(name, names_cfg[name])}
        for per, tag in (("train", "訓練"), ("test", "測試"), ("full", "全期")):
            r = g[g["period"] == per]
            if not len(r):
                continue
            r = r.iloc[0]
            bp = bench_perf[(pname, per)]
            dd = d43.get((pname, rule, per))
            rec.update({f"[{tag}] 年化%": _p(r["cagr"]), f"[{tag}] 最大回撤%": _p(r["mdd"]),
                        f"[{tag}] 夏普": r["sharpe"], f"[{tag}] 卡瑪": r["calmar"],
                        f"[{tag}] 資金使用率%（持股/淨值）": _p(r["exposure"]),
                        f"[{tag}] 基準部位%": _p(r["bench_frac"]), f"[{tag}] 進場筆數": r["taken"],
                        f"[{tag}] 年化% 種子最小": _p(r["cagr_min"]) if r["n_seeds"] > 1 else None,
                        f"[{tag}] 年化% 種子最大": _p(r["cagr_max"]) if r["n_seeds"] > 1 else None,
                        f"[{tag}] 贏基準年化的種子%": r["beat_bench_pct"],
                        f"[{tag}] 年化差 vs 基準": _p(r["cagr"] - bp["cagr"]) if r["cagr"] is not None else None,
                        f"[{tag}] 夏普差 vs 基準": r["sharpe"] - bp["sharpe"] if r["sharpe"] is not None else None,
                        f"[{tag}] 年化差 vs D43": _p(r["cagr"] - dd["cagr"]) if dd is not None else None,
                        f"[{tag}] 夏普差 vs D43": (r["sharpe"] - dd["sharpe"]) if dd is not None else None,
                        f"[{tag}] PSR(夏普>0)": r.get("psr0"), f"[{tag}] PSR(超額日報酬夏普>0)": r.get("psr_act")})
        rows.append(rec)
    return pd.DataFrame(rows)


def bench_rows(bench_perf: dict) -> pd.DataFrame:
    rows = []
    for pname, _m in PORTS:
        rec = {"組合": pname, "方案": "100% 持有基準（台股 0050 還原權息／加密 BTC）"}
        for per, tag in (("train", "訓練"), ("test", "測試"), ("full", "全期")):
            bp = bench_perf[(pname, per)]
            rec.update({f"[{tag}] 年化%": _p(bp["cagr"]), f"[{tag}] 最大回撤%": _p(bp["mdd"]),
                        f"[{tag}] 夏普": bp["sharpe"], f"[{tag}] 卡瑪": G.calmar(bp["cagr"], bp["mdd"]),
                        f"[{tag}] 期間": bp["span"]})
        rows.append(rec)
    return pd.DataFrame(rows)


# ------------------------------------------------------------------ checks

def causal_checks(px: dict, bench: dict, grid: dict, feats: dict, bounds: dict) -> pd.DataFrame:
    """Recompute every feature / regime on data truncated at day d and compare with the full-data value at d."""
    rows = []
    for market in px:
        n = px[market].shape[1]
        for d in sorted({bounds[market]["train"][0] + 5, bounds[market]["test"][0] + 3, n - 40}):
            f2 = features(px[market][:, :d + 1], bench[market][:d + 1], market)
            for k in feats[market]:
                a, b = feats[market][k][:, d], f2[k][:, d]
                same = bool(np.array_equal(np.isnan(a), np.isnan(b)) and np.allclose(a[~np.isnan(a)], b[~np.isnan(b)],
                                                                                       rtol=0, atol=1e-12))
                rows.append({"市場": market, "截斷日": str(pd.to_datetime(grid[market][d], utc=True).date()),
                             "項目": k, "一致": same, "有值的標的數": int(np.isfinite(a).sum())})
            for k, fn in (("sma200", lambda v, t: G.sma_regime(v, SMA_N)),
                          ("faber10", lambda v, t: G.faber_regime(v, t, FABER_M))):
                a = fn(bench[market], grid[market])[d]
                b = fn(bench[market][:d + 1], grid[market][:d + 1])[d]
                rows.append({"市場": market, "截斷日": str(pd.to_datetime(grid[market][d], utc=True).date()),
                             "項目": k, "一致": bool((a == b) or (np.isnan(a) and np.isnan(b))), "有值的標的數": None})
    return pd.DataFrame(rows)


# ------------------------------------------------------------------ main

def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--tag", default="main")
    p.add_argument("--workers", type=int, default=15)
    p.add_argument("--seeds", type=int, default=20)
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
    idx = lambda m, s: int(np.searchsorted(grid[m], pd.Timestamp(s, tz="UTC").value))
    bounds = {m: {"train": (idx(m, TRAIN_START[m]), idx(m, TEST_START) - 1),
                  "test": (idx(m, TEST_START), len(grid[m]) - 1),
                  "full": (idx(m, TRAIN_START[m]), len(grid[m]) - 1)} for m in grid}

    # ---- price matrices (same universe as D43: every symbol with a D40 signal file)
    tasks = []
    for market in ("tw", "crypto"):
        for p in sorted((D.RESULTS_DIR / args.tag / market / "signals").glob("*.csv")):
            source, symbol = p.stem.split("_", 1)
            tasks.append((market, source, symbol, grid[market]))
    rows = {"tw": [], "crypto": []}
    sym_row = {"tw": {}, "crypto": {}}
    with mp.Pool(args.workers) as pool:
        for market, source, symbol, v in pool.imap_unordered(px_worker, tasks, chunksize=8):
            if v is not None:
                sym_row[market][(source, str(symbol))] = len(rows[market])
                rows[market].append(v)
    px = {m: np.vstack(rows[m]) for m in rows}
    _log(f"[edges] price matrices tw {px['tw'].shape} crypto {px['crypto'].shape} ({time.time() - t0:.0f}s)")

    feats = {m: features(px[m], bench_v[m], m) for m in px}
    regime = {m: {"sma200": G.sma_regime(bench_v[m], SMA_N), "faber10": G.faber_regime(bench_v[m], grid[m], FABER_M)}
              for m in px}
    bvol = {m: G.realized_vol(bench_v[m], LOOK[m]["vol"], LOOK[m]["ann"]) for m in px}
    sig_star = {m: float(np.nanmedian(bvol[m][bounds[m]["train"][0]:bounds[m]["train"][1] + 1])) for m in px}
    acct_mult = {m: np.where(np.isfinite(bvol[m]), np.minimum(1.0, sig_star[m] / bvol[m]), 1.0) for m in px}
    bench_eq = {m: bench_v[m] / bench_v[m][0] for m in px}
    _log(f"[edges] features done ({time.time() - t0:.0f}s); bench vol target {sig_star}")

    # ---- D43 trades
    use = ["market", "source", "symbol", "timeframe", "subset", "rule", "entry_price", "risk_pct", "net_ret", "is_open",
           "entry_ct", "n_legs", "leg1_frac", "leg1_ct", "leg1_px", "leg2_frac", "leg2_ct", "leg2_px"]
    tr = pd.read_csv(EXITS_TRADES, encoding="utf-8", usecols=use, low_memory=False)
    tr = tr[tr["rule"].isin(RULES)].copy()
    tr["is_open"] = tr["is_open"].astype(str).str.lower().eq("true")
    sim_all, port_market_all = build_sim_trades(tr, grid, sym_row)
    port_market = {p: m for p, m in PORTS}
    sim, calib = {}, []
    for pname, market in PORTS:
        f = feats[market]
        lo, hi = bounds[market]["train"]
        for rule in RULES:
            arr = dict(sim_all[(pname, rule)])
            s, d0 = arr["sym"], arr["d0"]
            for k in ("rs", "near", "combo", "p_rs", "p_near", "p_combo", "vol"):
                arr[k] = f[k][s, d0]
            w43 = 0.01 / arr["risk"]
            intr = (d0 >= lo) & (d0 <= hi)
            sig = np.maximum(arr["vol"], VOL_FLOOR)
            c_vol = float(np.nanmedian(w43[intr]) * np.nanmedian(sig[intr]))
            arr["w_vol"] = G.vol_weights(arr["vol"], c_vol, VOL_FLOOR, VOL_MAX_W)
            sim[(pname, rule)] = arr
            calib.append({"組合": pname, "出場": rule, "訓練期交易數": int(intr.sum()),
                          "D43 部位% 中位數（訓練）": 100 * float(np.nanmedian(w43[intr])),
                          "20日年化波動% 中位數（訓練）": 100 * float(np.nanmedian(arr["vol"][intr])),
                          "波動度部位常數 c": c_vol,
                          "波動度部位% 中位數（訓練）": 100 * float(np.nanmedian(arr["w_vol"][intr])),
                          "波動度部位% 中位數（測試）": 100 * float(np.nanmedian(arr["w_vol"][d0 > hi])),
                          "RS 缺值筆數": int((~np.isfinite(arr["p_rs"])).sum()),
                          "52週高 缺值筆數": int((~np.isfinite(arr["p_near"])).sum()),
                          "波動度缺值筆數": int((~np.isfinite(arr["vol"])).sum())})
    calib_df = pd.DataFrame(calib)

    # ---- causal checks + D43 reproduction
    chk = causal_checks(px, bench_v, grid, feats, bounds)
    _log(f"[edges] truncation checks: {int(chk['一致'].sum())}/{len(chk)} equal")
    state = {"px": px, "grid": grid, "bench_v": bench_v, "bench_eq": bench_eq, "regime": regime,
             "acct_mult": acct_mult, "bounds": bounds, "sim": sim, "port_market": port_market}
    _init(state)
    rep = []
    for pname, market in PORTS:
        tl = unpack(sim[(pname, "TP2")])
        lo = bounds[market]["full"][0]
        for seed in (None, 0):
            order = None if seed is None else np.random.default_rng(seed).permutation(len(tl))
            a = X.equity_curve(px[market], tl, bench=bench_v[market], risk=RISK, max_pos=M, costs=E.COSTS[market],
                               bench_costs=F.BENCH_COSTS[market], start=lo, order=order)["equity"]
            b = G.equity_ext(px[market], tl, bench=bench_v[market], risk=RISK, max_pos=M, costs=E.COSTS[market],
                             bench_costs=F.BENCH_COSTS[market], start=lo, order=order)["equity"]
            rep.append({"組合": pname, "出場": "TP2", "排序種子": seed,
                        "最大相對差": float(np.nanmax(np.abs(b[lo:] / a[lo:] - 1.0)))})
    rep_df = pd.DataFrame(rep)
    _log(f"[edges] D43 reproduction max rel diff {rep_df['最大相對差'].max():.2e}")

    # ---- benchmark per period
    bench_perf = {}
    for pname, market in PORTS:
        for per, (lo, hi) in bounds[market].items():
            eq = bench_v[market] / bench_v[market][lo] * (1 - F.BENCH_COSTS[market]["buy"])
            pf = X.perf(eq, grid[market], lo, hi)
            pf["span"] = (f"{pd.to_datetime(grid[market][lo], utc=True).date()}～"
                          f"{pd.to_datetime(grid[market][hi], utc=True).date()}")
            bench_perf[(pname, per)] = pf

    pr = [(p, r) for p, _m in PORTS for r in RULES]
    cands = candidates()
    phase1 = (tasks_for(REFS + cands, pr, ("train", "test"), args.seeds)
              + tasks_for(CONTROLS, [(p, "-") for p, _m in PORTS], ("train", "test", "full"), args.seeds))
    _log(f"[edges] phase 1: {len(phase1)} equity runs")
    with mp.Pool(args.workers, initializer=_init, initargs=(state,)) as pool:
        res1 = pool.map(sim_task, phase1, chunksize=4)
        a1 = aggregate(res1, bench_perf)
        _log(f"[edges] phase 1 done ({time.time() - t0:.0f}s)")

        # ---- training-only selection (median training Sharpe), per portfolio x exit
        sel_rows, combos = [], []
        tr1 = a1[(a1["period"] == "train") & (a1["drop"] == 0)]
        for pname, rule in pr:
            g = tr1[(tr1["portfolio"] == pname) & (tr1["rule"] == rule)]
            base_sh = float(g.loc[g["name"] == "D43", "sharpe"].iloc[0])
            merged = dict(BASE)
            picks = {}
            for method in ("1 大盤開關", "2 波動度部位", "3 RS排序"):
                gm = g[g["method"] == method].sort_values("sharpe", ascending=False)
                best = gm.iloc[0]
                c = dict(next(c for m_, n_, c in cands if n_ == best["name"]))
                use_it = float(best["sharpe"]) > base_sh
                picks[method] = (best["name"], use_it)
                if use_it:
                    keys = {"1 大盤開關": ("switch", "off", "idle"), "2 波動度部位": ("sizing", "acct"),
                            "3 RS排序": ("order", "filt")}[method]
                    merged.update({k: c[k] for k in keys})
                sel_rows.append({"組合": pname, "出場": RULE_LABEL[rule], "方法": method, "候選數": len(gm),
                                 "訓練夏普最佳": best["name"], "方案": label(best["name"], c),
                                 "最佳 訓練夏普": best["sharpe"], "D43 訓練夏普": base_sh,
                                 "最佳 訓練年化%": _p(best["cagr"]), "是否採用（訓練夏普 > D43）": "是" if use_it else "否"})
            combos.append((pname, rule, merged, picks))
        combo_items = {}
        for pname, rule, merged, _p_ in combos:
            combo_items.setdefault(tuple(sorted((k, str(v)) for k, v in merged.items())), (merged, []))[1].append(
                (pname, rule))
        phase2 = []
        for (merged, prs) in combo_items.values():
            nm = "combo:" + label("", merged)
            phase2 += tasks_for([("4 組合", nm, merged)], prs, ("train", "test", "full"), args.seeds)
        phase2 += tasks_for([REFS[0]], pr, ("full",), args.seeds)
        # robustness: leave out the 5 best trades of the period, for D43, each method's training best and the combo
        rob = []
        for pname, rule, merged, picks in combos:
            items = [REFS[0]] + [next(x for x in cands if x[1] == picks[m][0]) for m in picks]
            items.append(("4 組合", "combo:" + label("", merged), merged))
            rob += tasks_for(items, [(pname, rule)], ("train", "test"), args.seeds, drop=DROP)
            # diagnostic: the same configuration with the idle money in 0% cash (is it the stocks or the benchmark?)
            diag = [("5 診斷", n + "|閒置現金", {**c, "idle": "cash"}) for _m, n, c in items
                    if n != "D43" and c["idle"] == "bench"]
            rob += tasks_for(diag, [(pname, rule)], ("train", "test"), args.seeds)
        _log(f"[edges] phase 2: {len(phase2)} + robustness {len(rob)} runs")
        res2 = pool.map(sim_task, phase2 + rob, chunksize=4)
    a = psr_cols(pd.concat([a1, aggregate(res2, bench_perf)], ignore_index=True))
    names_cfg = {n: c for _m, n, c in REFS + cands + CONTROLS}
    for (merged, _prs) in combo_items.values():
        names_cfg["combo:" + label("", merged)] = merged
    for n in a.loc[a["method"] == "5 診斷", "name"].unique():
        base_n = n[: -len("|閒置現金")]
        names_cfg[n] = {**names_cfg[base_n], "idle": "cash"}
    d43 = {(r["portfolio"], r["rule"], r["period"]): r for _, r in a[(a["name"] == "D43") & (a["drop"] == 0)].iterrows()}
    # frozen test of each method's training choice (whether or not it beat D43 in training)
    lab2rule = {v: k for k, v in RULE_LABEL.items()}
    for rec in sel_rows:
        rule = lab2rule[rec["出場"]]
        te = a[(a["portfolio"] == rec["組合"]) & (a["rule"] == rule) & (a["name"] == rec["訓練夏普最佳"])
               & (a["period"] == "test") & (a["drop"] == 0)].iloc[0]
        dte, bp = d43[(rec["組合"], rule, "test")], bench_perf[(rec["組合"], "test")]
        rec.update({"測試 年化%": _p(te["cagr"]), "測試 最大回撤%": _p(te["mdd"]), "測試 夏普": te["sharpe"],
                    "測試 卡瑪": te["calmar"], "測試 D43 年化%": _p(dte["cagr"]), "測試 D43 夏普": dte["sharpe"],
                    "測試 基準年化%": _p(bp["cagr"]), "測試 基準最大回撤%": _p(bp["mdd"]), "測試 基準夏普": bp["sharpe"],
                    "測試 贏 D43（夏普）": "是" if te["sharpe"] > dte["sharpe"] else "否",
                    "測試 贏基準（年化／夏普）": f"{'是' if te['cagr'] > bp['cagr'] else '否'}／"
                                         f"{'是' if te['sharpe'] > bp['sharpe'] else '否'}"})

    # ---- DSR (training: the best of all candidates of a portfolio x exit; and across all 9 portfolio x exit)
    dsr_rows = []
    trn = a[(a["period"] == "train") & (a["drop"] == 0) & (a["method"] != "0 對照")]
    all_srs = trn["sr_d"].tolist()
    for pname, rule in pr:
        g = trn[(trn["portfolio"] == pname) & (trn["rule"] == rule)]
        best = g.sort_values("sharpe", ascending=False).iloc[0]
        d1 = G.dsr(best["sr_d"], int(best["n_d"]), best["skew"], best["kurt"], g["sr_d"].tolist())
        d2 = G.dsr(best["sr_d"], int(best["n_d"]), best["skew"], best["kurt"], all_srs)
        cmb = a[(a["portfolio"] == pname) & (a["rule"] == rule) & (a["method"] == "4 組合") & (a["drop"] == 0)]
        te = cmb[cmb["period"] == "test"].iloc[0]
        dte = d43[(pname, rule, "test")]
        dsr_rows.append({"組合": pname, "出場": RULE_LABEL[rule], "訓練候選數 N": d1["n_trials"],
                         "訓練最佳方案": label(best["name"], names_cfg[best["name"]]),
                         "訓練最佳 年化夏普": best["sharpe"], "訓練最佳 日夏普": best["sr_d"],
                         "N 次試驗下的期望最大日夏普 SR0": d1["sr0"], "DSR（同組合 N）": d1["dsr"],
                         "DSR（9 組×N 全部試驗）": d2["dsr"], "全部試驗數": d2["n_trials"],
                         "PSR 訓練最佳（夏普>0，未校正）": G.psr(best["sr_d"], int(best["n_d"]), best["skew"], best["kurt"]),
                         "測試 組合方案 年化夏普": te["sharpe"], "測試 PSR(夏普>0)": te["psr0"],
                         "測試 PSR(對基準超額日報酬夏普>0)": te["psr_act"],
                         "測試 D43 PSR(超額>0)": dte["psr_act"] if "psr_act" in dte else None,
                         "測試 基準年化夏普": bench_perf[(pname, "test")]["sharpe"]})
    dsr_df = pd.DataFrame(dsr_rows)

    # ---- robustness table
    rob_rows = []
    for (pname, rule, name), g in a[a["drop"] == DROP].groupby(["portfolio", "rule", "name"], sort=False):
        rec = {"組合": pname, "出場": RULE_LABEL[rule], "方案": label(name, names_cfg[name]), "代號": name}
        for per, tag in (("train", "訓練"), ("test", "測試")):
            r0 = a[(a["portfolio"] == pname) & (a["rule"] == rule) & (a["name"] == name) & (a["period"] == per)
                   & (a["drop"] == 0)]
            r1 = g[g["period"] == per]
            bp = bench_perf[(pname, per)]
            if len(r0) and len(r1):
                rec.update({f"[{tag}] 年化%": _p(r0.iloc[0]["cagr"]), f"[{tag}] 去掉最好5筆 年化%": _p(r1.iloc[0]["cagr"]),
                            f"[{tag}] 夏普": r0.iloc[0]["sharpe"], f"[{tag}] 去掉最好5筆 夏普": r1.iloc[0]["sharpe"],
                            f"[{tag}] 基準年化%": _p(bp["cagr"]), f"[{tag}] 基準夏普": bp["sharpe"]})
            cash_name = "D43|cash" if name == "D43" else name + "|閒置現金"
            r2 = a[(a["portfolio"] == pname) & (a["rule"] == rule) & (a["name"] == cash_name) & (a["period"] == per)
                   & (a["drop"] == 0)]
            if len(r2):
                rec.update({f"[{tag}] 閒置放現金 年化%": _p(r2.iloc[0]["cagr"]),
                            f"[{tag}] 閒置放現金 最大回撤%": _p(r2.iloc[0]["mdd"]),
                            f"[{tag}] 閒置放現金 夏普": r2.iloc[0]["sharpe"],
                            f"[{tag}] 閒置放現金 資金使用率%": _p(r2.iloc[0]["exposure"])})
        rob_rows.append(rec)
    rob_df = pd.DataFrame(rob_rows)

    sheet = lambda ms: wide(a, bench_perf, d43, ms, names_cfg)
    seeds_df = pd.DataFrame(res1 + res2)
    seeds_df = seeds_df[["method", "name", "portfolio", "rule", "period", "drop", "seed", "cagr", "mdd", "sharpe",
                         "calmar", "exposure", "taken", "skipped_regime", "forced"]]
    sel_df = pd.DataFrame(sel_rows)
    lk = LOOK
    notes = [
        ("用途", "D44：在 D43 資金曲線框架上測試 docs/edge-research.md 推薦 1–3（大盤趨勢開關、波動度目標部位、相對強弱排序）及其組合。"
                 "只用 D43 既有交易（results/exits/trades.csv.gz）與 K 線快取，未重跑偵測與出場；未改網站、掃描器、通知、workflow。"
                 "回測只描述過去資料，不是投資建議。"),
        ("對象", "台股頭肩底 A 日＋週（全部、高勝率篩選）；加密頭肩底 A 日線（全部）。出場：TP2、ATR×3 收盤移動停損（D43 代號 ATR3_C）、TP1 對照。"
                 "進場、停損、出場價與 D43 完全相同（同一份交易表）。"),
        ("期間", f"訓練 台股 {bounds['tw']['train']} 對應 {TRAIN_START['tw']}～2021-12-31，加密 {TRAIN_START['crypto']}～2021-12-31；"
                 f"測試 {TEST_START}～資料結束（參數凍結、帳戶重新起算、初始 100% 在基準）。"
                 "訓練期起點比原訂 2016／2018-08 晚：台股個股 K 線快取自 2016-10 起、加密自 2018-08 起，52 週高需滿一年資料，"
                 "為了讓所有方案在同一期間比較，統一從所有特徵都有值的日期開始。「全期」＝訓練起點到資料結束同一條曲線。"),
        ("帳戶（同 D43）", f"日網格＝基準交易日；每筆部位＝淨值×1%÷停損距離（固定風險），最多 {M} 筆，不融資（不超過閒置資金）；"
                         "閒置資金放基準（0050 還原權息／BTC），移轉扣基準成本；台股買 0.1425%、賣 0.1425%＋證交稅 0.3%；ETF 賣另加 0.1%；加密每邊 0.1%。"
                         f"隨機同日排序的方案跑 {args.seeds} 組種子取中位數（另列最小／最大與贏基準比例）；RS 排序方案為確定性排序（不需種子）。"),
        ("方法 1 大盤開關", f"200 日線：基準收盤 > 最近 {SMA_N} 個交易日（含當日）均線。10 月線（Faber）：上一個已結束月份的月底收盤 > 最近 10 個已結束月份月底收盤平均"
                         "（當月尚未結束不算，避免『今天是不是月底』的前視）。開關為否時不開新倉；『跌破全部出場』版本在開關為否的那天以收盤（扣成本）賣出所有持倉。"
                         "閒置資金三種：放基準／放現金／線上放基準線下放現金（這個版本等於基準本身也被擇時）。候選 2×2×3＝12。"
                         "對照：只持有基準＋同開關（不做任何型態交易）。"),
        ("方法 2 波動度", f"(a) 每筆部位＝c ÷ max(個股近 20 日年化波動度, {VOL_FLOOR:.0%})，單筆上限淨值 {VOL_MAX_W:.0%}；"
                        "c 用訓練期交易校準成『波動度部位的中位數＝D43 部位的中位數』，所以平均投入相近、只改變分配（選擇波動度而非停損距離：頭肩底停損是結構位置，不反映標的波動）。"
                        f"(b) 帳戶層級：乘數＝min(1, σ*÷基準近 20 日年化波動)，σ*＝訓練期基準 20 日波動中位數（台股 {sig_star['tw']:.1%}、加密 {sig_star['crypto']:.1%}）；"
                        f"新部位大小與閒置資金中放基準的比例都乘上它（其餘放現金，偏離 > {ACCT_BAND:.0%} 才再平衡），不放大槓桿。候選 3（vol、acct、vol+acct）。"),
        ("方法 3 RS", f"(a) 6 個月相對強弱＝個股 {lk['tw']['rs']} 日（加密 {lk['crypto']['rs']} 日）報酬相對 0050／BTC 同期報酬；"
                    f"(b) 收盤 ÷ 近 {lk['tw']['high']} 日（加密 {lk['crypto']['high']} 日）最高收盤；(c) (a)(b) 全市場百分位的平均。"
                    "全市場百分位＝當天所有有資料的標的（D43 宇宙，台股約 1,975 檔、加密約 1,200 個交易對）中的排名。同日訊號依分數由高到低進場；"
                    "過濾版只做百分位 ≥ 50%（前 50%）或 ≥ 70%（前 30%）。只用進場當日（含）以前的收盤。候選 3×3＝9。"),
        ("選擇規則（只用訓練期）", "每個『組合×出場』在訓練期依夏普（20 種子中位數）選各方法最佳候選；只有在訓練夏普高於 D43 原方案時才採用。"
                              "方法 4 組合＝把三個方法中被採用的設定合在一起（沒被採用的維持 D43），不另外搜尋。測試期參數凍結，只跑一次。"),
        ("穩健性", f"去掉該期間淨報酬最好的 {DROP} 筆交易後重跑；DSR：Bailey & López de Prado 2014，日報酬夏普、偏態、峰態，"
                  "試驗數 N＝該組合×出場的全部候選（再列 9 組合×出場全部試驗的保守版）；PSR(超額)＝策略日報酬減基準日報酬的夏普 > 0 的機率。"),
        ("截斷測試", f"把價格矩陣截到第 d 天重算所有特徵與開關，與全資料在第 d 天的值比較：{int(chk['一致'].sum())}/{len(chk)} 一致"
                   f"（工作表 8）。equity_ext 預設參數重現 D43 equity_curve，最大相對差 {rep_df['最大相對差'].max():.1e}。"),
        ("限制", "倖存者偏差（宇宙為目前上市／交易標的；加密更嚴重）；策略標的 K 線未還原權息；無滑價、漲跌停、流動性；"
                 "被開關、過濾或強制出場略過的交易不會讓同序列後續訊號遞補（D43 的一次一倉交易表是事先決定的）；"
                 "訓練期僅約 4 年（加密約 2.3 年），測試期約 4.8 年，都只有一條歷史路徑。"),
        ("執行", f"產生 {time.strftime('%Y-%m-%d %H:%M')}，共 {len(res1) + len(res2)} 次資金曲線模擬，{time.time() - t0:.0f} 秒。"),
    ]
    out = OUTPUT_DIR / f"三方法測試_{time.strftime('%Y%m%d')}.xlsx"
    sheets = {"0_基準與D43": pd.concat([bench_rows(bench_perf), sheet(["0 對照"])], ignore_index=True),
              "1_大盤開關": sheet(["0 對照", "1 大盤開關"]), "2_波動度部位": sheet(["0 對照", "2 波動度部位"]),
              "3_RS排序": sheet(["0 對照", "3 RS排序"]), "4_組合_選擇": sel_df, "4_組合_結果": sheet(["0 對照", "4 組合"]),
              "5_穩健性_去掉最好5筆": rob_df, "6_DSR與多重比較": dsr_df, "7_參數校準": calib_df,
              "8_截斷與重現檢查": pd.concat([chk, rep_df], ignore_index=True), "9_種子明細": seeds_df}
    write_excel(out, sheets, notes)
    _log(f"[edges] wrote {out}")
    with open(OUT_DIR / "summary.md", "w", encoding="utf-8") as fh:
        for k, v in sheets.items():
            if k == "9_種子明細":
                continue
            fh.write(f"# {k}\n\n" + md_table(v, list(v.columns)) + "\n\n")
    _log(f"[edges] done in {time.time() - t0:.0f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
