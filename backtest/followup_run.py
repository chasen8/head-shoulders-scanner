"""D42 follow-up analyses of the D40 backtest: benchmark + random entry, extra filters, neckline-retest entry.

  python -m backtest.followup_run [--workers 15] [--k-random 5] [--wait-1d 20] [--wait-1w 8] [--offline]
                                  [--max-symbols N] [--tag main]

Inputs (no detector re-run): results/<tag>/trades_all.csv, results/<tag>/<market>/signals/*.csv and the kline
cache data/<market>/<source>/<symbol>_<tf>.csv. Benchmarks: 0050.TW daily from Yahoo (public chart endpoint,
cached to data/benchmark/0050.TW_1d.csv, close = split-adjusted price, adjclose = + dividends) and the cached
BTCUSDT (binance-spot) 1d / 4h.
Outputs (not in git): results/followup/{trades_annotated,retest}.csv, results/followup/summary.md and
C:\\trading-data\\ihs-scanner-v2\\output\\回測延伸分析_<date>.xlsx. Pure logic lives in backtest/followup.py.
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
from backtest import engine as E
from backtest import followup as F
from backtest.report import OUTPUT_DIR, write_excel

OUT_DIR = D.RESULTS_DIR / "followup"
BENCH_DIR = D.DATA_DIR / "benchmark"
TW_BENCH = BENCH_DIR / "0050.TW_1d.csv"
LOOKBACK = {"1d": 250, "1w": 52, "4h": 500}
VOL_N = {"1d": 3, "1w": 1, "4h": 6}
XS = (0.0, 0.01, 0.03)
RETEST_MODES = ("limit", "close_confirm")
PERIODS = (("全期", 0, 9999), ("2016–2022", 2016, 2022), ("2023–2026", 2023, 2026))
MIN_RISK_FOR_R = 0.005
HW_NECK = (-0.03, 0.20)
HW_ENTRY = 1.02
TRADE_COLS = ["market", "source", "symbol", "family", "timeframe", "mode", "stop_kind", "direction", "exit", "t",
              "exit_bar", "bars_held", "entry_price", "stop", "tp_used", "tp1", "tp2", "risk_pct", "is_open",
              "tp_hit", "net_ret", "R", "entry_time", "exit_time", "days_held", "exit_reason", "variant",
              "sig_LS_time", "sig_H_time", "sig_RS_time", "sig_H_price", "sig_RS_price", "sig_breakout_level",
              "sig_breakout_time", "sig_neckline_change", "pattern_id"]


def _log(text: str) -> None:
    line = f"{time.strftime('%H:%M:%S')} {text}"
    print(line, flush=True)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(OUT_DIR / "run.log", "a", encoding="utf-8") as fh:
        fh.write(line + "\n")


# ------------------------------------------------------------------ benchmarks

def fetch_0050() -> pd.DataFrame:
    """0050.TW daily since 2014 (public Yahoo chart endpoint, no key). period1/period2 because range=max returns
    monthly bars for daily requests."""
    from ihs_scanner import data_tw
    client = data_tw.YahooClient()
    client.refresh_crumb()
    params = {"interval": "1d", "period1": 1388534400, "period2": int(time.time()), "events": "div,split"}
    if client.crumb:
        params["crumb"] = client.crumb
    resp = client._session().get(data_tw.YAHOO_CHART_URL.format(symbol="0050.TW"), params=params, timeout=30)
    res = data_tw.chart_result(resp.json(), "0050.TW")
    q = res["indicators"]["quote"][0]
    adj = res["indicators"]["adjclose"][0]["adjclose"]
    df = pd.DataFrame({"timestamp": pd.to_datetime(res["timestamp"], unit="s", utc=True), "close": q["close"],
                       "adjclose": adj, "volume": q["volume"]}).dropna(subset=["close", "adjclose"])
    df["close_time"] = df["timestamp"] + pd.Timedelta(hours=4, minutes=30)  # 13:30 Taipei
    df = df[df["close_time"] <= pd.Timestamp.now(tz="UTC")]
    divs = res.get("events", {}).get("dividends", {}) or {}
    BENCH_DIR.mkdir(parents=True, exist_ok=True)
    df.to_csv(TW_BENCH, index=False, encoding="utf-8")
    pd.DataFrame([{"date": pd.to_datetime(int(v["date"]), unit="s", utc=True), "amount": v["amount"]}
                  for v in divs.values()]).to_csv(BENCH_DIR / "0050.TW_dividends.csv", index=False, encoding="utf-8")
    return df


def load_benchmarks(offline: bool) -> dict:
    if TW_BENCH.exists() or offline:
        tw = pd.read_csv(TW_BENCH, encoding="utf-8")
        for c in ("timestamp", "close_time"):
            tw[c] = pd.to_datetime(tw[c], utc=True, format="ISO8601")
    else:
        tw = fetch_0050()
    btc = {tf: D.load_frame(D.cache_file("crypto", "binance-spot", "BTCUSDT", tf)) for tf in ("1d", "4h")}
    ns = lambda s: s.astype("datetime64[ns, UTC]").astype("int64").to_numpy()
    return {"tw": {"t": ns(tw["close_time"]), "tr": tw["adjclose"].to_numpy(float), "px": tw["close"].to_numpy(float),
                   "first": tw["timestamp"].iloc[0], "last": tw["timestamp"].iloc[-1]},
            "crypto_1d": {"t": ns(btc["1d"]["close_time"]), "tr": btc["1d"]["close"].to_numpy(float)},
            "crypto_4h": {"t": ns(btc["4h"]["close_time"]), "tr": btc["4h"]["close"].to_numpy(float)}}


# ------------------------------------------------------------------ per-symbol worker

def _ns_index(series: pd.Series) -> dict[int, int]:
    v = series.astype("datetime64[ns, UTC]").astype("int64").to_numpy()
    return {int(x): i for i, x in enumerate(v)}


def _ns(text) -> int | None:
    if text is None or (isinstance(text, float) and np.isnan(text)) or text == "":
        return None
    return int(pd.Timestamp(text).tz_convert("UTC").value) if pd.Timestamp(text).tzinfo else \
        int(pd.Timestamp(text).tz_localize("UTC").value)


def _hw_signal(neck_change, close_t, level) -> bool:
    try:
        return HW_NECK[0] <= float(neck_change) <= HW_NECK[1] and float(close_t) >= HW_ENTRY * float(level)
    except (TypeError, ValueError):
        return False


def worker(task: tuple) -> tuple[list[dict], list[dict], dict]:
    market, source, symbol, tr, sig_path, k_random, waits = task
    rng = np.random.default_rng(zlib.crc32(f"{market}|{source}|{symbol}".encode("utf-8")))
    annots, retests = [], []
    checks = {"price_mismatch": 0, "future_pivot": 0, "ls_missing": 0, "h_mismatch": 0, "rows": 0}
    frames = {}
    for tf in sorted(set(tr["timeframe"]) | {"1d", "1w"}):
        p = D.cache_file(market, source, symbol, tf)
        if p.exists():
            df = D.load_frame(p)
            if len(df):
                frames[tf] = df
    for tf, g in tr.groupby("timeframe"):
        df = frames.get(tf)
        if df is None:
            continue
        b = E.Bars.of(df)
        vol = df["volume"].to_numpy(float)
        ct = df["close_time"].astype("datetime64[ns, UTC]").astype("int64").to_numpy()
        idx = _ns_index(df["timestamp"])
        n, start, max_hold = b.n, E.START[tf], E.MAX_HOLD[tf]
        for row in g.itertuples(index=False):
            checks["rows"] += 1
            t, xb, h = int(row.t), int(row.exit_bar), int(row.bars_held)
            rec = {"rid": row.rid, "entry_ct": int(ct[t]), "exit_ct": int(ct[xb])}
            if abs(b.close[t] - row.entry_price) > 1e-9 * max(1.0, abs(row.entry_price)):
                checks["price_mismatch"] += 1
            # random entry, same bars held
            st = F.draw_starts(rng, start, n - 1 - h, k_random)
            if st.size and h >= 1:
                r = F.fixed_hold_returns(b.close, st, h, market)
                rec["rnd_hold_mean"], rec["rnd_hold_pos"] = float(r.mean()), float((r > 0).mean())
            # random entry, same %-bracket and holding cap (single-target exits only)
            if row.exit in ("TP1", "TP2") and np.isfinite(row.tp_used) and row.risk_pct > 0:
                st2 = F.draw_starts(rng, start, n - 2, k_random)
                rets, wins = F.random_bracket(b, st2, float(row.risk_pct), float(row.tp_used) / row.entry_price - 1.0,
                                              max_hold, market)
                if rets:
                    rec["rnd_br_mean"], rec["rnd_br_win"] = float(np.mean(rets)), float(np.mean(wins))
            if row.family == "ihs":
                ls_i, h_i, rs_i = (idx.get(_ns(v)) for v in (row.sig_LS_time, row.sig_H_time, row.sig_RS_time))
                brk_i = idx.get(_ns(row.sig_breakout_time)) if isinstance(row.sig_breakout_time, str) else None
                if ls_i is None or h_i is None or rs_i is None:
                    checks["ls_missing"] += 1
                elif max(ls_i, h_i, rs_i, brk_i or 0) > t:
                    checks["future_pivot"] += 1  # never expected: the detector only saw bars <= t
                else:
                    if abs(b.low[h_i] - row.sig_H_price) > 1e-6 * max(1.0, row.sig_H_price):
                        checks["h_mismatch"] += 1
                    rec["ls_low"] = float(b.low[ls_i])
                    rec["rs_ls"] = float(row.sig_RS_price) / rec["ls_low"] if rec["ls_low"] > 0 else None
                    d1 = F.prior_decline(b.high, b.low, ls_i, LOOKBACK[tf])
                    d2 = F.prior_decline(b.high, b.low, ls_i, 2 * LOOKBACK[tf])
                    rec.update(d1)
                    rec["decline_bars_ext"], rec["decline_depth_ext"] = d2["decline_bars"], d2["decline_depth"]
                    rec["vol_rs_ratio"] = F.volume_contraction(vol, ls_i, h_i, rs_i, t, VOL_N[tf])
                    rec["vol_entry_ratio"] = F.volume_surge(vol, t, 20)
                    rec["vol_brk_ratio"] = F.volume_surge(vol, brk_i, 20) if brk_i is not None else None
            annots.append(rec)
    # neckline retest entry
    tfs = ("1d", "1w") if market == "tw" else ("1d",)
    if sig_path is not None and Path(sig_path).exists():
        try:
            sg = pd.read_csv(sig_path, encoding="utf-8", low_memory=False)
        except pd.errors.EmptyDataError:
            sg = pd.DataFrame()
        if len(sg) and not {"family", "mode", "timeframe"} <= set(sg.columns):
            sg = pd.DataFrame()
        if len(sg):
            sg = sg[(sg["family"] == "ihs") & (sg["mode"] == "A") & (sg["timeframe"].isin(tfs))]
        for tf, g in (sg.groupby("timeframe") if len(sg) else []):
            df = frames.get(tf)
            if df is None:
                continue
            b = E.Bars.of(df)
            ts = df["timestamp"]
            for s in g.itertuples(index=False):
                t = int(s.t)
                level, rs = float(s.breakout_level), float(s.RS_price)
                base = {"market": market, "source": source, "symbol": symbol, "timeframe": tf, "t": t,
                        "pattern_id": s.pattern_id, "entry_time": ts.iloc[t], "year": ts.iloc[t].year,
                        "close_t": float(b.close[t]), "breakout_level": level, "RS_price": rs,
                        "neckline_change": s.neckline_change, "hw": _hw_signal(s.neckline_change, b.close[t], level)}
                for ex, tp in (("TP1", float(s.tp1)), ("TP2", float(s.tp2))):
                    bo = F.breakout_trade(b, t, rs, tp, E.MAX_HOLD[tf], market)
                    for mode in RETEST_MODES:
                        for x in XS:
                            rt = F.retest_trade(b, t, level, rs, tp, x, waits[tf], E.MAX_HOLD[tf], market, mode)
                            retests.append({**base, "exit": ex, "rt_mode": mode, "x": x, "wait": waits[tf],
                                            **{f"bo_{k}": v for k, v in bo.items()},
                                            **{f"rt_{k}": v for k, v in rt.items()}})
    return annots, retests, checks


# ------------------------------------------------------------------ aggregation helpers

def period_mask(df: pd.DataFrame, lo: int, hi: int, col: str = "year") -> pd.Series:
    return (df[col] >= lo) & (df[col] <= hi)


def basic_stats(g: pd.DataFrame, ret="net_ret", win="tp_hit", risk="risk_pct", R="R") -> dict:
    n = len(g)
    r = g[ret].astype(float)
    rr = g.loc[g[risk] >= MIN_RISK_FOR_R, R].astype(float) if R in g else pd.Series(dtype=float)
    return {"筆數": n,
            "勝率%": 100 * g[win].astype(bool).mean() if n else None,
            "平均報酬%": 100 * r.mean() if n else None,
            "中位數報酬%": 100 * r.median() if n else None,
            "獲利因子": F.profit_factor(r) if n else None,
            "平均R": rr.mean() if len(rr) else None}


def excess_stats(g: pd.DataFrame) -> dict:
    out = {}
    for tag, col in (("", "excess_tr"), ("（價格）", "excess_px")):
        e = g[col].dropna()
        m, _t, p = F.one_sample_t(e)
        out[f"基準平均報酬%{tag}"] = 100 * g[col.replace("excess", "bench")].dropna().mean() if len(e) else None
        out[f"平均超額%{tag}"] = 100 * m if m is not None else None
        out[f"超額>0比例%{tag}"] = 100 * (e > 0).mean() if len(e) else None
        out[f"超額 p 值{tag}"] = p
    out["有基準的筆數"] = int(g["excess_tr"].notna().sum())
    return out


def label_cols(market, family, variant, tf, ex) -> dict:
    return {"市場": {"tw": "台股", "crypto": "加密"}[market], "型態": {"ihs": "頭肩底", "tri": "收斂三角"}[family],
            "進場／停損": {"A": "A 突破確認", "B": "B 右肩確認", "inval": "失效價停損", "atr": "ATR 1.5× 停損"}[variant],
            "週期": tf, "出場": ex}


def tf_groups(g: pd.DataFrame) -> list[tuple[str, pd.DataFrame]]:
    out = [(tf, g[g["timeframe"] == tf]) for tf in E.TIMEFRAMES]
    out.append(("1d+1w", g[g["timeframe"].isin(["1d", "1w"])]))
    out.append(("全部", g))
    return [(k, v) for k, v in out if len(v)]


def bench_tables(tr: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    rows, rnd = [], []
    for (market, family, variant, ex), g0 in tr.groupby(["market", "family", "variant", "exit"], sort=True):
        subsets = [("全部", g0)]
        if market == "tw" and family == "ihs" and variant == "A":
            subsets.append(("高勝率篩選", g0[g0["hw"]]))
        for sub, g1 in subsets:
            for tf, g2 in tf_groups(g1):
                for pname, lo, hi in PERIODS:
                    g = g2[period_mask(g2, lo, hi)]
                    if not len(g):
                        continue
                    lab = {**label_cols(market, family, variant, tf, ex), "子集合": sub, "期間": pname}
                    rows.append({**lab, **basic_stats(g), **excess_stats(g)})
                    d1 = (g["net_ret"] - g["rnd_hold_mean"]).dropna()
                    m1, _t, p1 = F.one_sample_t(d1)
                    rec = {**lab, **basic_stats(g), "正報酬比例%": 100 * (g["net_ret"] > 0).mean(),
                           "隨機同持有根數 平均報酬%": 100 * g["rnd_hold_mean"].mean(),
                           "隨機同持有根數 正報酬比例%": 100 * g["rnd_hold_pos"].mean(),
                           "策略−隨機(持有) 平均差%": 100 * m1 if m1 is not None else None, "p(持有)": p1}
                    if g["rnd_br_mean"].notna().any():
                        d2 = (g["net_ret"] - g["rnd_br_mean"]).dropna()
                        m2, _t, p2 = F.one_sample_t(d2)
                        dw = (g["tp_hit"].astype(float) - g["rnd_br_win"]).dropna()
                        mw, _t, pw = F.one_sample_t(dw)
                        rec.update({"隨機同停損停利 平均報酬%": 100 * g["rnd_br_mean"].mean(),
                                    "隨機同停損停利 勝率%": 100 * g["rnd_br_win"].mean(),
                                    "策略−隨機(停損停利) 平均差%": 100 * m2 if m2 is not None else None,
                                    "p(停損停利 報酬)": p2,
                                    "策略−隨機 勝率差(百分點)": 100 * mw if mw is not None else None,
                                    "p(勝率)": pw})
                    rnd.append(rec)
    return pd.DataFrame(rows), pd.DataFrame(rnd)


# ------------------------------------------------------------------ item 2: filters

MONTH_EDGES, MONTH_LABELS = [0, 3, 6, 12, 1e9], ["0–3 月", "3–6 月", "6–12 月", "12+ 月"]


def add_filter_bins(t: pd.DataFrame) -> pd.DataFrame:
    t = t.copy()
    mb = t["timeframe"].map(F.month_bars)
    t["decline_months"] = t["decline_bars"] / mb
    t["decline_months_ext"] = t["decline_bars_ext"] / mb
    t["c_rs_ge_ls"] = np.where(t["rs_ls"].isna(), "未知", np.where(t["rs_ls"] >= 1.0, "RS ≥ LS", "RS < LS"))
    t["c_rs_ls_bin"] = [F.bin_label(v, [0, 0.95, 1.0, 1.05, 1.10, 1e9],
                                    ["<0.95", "0.95–1.00", "1.00–1.05", "1.05–1.10", "≥1.10"]) for v in t["rs_ls"]]
    t["c_decline_months"] = [F.bin_label(v, MONTH_EDGES, MONTH_LABELS) for v in t["decline_months"]]
    t["c_decline_months_ext"] = [F.bin_label(v, MONTH_EDGES, MONTH_LABELS) for v in t["decline_months_ext"]]
    t["c_decline_depth"] = [F.bin_label(-float(v) if pd.notna(v) else None, [-1e9, 0.20, 0.35, 0.50, 1e9],
                                        ["跌幅 <20%", "20–35%", "35–50%", "≥50%"]) for v in t["decline_depth"]]
    t["c_vol_rs"] = [F.bin_label(v, [0, 0.6, 0.8, 1.0, 1.3, 1e18], ["<0.6", "0.6–0.8", "0.8–1.0", "1.0–1.3", "≥1.3"])
                     for v in t["vol_rs_ratio"]]
    edges, labs = [0, 1.0, 1.5, 2.0, 3.0, 1e18], ["<1", "1–1.5", "1.5–2", "2–3", "≥3"]
    t["c_vol_entry"] = [F.bin_label(v, edges, labs) for v in t["vol_entry_ratio"]]
    t["c_vol_brk"] = [F.bin_label(v, edges, labs) for v in t["vol_brk_ratio"]]
    t["c_bench_sma"] = np.where(t["bench_above_sma"].isna(), "未知",
                                np.where(t["bench_above_sma"] == 1.0, "大盤 > 200MA", "大盤 ≤ 200MA"))
    return t


CONDITIONS = [("c_rs_ge_ls", "(a) 右肩 ≥ 左肩"), ("c_rs_ls_bin", "(a) RS/LS 比值"),
              ("c_decline_months", "(b) 前置下跌長度（回看 250/52 根）"),
              ("c_decline_months_ext", "(b) 前置下跌長度（延長回看 500/104 根）"),
              ("c_decline_depth", "(b) 前置下跌深度"), ("c_vol_rs", "(c) RS 量縮比"),
              ("c_vol_entry", "(c) 進場當根放量比"), ("c_vol_brk", "(c) 突破當根放量比"),
              ("c_bench_sma", "(d) 大盤 200 日均線濾網")]


def _diff_dir(g: pd.DataFrame, rest: pd.DataFrame) -> tuple[float | None, float | None]:
    if len(g) < 30 or len(rest) < 30:
        return None, None
    return (100 * (g["tp_hit"].astype(bool).mean() - rest["tp_hit"].astype(bool).mean()),
            100 * (g["net_ret"].mean() - rest["net_ret"].mean()))


def filter_table(t: pd.DataFrame, conditions=CONDITIONS, subsets=("全部", "高勝率篩選"),
                 tfs=("1d+1w", "1d", "1w"), exits=("TP1", "TP2")) -> pd.DataFrame:
    rows = []
    for col, cname in conditions:
        for sub in subsets:
            g0 = t[t["hw"]] if sub == "高勝率篩選" else t
            for ex in exits:
                g1 = g0[g0["exit"] == ex]
                for tf in tfs:
                    g2 = g1[g1["timeframe"].isin(["1d", "1w"])] if tf == "1d+1w" else (
                        g1 if tf == "全部" else g1[g1["timeframe"] == tf])
                    if not len(g2):
                        continue
                    for val in sorted(g2[col].unique(), key=str):
                        rec = {"條件": cname, "分組": val, "基底": sub, "出場": ex, "週期": tf}
                        dirs = []
                        for pname, lo, hi in PERIODS:
                            gp = g2[period_mask(g2, lo, hi)]
                            g = gp[gp[col] == val]
                            rest = gp[(gp[col] != val) & (gp[col] != "未知")]
                            st = basic_stats(g)
                            ex_m = g["excess_tr"].dropna()
                            pre = "" if pname == "全期" else f"[{pname}] "
                            if pname == "全期":
                                rec.update(st)
                                rec["平均超額%（vs 基準）"] = 100 * ex_m.mean() if len(ex_m) else None
                                # same %-bracket at random times: how much of the win rate is just geometry
                                rec["隨機同停損停利 勝率%"] = 100 * g["rnd_br_win"].mean() if len(g) else None
                                rec["勝率−隨機(百分點)"] = (rec["勝率%"] - rec["隨機同停損停利 勝率%"]
                                                       if len(g) else None)
                                rec["報酬−隨機(百分點)"] = (100 * (g["net_ret"] - g["rnd_br_mean"]).mean()
                                                       if len(g) else None)
                                dw, dr = _diff_dir(g, rest)
                                rec["勝率差 vs 其他組(百分點)"] = dw
                                rec["平均報酬差 vs 其他組(百分點)"] = dr
                            else:
                                rec[pre + "筆數"] = st["筆數"]
                                rec[pre + "勝率%"] = st["勝率%"]
                                rec[pre + "平均報酬%"] = st["平均報酬%"]
                                dw, dr = _diff_dir(g, rest)
                                rec[pre + "勝率差"] = dw
                                rec[pre + "報酬差"] = dr
                                dirs.append((dw, dr))
                        rec["兩段一致"] = consistency(dirs) if val != "未知" else ""
                        rows.append(rec)
    return pd.DataFrame(rows)


def consistency(dirs: list[tuple[float | None, float | None]]) -> str:
    """'一致較好' when the group beats the rest of the base in BOTH periods on win rate AND mean return,
    '一致較差' when it trails in both, otherwise '不一致'; '樣本不足' when a period has < 30 trades."""
    if len(dirs) != 2 or any(d[0] is None for d in dirs):
        return "樣本不足"
    (w1, r1), (w2, r2) = dirs
    if w1 > 0 and w2 > 0 and r1 > 0 and r2 > 0:
        return "一致較好"
    if w1 < 0 and w2 < 0 and r1 < 0 and r2 < 0:
        return "一致較差"
    return "不一致"


# ------------------------------------------------------------------ item 3: retest

def retest_table(rt: pd.DataFrame) -> pd.DataFrame:
    rows = []
    ok = rt[(rt["bo_status"] == "entered") & (~rt["bo_is_open"].astype(bool))
            & (~rt["rt_status"].isin(["pending", "bad_level"])) & (rt["rt_is_open"] != True)]  # noqa: E712
    for (market, tf, ex, mode, x), g0 in ok.groupby(["market", "timeframe", "exit", "rt_mode", "x"], sort=True):
        for sub in ("全部", "高勝率篩選"):
            g1 = g0[g0["hw"]] if sub == "高勝率篩選" else g0
            for pname, lo, hi in PERIODS:
                g = g1[period_mask(g1, lo, hi)]
                if not len(g):
                    continue
                filled = g[g["rt_status"] == "filled"]
                missed = g[g["rt_status"] != "filled"]
                bo = g.rename(columns={"bo_net_ret": "net_ret", "bo_tp_hit": "tp_hit", "bo_risk_pct": "risk_pct",
                                       "bo_R": "R"})
                rtf = filled.rename(columns={"rt_net_ret": "net_ret", "rt_tp_hit": "tp_hit", "rt_risk_pct": "risk_pct",
                                             "rt_R": "R"})
                bof = bo.loc[filled.index]
                bom = bo.loc[missed.index]
                s_bo, s_rt, s_bof, s_bom = basic_stats(bo), basic_stats(rtf), basic_stats(bof), basic_stats(bom)
                per_sig = np.where(g["rt_status"] == "filled", g["rt_net_ret"], 0.0)
                rec = {"市場": {"tw": "台股", "crypto": "加密"}[market], "週期": tf, "出場": ex,
                       "回測方式": {"limit": "限價", "close_confirm": "收盤確認"}[mode], "x%": 100 * x,
                       "子集合": sub, "期間": pname, "訊號數": len(g),
                       "成交比例%": 100 * len(filled) / len(g),
                       "突破進場 勝率%": s_bo["勝率%"], "突破進場 平均報酬%": s_bo["平均報酬%"],
                       "突破進場 平均R": s_bo["平均R"], "突破進場 獲利因子": s_bo["獲利因子"],
                       "回測進場 筆數": s_rt["筆數"], "回測進場 勝率%": s_rt["勝率%"],
                       "回測進場 平均報酬%": s_rt["平均報酬%"], "回測進場 中位數%": s_rt["中位數報酬%"],
                       "回測進場 平均R": s_rt["平均R"], "回測進場 獲利因子": s_rt["獲利因子"],
                       "同批(有成交)訊號 突破進場 勝率%": s_bof["勝率%"],
                       "同批(有成交)訊號 突破進場 平均報酬%": s_bof["平均報酬%"],
                       "沒等到回測的訊號 筆數": s_bom["筆數"], "沒等到 突破進場 勝率%": s_bom["勝率%"],
                       "沒等到 突破進場 平均報酬%": s_bom["平均報酬%"],
                       "每訊號期望值% 突破": s_bo["平均報酬%"],
                       "每訊號期望值% 回測(未成交=0)": 100 * float(np.mean(per_sig))}
                d = (filled["rt_net_ret"] - filled["bo_net_ret"]).dropna()
                rec["回測−突破(同批) 平均差%"] = 100 * d.mean() if len(d) else None
                rec["p(同批差)"] = F.one_sample_t(d)[2]
                rows.append(rec)
    return pd.DataFrame(rows)


# ------------------------------------------------------------------ main

def md_table(df: pd.DataFrame, cols: list[str]) -> str:
    cols = [c for c in cols if c in df.columns]
    lines = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for _, r in df[cols].iterrows():
        vals = []
        for v in r:
            if isinstance(v, (float, np.floating)):
                vals.append("" if not np.isfinite(v) else (f"{v:.4f}" if abs(v) < 0.1 and v != 0 else f"{v:.2f}"))
            else:
                vals.append(str(v))
        lines.append("| " + " | ".join(vals) + " |")
    return "\n".join(lines)


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--tag", default="main")
    p.add_argument("--workers", type=int, default=15)
    p.add_argument("--k-random", type=int, default=5)
    p.add_argument("--wait-1d", type=int, default=20)
    p.add_argument("--wait-1w", type=int, default=8)
    p.add_argument("--max-symbols", type=int, default=0)
    p.add_argument("--offline", action="store_true")
    return p.parse_args(argv)


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    args = parse_args(argv)
    t0 = time.time()
    bench = load_benchmarks(args.offline)
    _log(f"[followup] 0050 {bench['tw']['first']} .. {bench['tw']['last']} ({len(bench['tw']['t'])} bars)")
    tr = pd.read_csv(D.RESULTS_DIR / args.tag / "trades_all.csv", encoding="utf-8", usecols=TRADE_COLS,
                     low_memory=False)
    tr["is_open"] = tr["is_open"].astype(str).str.lower().eq("true")
    tr["tp_hit"] = tr["tp_hit"].astype(str).str.lower().eq("true")
    tr = tr[(tr["direction"] == 1) & (~tr["is_open"])].reset_index(drop=True)
    tr["rid"] = np.arange(len(tr))
    tr["entry_time"] = pd.to_datetime(tr["entry_time"], utc=True, format="ISO8601")
    tr["year"] = tr["entry_time"].dt.year
    keys = tr[["market", "source", "symbol"]].drop_duplicates().values.tolist()
    if args.max_symbols:  # trial: evenly spaced over the list (both markets)
        keys = keys[:: max(1, len(keys) // args.max_symbols)][: args.max_symbols]
    waits = {"1d": args.wait_1d, "1w": args.wait_1w}
    groups = {k: g for k, g in tr.groupby(["market", "source", "symbol"], sort=False)}
    tasks = []
    for market, source, symbol in keys:
        g = groups[(market, source, symbol)]
        sp = D.RESULTS_DIR / args.tag / market / "signals" / f"{source}_{symbol}.csv"
        tasks.append((market, source, symbol, g.drop(columns=["entry_time"]), str(sp), args.k_random, waits))
    _log(f"[followup] {len(tr)} closed long trades, {len(tasks)} symbols, workers={args.workers}")
    annots, retests = [], []
    checks: dict[str, int] = {}
    with mp.Pool(args.workers) as pool:
        for k, (a, r, c) in enumerate(pool.imap_unordered(worker, tasks, chunksize=4), 1):
            annots.extend(a)
            retests.extend(r)
            for kk, vv in c.items():
                checks[kk] = checks.get(kk, 0) + vv
            if k % 500 == 0 or k == len(tasks):
                _log(f"[followup] {k}/{len(tasks)} symbols, {time.time() - t0:.0f}s")
    _log(f"[followup] checks {checks}")
    an = pd.DataFrame(annots)
    tr = tr.merge(an, on="rid", how="inner")
    # benchmark returns (same holding period, after the benchmark's own costs)
    tr["bench_tr"] = np.nan
    tr["bench_px"] = np.nan
    tr["bench_above_sma"] = np.nan
    for market in ("tw", "crypto"):
        for tf in E.TIMEFRAMES:
            m = (tr["market"] == market) & (tr["timeframe"] == tf)
            if not m.any():
                continue
            bk = bench["tw"] if market == "tw" else bench["crypto_4h" if tf == "4h" else "crypto_1d"]
            e0, e1 = tr.loc[m, "entry_ct"].to_numpy(np.int64), tr.loc[m, "exit_ct"].to_numpy(np.int64)
            tr.loc[m, "bench_tr"] = F.bench_return(bk["t"], bk["tr"], e0, e1, market)
            tr.loc[m, "bench_px"] = F.bench_return(bk["t"], bk.get("px", bk["tr"]), e0, e1, market)
            sma_src = bench["tw"] if market == "tw" else bench["crypto_1d"]
            tr.loc[m, "bench_above_sma"] = F.above_sma_asof(sma_src["t"], sma_src["tr"], e0, 200)
            first = bk["t"][0]
            tr.loc[m & (tr["entry_ct"] < first), ["bench_tr", "bench_px"]] = np.nan
    tr["excess_tr"] = tr["net_ret"] - tr["bench_tr"]
    tr["excess_px"] = tr["net_ret"] - tr["bench_px"]
    tr["hw"] = ((tr["family"] == "ihs") & (tr["mode"] == "A")
                & tr["sig_neckline_change"].between(*HW_NECK) & (tr["entry_price"] >= HW_ENTRY * tr["sig_breakout_level"]))
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    tr.to_csv(OUT_DIR / "trades_annotated.csv", index=False, encoding="utf-8")
    rt = pd.DataFrame(retests)
    rt.to_csv(OUT_DIR / "retest.csv", index=False, encoding="utf-8")
    _log(f"[followup] simulation done {time.time() - t0:.0f}s; aggregating")

    b1, r1 = bench_tables(tr)
    tw_a = add_filter_bins(tr[(tr["market"] == "tw") & (tr["family"] == "ihs") & (tr["mode"] == "A")
                              & tr["timeframe"].isin(["1d", "1w"]) & tr["exit"].isin(["TP1", "TP2"])])
    f2 = filter_table(tw_a)
    cr_a = add_filter_bins(tr[(tr["market"] == "crypto") & (tr["family"] == "ihs") & (tr["mode"] == "A")
                              & tr["exit"].isin(["TP1", "TP2"])])
    f2c = filter_table(cr_a, conditions=[("c_bench_sma", "(d) BTC 200 日均線濾網")], subsets=("全部",),
                       tfs=("4h", "1d", "1w", "全部"))
    r3 = retest_table(rt)

    # 0050 buy and hold over the whole window, for context
    bt = bench["tw"]
    yrs = (bt["t"][-1] - bt["t"][0]) / 1e9 / 86400 / 365.25
    notes = [
        ("用途", "D42：D40 回測的延伸分析（基準比較、隨機進場對照、新篩選條件、頸線回測進場）。只用既有交易與 K 線快取，未重跑偵測；"
                 "未改網站、掃描器、通知、workflow。回測只描述過去資料，不是投資建議。"),
        ("資料", f"交易：results/{args.tag}/trades_all.csv 的做多、已平倉交易 {len(tr)} 筆；訊號：results/{args.tag}/<market>/signals；"
                 "K 線：backtest/data 快取。"),
        ("基準", f"台股＝0050.TW 日 K（Yahoo 公開端點，{bt['first'].date()}～{bt['last'].date()}）：主結果用 adjclose（還原權息，含配息再投入），"
                 "另列「（價格）」＝只還原分割、不含配息。加密＝BTCUSDT（幣安現貨）1d，4h 交易用 BTC 4h。基準報酬＝進場那根收盤時間"
                 "（as-of：該時間或之前最新一根基準收盤）到出場那根收盤時間；扣基準自身成本（0050 買 0.1425%、賣 0.1425%＋ETF 證交稅 0.1%；BTC 每邊 0.1%）。"
                 f"超額＝策略 net_ret − 基準報酬。0050 全期還原報酬年化 {100 * ((bt['tr'][-1] / bt['tr'][0]) ** (1 / yrs) - 1):.1f}%（{yrs:.1f} 年）。"),
        ("權息不對稱", "策略標的的 K 線（Yahoo close）只還原分割、未還原權息：持有期間遇除息，策略報酬少算股息（且除息缺口可能誤觸停損），"
                     "而基準主結果含配息，所以主結果對策略偏嚴；「（價格）」欄兩邊都不含配息。"),
        ("t 檢定", "超額報酬：單樣本 t（H0 平均超額＝0），雙尾 p。隨機對照：每筆交易減去其隨機對照平均後做單樣本 t（配對）。"
                  "p 值用 Student t 分布（自寫不完全 beta 函數，未裝 scipy）。交易之間不獨立（同時段多檔同時進出），p 值偏樂觀。"),
        ("隨機進場", f"同一標的、同一週期，每筆交易抽 {args.k_random} 個隨機進場根（均勻抽於可用 K 線範圍）："
                    "(1) 持有相同根數，收盤進出；(2) 同樣的停損距離%、停利距離%與持有上限，用 D40 出場規則（同根碰兩者算停損）。"
                    "(2) 只對 TP1、TP2 出場做。成本同 D40。亂數種子固定（依標的名稱），可重現。"),
        ("高勝率篩選", "頭肩底 A：頸線 N2/N1−1 在 −3%～+20%，且進場價（突破確認那根收盤）≥ 突破當根頸線價 × 1.02。"),
        ("篩選條件（只用進場前資料）", "LS 價＝快取 K 線在 LS 那根的 low（LS 在進場根之前）；前置下跌＝LS 之前回看 250 根（日）／52 根（週）內最高 high 到 LS 的根數"
                     "（日線 21 根＝1 月、週線 4.35 根＝1 月）與跌幅 LS/高點−1，另以兩倍回看長度做敏感度；量縮比＝RS 前後 3 根（日）／1 根（週）平均量 ÷ LS 與 H 前後同樣根數的平均量，"
                     "所有視窗截到進場根為止；放量比＝進場根（或突破根）量 ÷ 前 20 根平均量；大盤濾網＝進場那根收盤時間 as-of 的 0050 還原收盤 > 其 200 日均線"
                     "（加密用 BTC 1d 收盤 > 200 日均線）。所有樞紐（LS/H/RS/突破）在進場根或之前，程式逐筆檢查（未來樞紐 0 筆）。"),
        ("兩段一致", "同一基底、出場、週期內，比較該分組與其他分組（不含「未知」）在 2016–2022 與 2023–2026 兩段的勝率差與平均報酬差："
                    "兩段兩者都 > 0＝一致較好，都 < 0＝一致較差，否則不一致；任一段任一邊 < 30 筆＝樣本不足。"),
        ("回測進場", f"每個 A 訊號（訊號 CSV，不受「同序列一次一倉」限制，所以筆數與 D40 不同）：訊號根 t 收盤後掛單，等 t+1～t+M 根"
                    f"（日 {args.wait_1d}、週 {args.wait_1w}）。限價：第一根 low ≤ 頸線×(1+x) 以 min(開盤, 限價) 成交；"
                    "收盤確認：第一根 low ≤ 頸線×(1+x) 且收盤 ≥ 頸線，以該根收盤進場，期間任一根收盤 < RS 取消。頸線＝訊號的突破當根頸線價。停損 RS、"
                    "停利沿用訊號 TP1／TP2，持有上限與成本同 D40。限價成交那根：low ≤ 停損即停損（以 min(成交價, 停損) 出場）；停利只在開盤即成交時才算"
                    "（否則高點可能在回落之前）；之後各根同 D40（同根碰停利停損算停損）。限價單在收盤跌破 RS 之前一定先成交（限價 > RS），所以「收盤跌破 RS 取消」"
                    "只在收盤確認版本有作用。每訊號期望值＝未成交記 0。"),
        ("執行", f"產生 {time.strftime('%Y-%m-%d %H:%M')}，耗時 {time.time() - t0:.0f} 秒；檢查 {checks}"),
    ]
    out = OUTPUT_DIR / f"回測延伸分析_{time.strftime('%Y%m%d')}.xlsx"
    write_excel(out, {"1_基準比較": b1, "1_隨機進場對照": r1, "2_台股A篩選條件": f2, "2_加密A大盤濾網": f2c,
                      "3_頸線回測進場": r3}, notes)
    _log(f"[followup] wrote {out}")

    # compact markdown for the report
    key_f = f2[(f2["週期"] == "1d+1w")] if len(f2) else f2
    with open(OUT_DIR / "summary.md", "w", encoding="utf-8") as fh:
        fh.write("# bench\n\n" + md_table(b1, list(b1.columns)) + "\n\n# random\n\n" + md_table(r1, list(r1.columns))
                 + "\n\n# filters tw (1d+1w)\n\n" + md_table(key_f, list(key_f.columns))
                 + "\n\n# filters crypto\n\n" + md_table(f2c, list(f2c.columns))
                 + "\n\n# retest\n\n" + md_table(r3, list(r3.columns)) + "\n")
    _log(f"[followup] done in {time.time() - t0:.0f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
