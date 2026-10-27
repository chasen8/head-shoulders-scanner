"""D50 score study: quintile tables, factor summary, new-score test. Reads features.csv.gz (score_study_run features)
and results/followup/trades_annotated.csv. Writes tables to C:\\trading-data\\ihs-scanner-v2\\output\\score-study\\.

Method (see docs/score-study.md): train = entries 2017-11 (crypto 2019-09) .. 2021, test = 2022 .. 2026-10 (frozen: the
feature selection and the signs of the new score are decided from training rows only). A feature value is first turned
into its percentile among the TRAINING rows of the same market and timeframe (mid-rank ECDF), so merged timeframes are
comparable; the five groups are the 0-20 .. 80-100 percentile bands of that training distribution.
"""
from __future__ import annotations

import math
from statistics import NormalDist

import numpy as np
import pandas as pd

from backtest import score_study as S
from backtest.score_study_run import FEATURES, KEY, OUT_DIR, TRADES

TEST_START = pd.Timestamp("2022-01-01", tz="UTC")
TRAIN_START = {"tw": pd.Timestamp("2017-11-01", tz="UTC"), "crypto": pd.Timestamp("2019-09-01", tz="UTC")}
MARKET_L = {"tw": "台股", "crypto": "加密"}

# (column, label, in new-score candidate pool)
FEATS = [
    ("pattern_score", "現行總分（滿分100）", False),
    ("shape_score", "形狀分", True),
    ("shoulder_score", "左右肩對稱分（30）", True),
    ("head_depth_score", "頭部深度分（25）", True),
    ("neckline_score", "頸線平緩分（25）", True),
    ("head_depth", "頭部深度%（原始）", True),
    ("head_atr_multiple", "頭部深度／ATR", True),
    ("head_rise_pct", "型態高度（頭低點到頸線漲幅）", True),
    ("volume_ratio", "突破量比", True),
    ("pos52", "52週區間位置", True),
    ("near52", "收盤／52週高", True),
    ("prior_high_months", "前置高點距LS月數", True),
    ("shoulder_diff", "肩差%（原始）", True),
    ("neckline_angle", "頸線角度（負＝下斜）", True),
]
LABEL = {c: l for c, l, _ in FEATS}
POOL = [c for c, _l, p in FEATS if p]
NORM = NormalDist()


def load(crypto_split: str = "spec") -> pd.DataFrame:
    tr = pd.read_csv(TRADES, encoding="utf-8", low_memory=False)
    tr = tr[(tr["family"] == "ihs") & (tr["mode"] == "A") & tr["exit"].isin(["TP1", "TP2"])].copy()
    f = pd.read_csv(FEATURES, encoding="utf-8", low_memory=False)
    f = f[f["error"].isna()].drop(columns=["error", "t", "status"], errors="ignore")
    d = tr.merge(f, on=KEY, how="inner")
    d["when"] = pd.to_datetime(d["entry_ct"], utc=True)
    d["ret"] = d["net_ret"].astype(float) * 100.0
    d["win"] = d["tp_hit"].astype(str).str.lower().eq("true")
    d["rnd"] = d["rnd_br_mean"].astype(float) * 100.0
    d["period"] = "drop"
    for m, t0 in TRAIN_START.items():
        ok = d["market"] == m
        d.loc[ok & (d["when"] >= t0) & (d["when"] < TEST_START), "period"] = "train"
        d.loc[ok & (d["when"] >= TEST_START), "period"] = "test"
    if crypto_split == "median":  # sensitivity: crypto 4h only exists from 2023, so split all crypto at the median date
        cm = d["market"] == "crypto"
        cut = d.loc[cm & (d["when"] >= TRAIN_START["crypto"]), "when"].median()
        d.loc[cm, "period"] = np.where(d.loc[cm, "when"] < cut, "train", "test")
        d.loc[cm & (d["when"] < TRAIN_START["crypto"]), "period"] = "drop"
    d = d[d["period"] != "drop"].reset_index(drop=True)
    d["tp_dist"] = (d["tp_used"].astype(float) / d["entry_price"].astype(float) - 1.0) * 100.0
    d["half"] = ""
    for m in TRAIN_START:
        tr_m = (d["market"] == m) & (d["period"] == "train")
        cut = d.loc[tr_m, "when"].median()
        d.loc[tr_m, "half"] = np.where(d.loc[tr_m, "when"] < cut, "h1", "h2")
    return d


