"""D51 fundamentals study: quintile tables, rho + bootstrap CI, multiple-comparison verdicts, simple rules, examples.
Reads OUT_DIR/fund_features.csv.gz (fund_study_run features) and the D50 merged_with_scores.csv.gz.

Method follows score_study_report (D50): train = entries 2017-11 .. 2021, test = 2022 .. 2026-10 (frozen); each
feature is first turned into its percentile among the TRAINING rows of the same timeframe, the five groups are the
0-20 .. 80-100 percentile bands of that training distribution; baseline = D42 "same symbol random entry, same
stop/target" (column rnd). Bonferroni alpha = 0.05 / (number of features x 2 exits).
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from backtest import fund_study as FS
from backtest import score_study as S
from backtest import score_study_report as R0
from backtest.fund_study_run import FEATURES, KEY, OUT_DIR, load_signals

FEATS = list(FS.ALL_FEATS)
LABEL = dict(FS.LABEL)
LABEL["pos52"] = "52週區間位置(D50 對照)"
R0.LABEL.update(LABEL)
EXITS = ("TP1", "TP2")
TFS_MAIN = ("1d", "1w")


def load(prefix: str = "", tfs=TFS_MAIN) -> pd.DataFrame:
    sc = load_signals()
    sc = sc[sc["period"].isin(["train", "test"]) & sc["timeframe"].isin(tfs)]
    f = pd.read_csv(FEATURES, encoding="utf-8", low_memory=False)
    f["symbol"] = f["symbol"].astype(str)
    cols = FEATS + ["f5_raw", "f20_raw", "t5_raw", "t20_raw", "vol5_raw", "vol20_raw", "rev_month", "inst_cutoff"]
    ren = {prefix + c: c for c in cols if prefix + c in f.columns}
    f = f[KEY + ["entry_date"] + list(ren)].rename(columns=ren)
    d = sc.drop(columns=[c for c in FEATS if c in sc.columns]).merge(f, on=KEY, how="left").reset_index(drop=True)
    d["ret"] = d["ret"].astype(float)
    d = R0.add_percentiles(d, FEATS + ["pos52"])
    hi = d["yoy_high12"].to_numpy(float)  # binary: put the two groups into Q1 / Q5
    d["yoy_high12_p"] = np.where(np.isfinite(hi), np.where(hi > 0, 0.9, 0.1), np.nan)
    return d


fmt = R0.fmt
md = R0.md


def coverage(d: pd.DataFrame) -> str:
    rows = []
    for (tf, per), g in d[d["exit"] == "TP1"].groupby(["timeframe", "period"]):
        rec = {"週期": tf, "期間": "訓練" if per == "train" else "測試", "訊號數": len(g)}
        for c in FEATS:
            rec[LABEL[c].split("(")[0]] = f"{g[c].notna().mean() * 100:.0f}%"
        rows.append(rec)
    return md(pd.DataFrame(rows))


def quintile_md(d: pd.DataFrame, exit_: str, col: str, tf: str | None = None) -> str:
    g = d[d["exit"] == exit_]
    if tf:
        g = g[g["timeframe"] == tf]
    g = g.dropna(subset=[col + "_p"])
    q = R0.quintile_table(g, col)
    rows = []
    for k in range(1, 6):
        if col == "yoy_high12" and k not in (1, 5):
            continue
        rec = {"組": ("否" if k == 1 else "是") if col == "yoy_high12" else f"Q{k}"}
        for per, tag in (("train", "訓練"), ("test", "測試")):
            r = q[(q["period"] == per) & (q["group"] == k)].iloc[0]
            rec.update({f"{tag} n": int(r["n"]), f"{tag} 勝率%": fmt(r["win"], 1), f"{tag} 平均%": fmt(r["mean"]),
                        f"{tag} 中位數%": fmt(r["median"]), f"{tag} PF": fmt(r["pf"]),
                        f"{tag} 超額%": fmt(r["mean"] - r["rnd"])})
        rows.append(rec)
    # value range of each group (training edges), for reading the table
    return md(pd.DataFrame(rows))


def group_ranges(d: pd.DataFrame, col: str) -> str:
    """Raw value ranges of the training-percentile groups (pooled 1d+1w), to make the quintiles readable."""
    g = d[(d["exit"] == "TP1") & (d["period"] == "train")].dropna(subset=[col, col + "_p"])
    b = R0.bins_of(g[col + "_p"].to_numpy(float))
    parts = []
    for k in range(5):
        v = g.loc[b == k, col]
        if len(v):
            parts.append(f"Q{k + 1}: {v.min():.4g}~{v.max():.4g}")
    return "；".join(parts)


def rules_table(d: pd.DataFrame, rules: dict[str, dict]) -> tuple[str, pd.DataFrame]:
    rows = []
    for ex in EXITS:
        g = d[d["exit"] == ex]
        for name, rule in rules.items():
            keep = FS.apply_rule(g, rule)
            for per, tag in (("train", "訓練"), ("test", "測試")):
                m = (g["period"] == per).to_numpy()
                a, k, dropped = g[m], g[m & keep], g[m & ~keep]
                base = S.group_stats(a["ret"], a["win"])
                st = S.group_stats(k["ret"], k["win"])
                diff, lo, hi = S.bootstrap_mean_diff(k["ret"], dropped["ret"], n_boot=1000, seed=3)
                rows.append({"出場": ex, "規則": name, "期間": tag, "全部 n": base["n"], "保留 n": st["n"],
                             "保留%": fmt(100 * st["n"] / max(base["n"], 1), 0),
                             "全部 勝率%": fmt(base["win"], 1), "保留 勝率%": fmt(st["win"], 1),
                             "全部 平均%": fmt(base["mean"]), "保留 平均%": fmt(st["mean"]),
                             "保留 中位數%": fmt(st["median"]), "全部 PF": fmt(base["pf"]), "保留 PF": fmt(st["pf"]),
                             "全部 超額%": fmt(float((a["ret"] - a["rnd"]).mean())),
                             "保留 超額%": fmt(float((k["ret"] - k["rnd"]).mean())) if len(k) else "—",
                             "保留−剔除 平均差% [95%CI]": f"{fmt(diff)} [{fmt(lo)},{fmt(hi)}]"})
    df = pd.DataFrame(rows)
    return md(df), df


def make_rules(d: pd.DataFrame) -> dict[str, dict]:
    """Fixed a priori (before any result was looked at); only the pos52 cut uses the training data (60th percentile)."""
    tr = d[(d["exit"] == "TP1") & (d["period"] == "train")]
    p60 = float(tr["pos52"].quantile(0.6))
    return {
        "營收YoY>0": {"yoy1": 0.0},
        "外資20日買超>0": {"f20": 0.0},
        "營收YoY>0 且 外資20日>0": {"yoy1": 0.0, "f20": 0.0},
        "外資+投信20日>0": {"ft20": 0.0},
        "營收3月YoY>0 且 外資+投信20日>0": {"yoy3": 0.0, "ft20": 0.0},
        f"52週位置>{p60:.2f}(訓練前40%)": {"pos52": p60},
        f"營收YoY>0 且 外資20日>0 且 52週位置>{p60:.2f}": {"yoy1": 0.0, "f20": 0.0, "pos52": p60},
    }


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    d = load("")
    n_feat = len(FEATS)
    alpha = 0.05 / (n_feat * len(EXITS))
    out = [f"# fundamentals-study tables (auto)\n\nrows={len(d)} features={n_feat} alpha_bonf={alpha:.2e}\n"]
    out.append("## 覆蓋率（TP1 訊號，各特徵非缺值比例）\n\n" + coverage(d) + "\n")
    cells = pd.DataFrame([R0.cell_stats(d, "tw", ex, None, c, boot=True) for ex in EXITS for c in FEATS + ["pos52"]])
    cells.to_csv(OUT_DIR / "cells_merged.csv", index=False, encoding="utf-8")
    rows = []
    for c in FEATS + ["pos52"]:
        r = {"因子": LABEL[c]}
        for ex in EXITS:
            x = cells[(cells["feature"] == c) & (cells["exit"] == ex)].iloc[0]
            r[f"{ex} ρ訓練 [CI]"] = f"{fmt(x['rho_train'], 3)} [{fmt(x['lo_train'], 3)},{fmt(x['hi_train'], 3)}] p={fmt(x['p_train'], 4)}"
            r[f"{ex} ρ測試 [CI]"] = f"{fmt(x['rho_test'], 3)} [{fmt(x['lo_test'], 3)},{fmt(x['hi_test'], 3)}] p={fmt(x['p_test'], 4)}"
            r[f"{ex} Q5−Q1 期望差 訓/測"] = f"{fmt(x['q51_exp_train'])}/{fmt(x['q51_exp_test'])}"
            r[f"{ex} Q5/Q1 PF 訓"] = x["q51_pf_train"]
            r[f"{ex} Q5/Q1 PF 測"] = x["q51_pf_test"]
        sub = cells[cells["feature"] == c]
        r["結論"] = R0.verdict(sub, alpha)
        rows.append(r)
    out.append(f"## 各因子 ρ（1d+1w，Bonferroni α={alpha:.2e}）\n\n" + md(pd.DataFrame(rows)) + "\n")
    for ex in EXITS:
        for c in FEATS + ["pos52"]:
            out.append(f"### 五分位 {ex}｜{LABEL[c]}\n\n（訓練期分組範圍 {group_ranges(d, c)}）\n\n" + quintile_md(d, ex, c) + "\n")
    for tf in TFS_MAIN:
        for ex in EXITS:
            for c in FEATS:
                out.append(f"### 五分位 {tf} {ex}｜{LABEL[c]}\n\n" + quintile_md(d, ex, c, tf) + "\n")
    rules = make_rules(d)
    t, df = rules_table(d, rules)
    df.to_csv(OUT_DIR / "rules.csv", index=False, encoding="utf-8")
    out.append("## 簡單門檻（事前定好，不調參；保留＝符合規則）\n\n" + t + "\n")
    # sensitivity: lag 1 (previous trading day instead of the day before), and 4h
    d1 = load("l1_")
    cells1 = pd.DataFrame([R0.cell_stats(d1, "tw", ex, None, c, boot=False) for ex in EXITS for c in FEATS])
    out.append("## 敏感度：法人資料只落後 1 個交易日（訊號當天收盤後公布者即用）ρ訓練/ρ測試\n\n" + md(pd.DataFrame(
        [{"因子": LABEL[c], **{f"{ex}": f"{fmt(x['rho_train'], 3)}/{fmt(x['rho_test'], 3)}"
                              for ex in EXITS for x in [cells1[(cells1['feature'] == c) & (cells1['exit'] == ex)].iloc[0]]}}
         for c in FEATS])) + "\n")
    out.append("## 附：4h 訊號\n\n4h 台股 K 線只從 2023-10 起，沒有訓練期，無法做訓練／測試切分，本研究不含 4h。\n")
    (OUT_DIR / "tables.md").write_text("\n".join(out), encoding="utf-8")
    print("wrote", OUT_DIR / "tables.md")


def examples() -> None:
    """Two hand-checkable signals: print the raw MOPS and T86 numbers behind the features."""
    from backtest import fund_data as FD
    d = load("")
    d = d[(d["exit"] == "TP1") & (d["timeframe"] == "1d") & (d["period"] == "test")].dropna(subset=FEATS)
    d = d[d["vol5_raw"] > 2e6]
    pick = d.sort_values("when").iloc[[len(d) // 5, 3 * len(d) // 5]]
    rev = FD.load_revenue()
    out = []
    for _, r in pick.iterrows():
        sym, ed = str(r["symbol"]), r["entry_date"]
        out.append(f"### {sym}（{r['source']}）1d 訊號，進場日 {ed}\n")
        ym = pd.Period(r["rev_month"], "M")
        rr = rev[(rev["code"] == sym) & (rev["ym"] <= ym) & (rev["ym"] > ym - 3)].sort_values("ym")
        out.append("營收（千元，MOPS 原始值）：\n\n" + md(rr[["ym", "rev", "rev_ly", "yoy_pct"]].astype(str)) + "\n")
        out.append(f"→ 可用最新月份 {r['rev_month']}，單月 YoY={r['yoy1'] * 100:.2f}%（rev/rev_ly−1 手算："
                   f"{rr.iloc[-1]['rev'] / rr.iloc[-1]['rev_ly'] * 100 - 100:.2f}%），近3月平均 {r['yoy3'] * 100:.2f}%\n")
        inst = FD.load_inst() if "_inst" not in globals() else globals()["_inst"]
        globals()["_inst"] = inst
        cut = pd.Timestamp(r["inst_cutoff"]).date()
        days5 = [x for x in FD.trading_days() if x <= cut][-5:]
        one = inst[(inst["code"] == sym) & (inst["market"] == r["source"])].drop_duplicates("date").set_index("date")
        net = one.reindex(days5).fillna(0.0).rename_axis("date").reset_index()  # absent day = 0 net
        out.append(f"法人（股，T86 原始值）截至 {cut}（進場日前第 2 個交易日）最後 5 個交易日：\n\n"
                   + md(net[["date", "foreign_net", "trust_net"]].astype(str)) + "\n")
        out.append(f"→ 外資5日合計 {net['foreign_net'].sum():,.0f} 股；表中 f5_raw={r['f5_raw']:,.0f}；"
                   f"5日成交量 {r['vol5_raw']:,.0f} 股 → f5={r['f5']:.4f}\n")
    (OUT_DIR / "examples.md").write_text("\n".join(out), encoding="utf-8")
    print("\n".join(out))


def equity() -> None:
    raise SystemExit("not run: no feature was consistently effective in both periods (see docs/fundamentals-study.md), "
                     "so the D44 account simulation was skipped by the pre-set rule")
