"""Score study (charter D50): does the pattern score / each sub-score predict the D40 backtest outcome?

Pure functions only (no network). backtest/score_study_run.py does the IO, the per-symbol workers and the report.

Feature rebuild: for every mode-A IHS signal of the D40 backtest the detector view is re-run ONCE on exactly the
window the backtest used (engine.window(df, t, tf) = bars <= t only) and the row with the same pattern_id is read.
Extra features (52-week position, months since the prior high, ...) use bars <= t (or < LS) only; tests lock this.
"""
from __future__ import annotations

import math
from typing import Any, Sequence

import numpy as np
import pandas as pd

SCORE_COLS = ("pattern_score", "shape_score", "shoulder_score", "head_depth_score", "neckline_score", "breakout_score")
RAW_COLS = ("head_depth", "head_atr_multiple", "head_rise_pct", "volume_ratio", "shoulder_diff", "neckline_angle",
            "neckline_total_slope", "head_prominence")
DAY_NS = 86_400 * 10 ** 9


# ------------------------------------------------------------------ extra (causal) features

def range_position(high: np.ndarray, low: np.ndarray, close: np.ndarray, times_ns: np.ndarray, t: int,
                   days: int = 365) -> dict[str, float]:
    """52-week features at bar t from bars with time in (times[t]-days, times[t]] only.
    Returns pos52 = (close-lo)/(hi-lo) and near52 = close/hi (hi = highest HIGH). NaN when the history does not
    cover the whole span (first bar later than times[t]-days)."""
    out = {"pos52": math.nan, "near52": math.nan}
    t0 = times_ns[t] - days * DAY_NS
    if times_ns[0] > t0:
        return out
    lo_i = int(np.searchsorted(times_ns, t0, side="right"))
    hi = float(np.max(high[lo_i:t + 1]))
    lo = float(np.min(low[lo_i:t + 1]))
    if hi > lo:
        out["pos52"] = (float(close[t]) - lo) / (hi - lo)
    if hi > 0:
        out["near52"] = float(close[t]) / hi
    return out


def months_since_prior_high(high: np.ndarray, times_ns: np.ndarray, ls_i: int, days: int = 730) -> float:
    """Months from the highest HIGH in the `days` before the LS bar (bars < LS only) to the LS bar. NaN when the
    history is shorter than `days` (the peak could be older), so the feature is never censored by the data start."""
    t0 = times_ns[ls_i] - days * DAY_NS
    if times_ns[0] > t0 or ls_i < 1:
        return math.nan
    lo_i = int(np.searchsorted(times_ns, t0, side="left"))
    seg = high[lo_i:ls_i]
    if seg.size == 0:
        return math.nan
    k = int(np.argmax(seg))
    return float(times_ns[ls_i] - times_ns[lo_i + k]) / DAY_NS / 30.4375


def prior_decline_depth(high: np.ndarray, low: np.ndarray, ls_i: int, lookback: int) -> float:
    """LS low / highest high in the lookback before LS - 1 (bars < LS)."""
    lo = max(0, ls_i - lookback)
    if ls_i - lo < 1:
        return math.nan
    peak = float(np.max(high[lo:ls_i]))
    return float(low[ls_i]) / peak - 1.0 if peak > 0 else math.nan


# ------------------------------------------------------------------ statistics

def profit_factor(r: Sequence[float]) -> float:
    """Sum of gains / sum of losses (returns in %, after costs). inf when there is no loss, NaN when empty."""
    a = np.asarray(r, dtype=float)
    a = a[np.isfinite(a)]
    if a.size == 0:
        return math.nan
    gain, loss = float(a[a > 0].sum()), float(-a[a < 0].sum())
    if loss == 0:
        return math.inf if gain > 0 else math.nan
    return gain / loss


def spearman(x: Sequence[float], y: Sequence[float]) -> float:
    s = pd.DataFrame({"x": np.asarray(x, float), "y": np.asarray(y, float)}).dropna()
    if len(s) < 3 or s["x"].nunique() < 2 or s["y"].nunique() < 2:
        return math.nan
    return float(s["x"].rank().corr(s["y"].rank()))


def bootstrap_spearman(x: Sequence[float], y: Sequence[float], n_boot: int = 500, seed: int = 0
                       ) -> tuple[float, float, float]:
    """(rho, lo, hi): Spearman and a 95% percentile bootstrap interval (pairs resampled)."""
    s = pd.DataFrame({"x": np.asarray(x, float), "y": np.asarray(y, float)}).dropna()
    rho = spearman(s["x"], s["y"])
    if len(s) < 30 or not math.isfinite(rho):
        return rho, math.nan, math.nan
    rx, ry = s["x"].rank().to_numpy(), s["y"].rank().to_numpy()
    rng = np.random.default_rng(seed)
    n = len(s)
    vals = np.empty(n_boot)
    for b in range(n_boot):
        i = rng.integers(0, n, n)
        # re-rank inside the resample so ties/ranks are those of the resample
        a, c = pd.Series(s["x"].to_numpy()[i]).rank().to_numpy(), pd.Series(s["y"].to_numpy()[i]).rank().to_numpy()
        sa, sc = a.std(), c.std()
        vals[b] = float(np.mean((a - a.mean()) * (c - c.mean())) / (sa * sc)) if sa > 0 and sc > 0 else math.nan
    return rho, float(np.nanpercentile(vals, 2.5)), float(np.nanpercentile(vals, 97.5))