def add_percentiles(d: pd.DataFrame, cols) -> pd.DataFrame:
    """<col>_p = mid-rank training ECDF within (market, timeframe, exit-independent signal set)."""
    d = d.copy()
    for c in cols:
        p = np.full(len(d), np.nan)
        for (m, tf), g in d.groupby(["market", "timeframe"]):
            tr = np.sort(g.loc[(g["period"] == "train") & g[c].notna(), c].to_numpy(float))
            if tr.size < 20:
                continue
            v = g[c].to_numpy(float)
            lo, hi = np.searchsorted(tr, v, side="left"), np.searchsorted(tr, v, side="right")
            q = (lo + hi) / 2.0 / tr.size
            q[~np.isfinite(v)] = np.nan
            p[g.index] = q
        d[c + "_p"] = p
    return d


def bins_of(p: np.ndarray, k: int = 5) -> np.ndarray:
    out = np.full(p.shape, -1)
    ok = np.isfinite(p)
    out[ok] = np.minimum((p[ok] * k).astype(int), k - 1)
    return out


def rho_p(rho: float, n: int) -> float:
    if not math.isfinite(rho) or n < 10:
        return math.nan
    return 2.0 * (1.0 - NORM.cdf(abs(rho) * math.sqrt(n - 1)))


def quintile_table(g: pd.DataFrame, col: str) -> pd.DataFrame:
    """One row per group x period."""
    b = bins_of(g[col + "_p"].to_numpy(float))
    rows = []
    for per in ("train", "test"):
        for k in range(5):
            s = g[(g["period"] == per) & (b == k)]
            st = S.group_stats(s["ret"], s["win"])
            st.update({"period": per, "group": k + 1, "rnd": float(s["rnd"].mean()) if len(s) else math.nan})
            rows.append(st)
    return pd.DataFrame(rows)


def cell_stats(d: pd.DataFrame, market: str, exit_: str, tf: str | None, col: str, boot: bool) -> dict:
    g = d[(d["market"] == market) & (d["exit"] == exit_)]
    if tf:
        g = g[g["timeframe"] == tf]
    g = g.dropna(subset=[col + "_p"])
    out = {"market": market, "exit": exit_, "tf": tf or "all", "feature": col}
    for per in ("train", "test"):
        s = g[g["period"] == per]
        if boot:
            rho, lo, hi = S.bootstrap_spearman(s[col + "_p"], s["ret"], n_boot=300, seed=1)
        else:
            rho, lo, hi = S.spearman(s[col + "_p"], s["ret"]), math.nan, math.nan
        out.update({f"rho_{per}": rho, f"lo_{per}": lo, f"hi_{per}": hi, f"p_{per}": rho_p(rho, len(s)),
                    f"n_{per}": len(s)})
    q = quintile_table(g, col)
    tr = q[q["period"] == "train"].dropna(subset=["mean"])
    if len(tr) >= 2:
        best, worst = int(tr.loc[tr["mean"].idxmax(), "group"]), int(tr.loc[tr["mean"].idxmin(), "group"])
        out.update({"best": best, "worst": worst})
        for per in ("train", "test"):
            qb = q[(q["period"] == per) & (q["group"] == best)].iloc[0]
            qw = q[(q["period"] == per) & (q["group"] == worst)].iloc[0]
            out.update({f"pf_best_{per}": qb["pf"], f"pf_worst_{per}": qw["pf"], f"win_best_{per}": qb["win"],
                        f"win_worst_{per}": qw["win"], f"exp_best_{per}": qb["mean"], f"exp_worst_{per}": qw["mean"],
                        f"n_best_{per}": qb["n"], f"n_worst_{per}": qw["n"]})
        for per in ("train", "test"):
            q1 = q[(q["period"] == per) & (q["group"] == 1)].iloc[0]
            q5 = q[(q["period"] == per) & (q["group"] == 5)].iloc[0]
            out[f"q51_exp_{per}"] = q5["mean"] - q1["mean"]
            out[f"q51_pf_{per}"] = f"{fmt(q5['pf'])}/{fmt(q1['pf'])}"
            out[f"q51_win_{per}"] = q5["win"] - q1["win"]
        out["same_dir"] = bool(
            out["exp_best_test"] - out["exp_worst_test"] > 0 and out["pf_best_test"] > out["pf_worst_test"]
            and out["exp_best_train"] - out["exp_worst_train"] > 0)
    return out


