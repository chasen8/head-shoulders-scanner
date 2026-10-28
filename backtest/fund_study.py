"""Fundamentals study (charter D51): monthly-revenue YoY and foreign / investment-trust net buying as filters for the
TW IHS signals of the D40 backtest. Pure functions only (no network, no file IO); fund_study_run.py does the IO.

Time alignment (all locked by tests/test_fund_study.py):
  * revenue of month M is usable from day 11 of month M+1 (the legal deadline is day 10);
  * institutional data of trading day d is usable for an entry on date E only when d is the `lag`-th trading day
    strictly before E (lag=1: previous trading day = the signal bar's own day; lag=2: one extra day of safety,
    the default of the study). Rolling windows end on that cutoff day and never include later days.
"""
from __future__ import annotations

import datetime as dt
import math

import numpy as np
import pandas as pd

from backtest.fund_data import cutoff_trading_day, revenue_available_month

REV_FEATS = ("yoy1", "yoy3", "yoy_high12")
INST_FEATS = ("f5", "f20", "t5", "t20", "ft5", "ft20")
ALL_FEATS = REV_FEATS + INST_FEATS
LABEL = {
    "yoy1": "單月營收 YoY", "yoy3": "近3月營收 YoY 平均", "yoy_high12": "營收 YoY 創12個月新高(0/1)",
    "f5": "外資近5日買賣超/成交量", "f20": "外資近20日買賣超/成交量",
    "t5": "投信近5日買賣超/成交量", "t20": "投信近20日買賣超/成交量",
    "ft5": "外資+投信近5日/成交量", "ft20": "外資+投信近20日/成交量",
}


# ------------------------------------------------------------------ revenue

def revenue_features(yoy: pd.Series, avail: pd.Period) -> dict[str, float]:
    """yoy: YoY fractions indexed by revenue month (pd.Period 'M'). Uses ONLY months <= avail.
    yoy1 = YoY of month `avail`; yoy3 = mean of the 3 months ending at `avail` (all 3 required);
    yoy_high12 = 1.0 when yoy1 >= every YoY of the trailing 12 months ending at `avail` (all 12 required), else 0.0."""
    out = {"yoy1": math.nan, "yoy3": math.nan, "yoy_high12": math.nan, "rev_month": str(avail)}
    s = yoy[yoy.index <= avail]
    if avail not in s.index or not np.isfinite(s.loc[avail]):
        return out
    out["yoy1"] = float(s.loc[avail])
    w3 = [avail - k for k in range(3)]
    if all(m in s.index and np.isfinite(s.loc[m]) for m in w3):
        out["yoy3"] = float(np.mean([s.loc[m] for m in w3]))
    w12 = [avail - k for k in range(12)]
    if all(m in s.index and np.isfinite(s.loc[m]) for m in w12):
        out["yoy_high12"] = 1.0 if s.loc[avail] >= max(s.loc[m] for m in w12) else 0.0
    return out


# ------------------------------------------------------------------ institutional

def inst_panel(cal: list[dt.date], fetched: set[dt.date], net: pd.DataFrame, vol: pd.Series,
               windows=(5, 20)) -> pd.DataFrame:
    """Per-symbol rolling features indexed by trading date (cal).
    net: columns date, foreign_net, trust_net (shares) for this symbol; vol: shares traded, indexed by date.
    A calendar day whose T86 report was not downloaded is NaN (any window containing it is NaN); a downloaded day
    where the symbol is absent counts as 0 net. Ratio = sum(net over the window) / sum(volume over the same window)."""
    idx = pd.Index(cal)
    n = net.drop_duplicates("date").set_index("date")
    fnet = n["foreign_net"].reindex(idx).fillna(0.0).to_numpy(float).copy()
    tnet = n["trust_net"].reindex(idx).fillna(0.0).to_numpy(float).copy()
    missing = np.array([d not in fetched for d in cal])
    fnet[missing], tnet[missing] = np.nan, np.nan
    v = pd.Series(vol.reindex(idx).to_numpy(float), index=idx)
    out = {}
    for w in windows:
        vs = v.rolling(w, min_periods=w).sum().replace(0.0, np.nan)
        for name, a in (("f", fnet), ("t", tnet), ("ft", fnet + tnet)):
            out[f"{name}{w}"] = pd.Series(a, index=idx).rolling(w, min_periods=w).sum() / vs
            out[f"{name}{w}_raw"] = pd.Series(a, index=idx).rolling(w, min_periods=w).sum()
        out[f"vol{w}_raw"] = v.rolling(w, min_periods=w).sum()
    return pd.DataFrame(out, index=idx)


def signal_row(entry_date: dt.date, yoy: pd.Series | None, panel: pd.DataFrame | None, cal: list[dt.date],
               lag: int) -> dict:
    """All fundamentals features of one signal, given the entry date. Uses only data dated before the entry."""
    row: dict = {}
    avail = revenue_available_month(entry_date)
    row["rev_avail_month"] = str(avail)
    row.update(revenue_features(yoy, avail) if yoy is not None and len(yoy) else
               {"yoy1": math.nan, "yoy3": math.nan, "yoy_high12": math.nan, "rev_month": str(avail)})
    cut = cutoff_trading_day(cal, entry_date, lag)
    row["inst_cutoff"] = cut.isoformat() if cut else ""
    for c in INST_FEATS:
        row[c] = math.nan
    if cut is not None and panel is not None and cut in panel.index:
        r = panel.loc[cut]
        for c in INST_FEATS:
            row[c] = float(r[c])
        for c in ("f5_raw", "f20_raw", "t5_raw", "t20_raw", "vol5_raw", "vol20_raw"):
            row[c] = float(r[c])
    return row


def tw_date(ts: pd.Timestamp) -> dt.date:
    """Taipei calendar date of a UTC timestamp (daily bars are stamped 01:00 UTC = 09:00 Taipei)."""
    return (ts + pd.Timedelta(hours=8)).date()


# ------------------------------------------------------------------ rules (decided on training only)

def apply_rule(d: pd.DataFrame, rule: dict[str, float]) -> np.ndarray:
    """rule: {feature: min_value}; every feature must be > value (NaN fails). Empty rule keeps everything."""
    ok = np.ones(len(d), bool)
    for c, thr in rule.items():
        v = d[c].to_numpy(float)
        ok &= np.isfinite(v) & (v > thr)
    return ok