def quintile_edges(train_values: Sequence[float], k: int = 5) -> np.ndarray:
    """Interior cut points (k-1) from the TRAINING values only (quantiles; duplicates collapsed)."""
    v = np.asarray(train_values, float)
    v = v[np.isfinite(v)]
    if v.size == 0:
        return np.array([])
    return np.unique(np.quantile(v, np.linspace(0, 1, k + 1)[1:-1]))


def assign_bins(values: Sequence[float], edges: np.ndarray) -> np.ndarray:
    """Group index 0..len(edges) for each value (NaN -> -1). Group g holds edges[g-1] < v <= edges[g]
    (right-closed, so a point mass at the lowest edge stays in group 0)."""
    v = np.asarray(values, float)
    out = np.searchsorted(edges, v, side="left").astype(int)
    out[~np.isfinite(v)] = -1
    return out


def group_stats(ret_pct: Sequence[float], win: Sequence[bool]) -> dict[str, float]:
    r = np.asarray(ret_pct, float)
    w = np.asarray(win, float)
    n = int(r.size)
    if n == 0:
        return {"n": 0, "win": math.nan, "mean": math.nan, "median": math.nan, "pf": math.nan}
    return {"n": n, "win": float(w.mean() * 100.0), "mean": float(r.mean()), "median": float(np.median(r)),
            "pf": profit_factor(r)}


def bootstrap_mean_diff(a: Sequence[float], b: Sequence[float], n_boot: int = 1000, seed: int = 0
                        ) -> tuple[float, float, float]:
    """mean(a) - mean(b) with a 95% percentile bootstrap interval (independent resamples)."""
    a, b = np.asarray(a, float), np.asarray(b, float)
    if a.size < 5 or b.size < 5:
        return math.nan, math.nan, math.nan
    rng = np.random.default_rng(seed)
    d = np.empty(n_boot)
    for i in range(n_boot):
        d[i] = rng.choice(a, a.size).mean() - rng.choice(b, b.size).mean()
    return float(a.mean() - b.mean()), float(np.percentile(d, 2.5)), float(np.percentile(d, 97.5))


def rank_sum_score(features: pd.DataFrame, cols: Sequence[str], train_mask: np.ndarray,
                   signs: Sequence[int]) -> np.ndarray:
    """Equal-weight sum of percentile ranks. Each feature is ranked by its TRAINING empirical CDF (so a test row's
    rank never depends on other test rows), multiplied by its sign (+1 high is good, -1 low is good) and averaged.
    NaN feature -> neutral rank 0.5."""
    total = np.zeros(len(features))
    for c, s in zip(cols, signs):
        v = features[c].to_numpy(float)
        tr = np.sort(v[train_mask & np.isfinite(v)])
        if tr.size == 0:
            r = np.full(len(v), 0.5)
        else:
            r = np.searchsorted(tr, v, side="right") / tr.size
            r[~np.isfinite(v)] = 0.5
        total += r if s > 0 else 1.0 - r
    return total / len(cols)


# ------------------------------------------------------------------ feature worker

def signal_features(df: pd.DataFrame, timeframe: str, market: str, symbol: str, t: int, pattern_id: str
                    ) -> dict[str, Any] | None:
    """All features of one signal, from df[:t+1] only. None when the pattern is not in the as-of-t view."""
    from backtest import engine as E  # local import: keeps this module importable without the detector

    cfg = E.ihs_config(market)
    win = E.window(df, t, timeframe)
    rows = E.ihs_view(win, timeframe, cfg, symbol)
    row = next((r for r in rows if r["pattern_id"] == pattern_id), None)
    if row is None:
        return None
    out: dict[str, Any] = {k: row.get(k) for k in SCORE_COLS + RAW_COLS}
    out["status"] = row.get("status")
    high = df["high"].to_numpy(float)[: t + 1]
    low = df["low"].to_numpy(float)[: t + 1]
    close = df["close"].to_numpy(float)[: t + 1]
    ts = pd.to_datetime(df["timestamp"], utc=True)
    times = ts.astype("datetime64[ns, UTC]").astype("int64").to_numpy()[: t + 1]
    out.update(range_position(high, low, close, times, t))
    ls_t = pd.Timestamp(row["LS_time"])
    ls_ns = int(ls_t.tz_convert("UTC").value if ls_t.tzinfo else ls_t.tz_localize("UTC").value)
    ls_i = int(np.searchsorted(times, ls_ns))
    if ls_i < len(times) and times[ls_i] == ls_ns:
        out["prior_high_months"] = months_since_prior_high(high, times, ls_i)
        out["ls_i"] = ls_i
    else:
        out["prior_high_months"] = math.nan
    return out