def verdict(cells: pd.DataFrame, alpha: float) -> str:
    """cells: the 2 rows (TP1, TP2) of one feature in one market. 有用 = both exits keep the same sign in training and
    test and every p < alpha (Bonferroni); 不確定 = at least one exit significant (p < 0.05) in both periods with a
    consistent sign; otherwise 無用."""
    strong, weak, signs = 0, 0, []
    for _, r in cells.iterrows():
        a, b = r["rho_train"], r["rho_test"]
        if not (math.isfinite(a) and math.isfinite(b)) or np.sign(a) != np.sign(b):
            continue
        if r["p_train"] < 0.05 and r["p_test"] < 0.05:
            signs.append(int(np.sign(a)))
            if r["p_train"] < alpha and r["p_test"] < alpha:
                strong += 1
            else:
                weak += 1
    if not signs:
        return "無用"
    if len(set(signs)) > 1:
        return "不確定(方向不一)"
    sg = "+" if signs[0] > 0 else "-"
    return f"有用({sg})" if strong == 2 else f"不確定({sg})"


def front_table(mc: pd.DataFrame, verd: dict, cols) -> str:
    """One row per factor: best vs worst training group (PF / win-rate gap / expectancy gap), training and test, for
    each market x exit; how many of the 4 cells keep the direction; the verdict per market."""
    def cell(r, per):
        pf = f"{fmt(r['pf_best_' + per])}/{fmt(r['pf_worst_' + per])}"
        return (f"PF {pf}；勝率差 {fmt(r['win_best_' + per] - r['win_worst_' + per], 1)}pp；"
                f"期望差 {fmt(r['exp_best_' + per] - r['exp_worst_' + per])}")
    rows = []
    for c in cols:
        rec = {"因子": LABEL[c]}
        ok = 0
        for m in ("tw", "crypto"):
            for ex in ("TP1", "TP2"):
                r = mc[(mc["feature"] == c) & (mc["market"] == m) & (mc["exit"] == ex)].iloc[0]
                tag = f"{MARKET_L[m]} {ex}"
                rec[f"{tag} 最好組(訓練決定)"] = f"Q{int(r['best'])} vs 最差 Q{int(r['worst'])}"
                rec[f"{tag} 訓練"] = cell(r, "train")
                rec[f"{tag} 測試"] = cell(r, "test")
                ok += int(bool(r["same_dir"]))
        rec["兩段方向一致(格/4)"] = f"{ok}/4"
        rec["結論 台股"], rec["結論 加密"] = verd[("tw", c)], verd[("crypto", c)]
        rows.append(rec)
    return md(pd.DataFrame(rows))


def fmt(v, nd=2, pct=False) -> str:
    if v is None or (isinstance(v, float) and not math.isfinite(v)):
        return "inf" if isinstance(v, float) and v == math.inf else "—"
    return f"{v:.{nd}f}"


def md(df: pd.DataFrame) -> str:
    cols = list(df.columns)
    lines = ["| " + " | ".join(map(str, cols)) + " |", "|" + "|".join("---" for _ in cols) + "|"]
    for _, r in df.iterrows():
        lines.append("| " + " | ".join(str(r[c]) for c in cols) + " |")
    return "\n".join(lines)


def quintile_md(d: pd.DataFrame, market: str, exit_: str, tf: str | None, col: str) -> str:
    g = d[(d["market"] == market) & (d["exit"] == exit_)]
    if tf:
        g = g[g["timeframe"] == tf]
    g = g.dropna(subset=[col + "_p"])
    q = quintile_table(g, col)
    rows = []
    for k in range(1, 6):
        rec = {"組": f"Q{k}"}
        for per, tag in (("train", "訓練"), ("test", "測試")):
            r = q[(q["period"] == per) & (q["group"] == k)].iloc[0]
            rec.update({f"{tag} n": int(r["n"]), f"{tag} 勝率%": fmt(r["win"], 1), f"{tag} 期望%": fmt(r["mean"]),
                        f"{tag} 中位數%": fmt(r["median"]), f"{tag} PF": fmt(r["pf"]),
                        f"{tag} 隨機基準%": fmt(r["rnd"])})
        rows.append(rec)
    return md(pd.DataFrame(rows))


def summary_table(cells: pd.DataFrame) -> str:
    rows = []
    for _, r in cells.iterrows():
        if "best" not in r or not math.isfinite(r.get("best", math.nan)):
            continue
        rows.append({
            "因子": LABEL[r["feature"]], "最好/最差組": f"Q{int(r['best'])}/Q{int(r['worst'])}",
            "訓練 PF 好/差": f"{fmt(r['pf_best_train'])}/{fmt(r['pf_worst_train'])}",
            "訓練 勝率% 好/差": f"{fmt(r['win_best_train'], 1)}/{fmt(r['win_worst_train'], 1)}",
            "訓練 期望差": fmt(r["exp_best_train"] - r["exp_worst_train"]),
            "測試 PF 好/差": f"{fmt(r['pf_best_test'])}/{fmt(r['pf_worst_test'])}",
            "測試 勝率% 好/差": f"{fmt(r['win_best_test'], 1)}/{fmt(r['win_worst_test'], 1)}",
            "測試 期望差": fmt(r["exp_best_test"] - r["exp_worst_test"]),
            "ρ 訓練 [95%CI]": f"{fmt(r['rho_train'], 3)} [{fmt(r['lo_train'], 3)},{fmt(r['hi_train'], 3)}]",
            "ρ 測試 [95%CI]": f"{fmt(r['rho_test'], 3)} [{fmt(r['lo_test'], 3)},{fmt(r['hi_test'], 3)}]",
            "Q5−Q1 訓練 期望差/勝率差/PF": f"{fmt(r['q51_exp_train'])}/{fmt(r['q51_win_train'], 1)}/{r['q51_pf_train']}",
            "Q5−Q1 測試 期望差/勝率差/PF": f"{fmt(r['q51_exp_test'])}/{fmt(r['q51_win_test'], 1)}/{r['q51_pf_test']}",
            "兩段方向一致": "是" if r.get("same_dir") else "否"})
    return md(pd.DataFrame(rows))


# ------------------------------------------------------------------ new score

def select_features(d: pd.DataFrame, market: str, alpha: float) -> list[tuple[str, int]]:
    """Training-only: a pool feature enters when, for BOTH exits, the training Spearman has the same sign, p < alpha,
    and the sign is the same in both halves of the training period. Returns [(feature, sign)]."""
    out = []
    for c in POOL:
        signs, ok = [], True
        for ex in ("TP1", "TP2"):
            g = d[(d["market"] == market) & (d["exit"] == ex) & (d["period"] == "train")].dropna(subset=[c + "_p"])
            rho = S.spearman(g[c + "_p"], g["ret"])
            h = [S.spearman(s[c + "_p"], s["ret"]) for _, s in g.groupby("half")]
            if not (math.isfinite(rho) and rho_p(rho, len(g)) < alpha and all(math.isfinite(x) for x in h)
                    and all(np.sign(x) == np.sign(rho) for x in h)):
                ok = False
                break
            signs.append(np.sign(rho))
        if ok and len(set(signs)) == 1:
            out.append((c, int(signs[0])))
    return out


def attach_new_score(d: pd.DataFrame, picks: dict[str, list[tuple[str, int]]], name: str = "new_score") -> pd.DataFrame:
    """new_score = mean of (sign-adjusted) training-ECDF percentiles of the picked features; NaN -> neutral 0.5.
    Markets with no pick get NaN."""
    d = d.copy()
    d[name] = np.nan
    for m, pk in picks.items():
        if not pk:
            continue
        ix = d["market"] == m
        tot = np.zeros(int(ix.sum()))
        for c, s in pk:
            p = d.loc[ix, c + "_p"].to_numpy(float)
            p = np.where(np.isfinite(p), p, 0.5)
            tot += p if s > 0 else 1.0 - p
        d.loc[ix, name] = tot / len(pk)
    return d


def top_stats(d: pd.DataFrame, market: str, exit_: str, col: str, frac: float, per: str) -> pd.DataFrame:
    """Top `frac` by `col` where the cut is the (1 - frac) quantile of TRAINING values of (market, timeframe)."""
    g = d[(d["market"] == market) & (d["exit"] == exit_)].dropna(subset=[col])
    keep = []
    for tf, s in g.groupby("timeframe"):
        thr = s.loc[s["period"] == "train", col].quantile(1.0 - frac)
        keep.append(s[(s["period"] == per) & (s[col] >= thr)])
    return pd.concat(keep) if keep else g.iloc[0:0]


def main(crypto_split: str = "spec") -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    sfx = "" if crypto_split == "spec" else "_" + crypto_split
    d = load(crypto_split)
    cols = [c for c, _l, _p in FEATS]
    d = add_percentiles(d, cols)
    alpha = 0.05 / (len(FEATS) * 4)
    alpha_sel = 0.05 / (len(POOL) * 2)
    out = []
    out.append(f"# score-study tables (auto-generated, crypto_split={crypto_split})\n\nrows={len(d)} "
               f"alpha_bonf={alpha:.2e} alpha_sel={alpha_sel:.2e}\n")
    cnt = d[d["exit"] == "TP1"].groupby(["market", "timeframe", "period"]).size().unstack()
    out.append("## 樣本數（TP1 訊號）\n\n" + md(cnt.reset_index()) + "\n")
    # ---- merged cells (market x exit)
    merged = [cell_stats(d, m, ex, None, c, boot=True) for m in ("tw", "crypto") for ex in ("TP1", "TP2")
              for c in cols]
    mc = pd.DataFrame(merged)
    mc.to_csv(OUT_DIR / f"cells_merged{sfx}.csv", index=False, encoding="utf-8")
    verd = {(m, c): verdict(mc[(mc["feature"] == c) & (mc["market"] == m)], alpha) for m in ("tw", "crypto") for c in cols}
    per_tf = [cell_stats(d, m, ex, tf, c, boot=False)
              for m, tfs in (("tw", ("4h", "1d", "1w")), ("crypto", ("4h", "1d"))) for tf in tfs
              for ex in ("TP1", "TP2") for c in cols]
    pd.DataFrame(per_tf).to_csv(OUT_DIR / f"cells_tf{sfx}.csv", index=False, encoding="utf-8")

    (OUT_DIR / f"front_table{sfx}.md").write_text(front_table(mc, verd, cols), encoding="utf-8")
    rows = []
    for c in cols:
        r = {"因子": LABEL[c]}
        for m in ("tw", "crypto"):
            for ex in ("TP1", "TP2"):
                x = mc[(mc["feature"] == c) & (mc["market"] == m) & (mc["exit"] == ex)].iloc[0]
                r[f"{MARKET_L[m]} {ex} ρ訓練/ρ測試"] = f"{fmt(x['rho_train'], 3)}/{fmt(x['rho_test'], 3)}"
        r["結論 台股"], r["結論 加密"] = verd[("tw", c)], verd[("crypto", c)]
        rows.append(r)
    out.append("## 各因子單獨效果（ρ）\n\n" + md(pd.DataFrame(rows)) + "\n")
    for m in ("tw", "crypto"):
        for ex in ("TP1", "TP2"):
            out.append(f"## 最好組 vs 最差組：{MARKET_L[m]} {ex}\n\n"
                       + summary_table(mc[(mc["market"] == m) & (mc["exit"] == ex)]) + "\n")
    for m in ("tw", "crypto"):
        for ex in ("TP1", "TP2"):
            for c in cols:
                out.append(f"### 五分位 {MARKET_L[m]} {ex}｜{LABEL[c]}\n\n" + quintile_md(d, m, ex, None, c) + "\n")
    for m, tfs in (("tw", ("4h", "1d", "1w")), ("crypto", ("4h", "1d"))):
        for tf in tfs:
            for ex in ("TP1", "TP2"):
                for c in ("pattern_score", "shoulder_score", "head_depth_score", "neckline_score"):
                    out.append(f"### 五分位 {MARKET_L[m]} {tf} {ex}｜{LABEL[c]}\n\n" + quintile_md(d, m, ex, tf, c) + "\n")
    # ---- new score (selection uses training rows only)
    variants = {"new_strict": (f"新分數(嚴格 α={alpha_sel:.1e})", alpha_sel), "new_loose": ("新分數(寬鬆 α=0.05)", 0.05)}
    picks_all = {}
    for key, (_nm, al) in variants.items():
        picks_all[key] = {m: select_features(d, m, al) for m in ("tw", "crypto")}
        d = attach_new_score(d, picks_all[key], key)
    out.append("## 新分數入選（訓練期，只看訓練）\n\n" + "\n".join(
        f"- {variants[k][0]}｜{MARKET_L[m]}：" + ("、".join(f"{LABEL[c]}({'+' if sg > 0 else '-'})" for c, sg in pk) or "無")
        for k, pks in picks_all.items() for m, pk in pks.items()) + "\n")
    comp = []
    n_trials = len(POOL) + 1
    for m in ("tw", "crypto"):
        for ex in ("TP1", "TP2"):
            for frac in (0.2, 0.3):
                for name, col in [("全部", None), ("現行分數", "pattern_score")] + [(v[0], k) for k, v in variants.items()]:
                    if col is None:
                        s = d[(d["market"] == m) & (d["exit"] == ex) & (d["period"] == "test")]
                    elif col.startswith("new") and not picks_all[col][m]:
                        continue
                    else:
                        s = top_stats(d, m, ex, col, frac, "test")
                    st = S.group_stats(s["ret"], s["win"])
                    r = s["ret"].to_numpy(float)
                    sr = float(r.mean() / r.std(ddof=1)) if len(r) > 2 and r.std(ddof=1) > 0 else math.nan
                    comp.append({"市場": MARKET_L[m], "出場": ex, "前%": int(frac * 100) if col else 100, "組": name,
                                 "n": st["n"], "勝率%": fmt(st["win"], 1), "期望%": fmt(st["mean"]),
                                 "中位數%": fmt(st["median"]), "PF": fmt(st["pf"]),
                                 "隨機基準%": fmt(float(s["rnd"].mean())),
                                 "超額%": fmt(float((s["ret"] - s["rnd"]).mean())), "每筆夏普": fmt(sr, 3)})
    from backtest import edges as G
    dsr_rows = []
    for key, (vname, _al) in variants.items():
        for m in ("tw", "crypto"):
            if not picks_all[key][m]:
                continue
            for ex in ("TP1", "TP2"):
                trial = []
                for c in POOL + ["pattern_score"]:
                    s = top_stats(d, m, ex, c, 0.3, "train")
                    r = s["ret"].to_numpy(float)
                    trial.append(float(r.mean() / r.std(ddof=1)) if len(r) > 2 and r.std(ddof=1) > 0 else math.nan)
                s = top_stats(d, m, ex, key, 0.3, "test")
                r = s["ret"].to_numpy(float)
                mo = G.moments(r)
                res = G.dsr(mo["sr"], mo["n"], mo["skew"], mo["kurt"], trial, n_trials)
                diff = S.bootstrap_mean_diff(r, top_stats(d, m, ex, "pattern_score", 0.3, "test")["ret"].to_numpy(float))
                dsr_rows.append({"版本": vname, "市場": MARKET_L[m], "出場": ex, "候選數": n_trials,
                                 "測試 每筆夏普": fmt(mo["sr"], 3), "SR0(多重比較門檻)": fmt(res["sr0"], 3),
                                 "DSR": fmt(res["dsr"], 3), "新−現行 前30% 期望差%": fmt(diff[0]),
                                 "95%CI": f"[{fmt(diff[1])},{fmt(diff[2])}]"})
    out.append("## 測試期：前 20%／前 30% 組合\n\n" + md(pd.DataFrame(comp)) + "\n")
    out.append("## 新分數 Deflated Sharpe 與差異信賴區間\n\n"
               + (md(pd.DataFrame(dsr_rows)) if dsr_rows else "（無入選因子）") + "\n")
    geo = []
    for m in ("tw", "crypto"):
        g = d[(d["market"] == m) & (d["exit"] == "TP1")]
        for c in ("pattern_score", "shoulder_score", "head_depth_score", "neckline_score"):
            geo.append({"市場": MARKET_L[m], "因子": LABEL[c],
                        "ρ(因子, 停損距離%)": fmt(S.spearman(g[c], g["risk_pct"]), 3),
                        "ρ(因子, TP1距離%)": fmt(S.spearman(g[c], g["tp_dist"]), 3),
                        "ρ(因子, 是否停利)": fmt(S.spearman(g[c], g["win"].astype(float)), 3),
                        "ρ(因子, 報酬%)": fmt(S.spearman(g[c], g["ret"]), 3),
                        "ρ(因子, 報酬%−隨機基準%)": fmt(S.spearman(g[c], g["ret"] - g["rnd"]), 3)})
    out.append("## 分數與交易幾何（全期 TP1）\n\n" + md(pd.DataFrame(geo)) + "\n")
    q51 = []
    for m in ("tw", "crypto"):
        for ex in ("TP1", "TP2"):
            for c in ("pattern_score", "shoulder_score", "head_depth_score", "neckline_score", "pos52", "head_rise_pct",
                      "prior_high_months"):
                g = d[(d["market"] == m) & (d["exit"] == ex)].dropna(subset=[c + "_p"])
                b = bins_of(g[c + "_p"].to_numpy(float))
                rec = {"市場": MARKET_L[m], "出場": ex, "因子": LABEL[c]}
                for per, tag in (("train", "訓練"), ("test", "測試")):
                    hi = g[(g["period"] == per) & (b == 4)]
                    lo = g[(g["period"] == per) & (b == 0)]
                    dm, l, h = S.bootstrap_mean_diff(hi["ret"], lo["ret"], n_boot=500, seed=2)
                    rec[f"{tag} Q5−Q1 期望差%[95%CI]"] = f"{fmt(dm)} [{fmt(l)},{fmt(h)}]"
                    rec[f"{tag} Q5−Q1 超額差%"] = fmt(float((hi["ret"] - hi["rnd"]).mean() - (lo["ret"] - lo["rnd"]).mean()))
                q51.append(rec)
    out.append("## Q5 對 Q1：期望值差與扣除隨機基準後的超額差\n\n" + md(pd.DataFrame(q51)) + "\n")
    d.to_csv(OUT_DIR / f"merged_with_scores{sfx}.csv.gz", index=False, encoding="utf-8")
    (OUT_DIR / f"tables{sfx}.md").write_text("\n".join(out), encoding="utf-8")
    print("wrote", OUT_DIR / f"tables{sfx}.md")
