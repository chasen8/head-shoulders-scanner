"""D44: three evidence-based "edges" (docs/edge-research.md recommendations 1-3) tested on the D43 equity-curve
framework. Pure functions only (no network, no file IO); backtest/edges_run.py does the IO, the runs, the
train/test selection and the Excel workbook.

Everything here is causal on the daily grid (benchmark trading days): a value at grid day d uses only
px[:, :d+1] / bench[:d+1] (asserted by tests/test_backtest_edges.py truncation tests).

1. Market regime switch (benchmark trend):
   sma_regime    bench[d] > mean(bench[d-n+1 .. d])                      (n = 200 grid days)
   faber_regime  last month-end close > mean of the last 10 month-end closes, using only months that ended
                 BEFORE the month of day d (a month-end is only known once the next month has started).
   Unknown (not enough history) -> NaN; the caller decides (edges_run starts after every feature exists).
2. Volatility: realized_vol = std of the last n daily log returns * sqrt(periods per year).
3. Relative strength on the grid matrix px[sym, d]:
   rel_strength  (px[d] / px[d-L]) / (bench[d] / bench[d-L]) - 1           (L = 6 months)
   near_high     px[d] / max(px[d-L+1 .. d])                               (L = 52 weeks, closes)
   xs_percentile cross-sectional percentile rank among the symbols with a finite value on day d (0..1].

equity_ext: the D43 account (exits.equity_curve) plus
   allow[d]      new entries only when True (regime switch); exit_on_off -> close every open position at
                 px[sym, d] on a day with allow False (strategy sell cost).
   idle_w[d]     fraction of the idle money held in the benchmark (rest 0% cash); rebalanced after the day's
                 exits when |current - target| > band, or when the target is 0 or 1 and differs (bench costs).
   weights[k]    position size as a fraction of equity per trade (default risk / stop distance, = D43),
                 capped at max_w; risk_mult[d] scales every new position on day d (account-level vol target).
   With all defaults it reproduces exits.equity_curve exactly (tested).
Statistics: Probabilistic / Deflated Sharpe Ratio (Bailey & Lopez de Prado 2014).
"""
from __future__ import annotations

import math
from statistics import NormalDist
from typing import Any, Sequence

import numpy as np
import pandas as pd

from backtest import exits as X

EULER = 0.5772156649015329
_N = NormalDist()


# ------------------------------------------------------------------ regime switches

def sma_regime(bench: np.ndarray, n: int = 200) -> np.ndarray:
    """1.0 when bench[d] > SMA(n) of bench[d-n+1 .. d], 0.0 when not, NaN for the first n-1 days."""
    v = np.asarray(bench, dtype=float)
    out = np.full(v.shape, np.nan)
    if v.size >= n:
        cs = np.concatenate([[0.0], np.cumsum(v)])
        sma = (cs[n:] - cs[:-n]) / n
        out[n - 1:] = (v[n - 1:] > sma).astype(float)
    return out


def month_keys(times_ns: np.ndarray, tz: str = "UTC") -> np.ndarray:
    """year * 12 + month of each grid day (in `tz`)."""
    ts = pd.to_datetime(np.asarray(times_ns, dtype=np.int64), utc=True).tz_convert(tz)
    return (ts.year * 12 + ts.month).to_numpy(np.int64)


def faber_regime(bench: np.ndarray, times_ns: np.ndarray, months: int = 10, tz: str = "UTC") -> np.ndarray:
    """1.0 / 0.0 / NaN: the last completed month-end close > the mean of the last `months` completed month-end
    closes. Only months strictly before the month of day d count (no look-ahead on 'is today the last day')."""
    v = np.asarray(bench, dtype=float)
    mk = month_keys(times_ns, tz)
    out = np.full(v.shape, np.nan)
    ends: list[float] = []      # month-end closes of completed months
    cur = None
    for d in range(v.size):
        if cur is not None and mk[d] != cur:
            ends.append(float(v[d - 1]))      # day d-1 was the last grid day of the previous month
        cur = mk[d]
        if len(ends) >= months:
            w = ends[-months:]
            out[d] = float(w[-1] > sum(w) / months)
    return out


# ------------------------------------------------------------------ volatility / relative strength

def realized_vol(px: np.ndarray, n: int = 20, per_year: float = 252.0) -> np.ndarray:
    """Annualized std of the last n daily log returns (rows = symbols when 2-D, columns = grid days).
    NaN until n returns exist."""
    a = np.asarray(px, dtype=float)
    two_d = a.ndim == 2
    m = a if two_d else a[None, :]
    with np.errstate(divide="ignore", invalid="ignore"):
        lr = np.log(m[:, 1:] / m[:, :-1])
    df = pd.DataFrame(lr.T)
    sd = df.rolling(n, min_periods=n).std(ddof=1).to_numpy().T * math.sqrt(per_year)
    out = np.concatenate([np.full((m.shape[0], 1), np.nan), sd], axis=1)
    return out if two_d else out[0]


def rel_strength(px: np.ndarray, bench: np.ndarray, lookback: int) -> np.ndarray:
    """(px[d]/px[d-L]) / (bench[d]/bench[d-L]) - 1 on the grid (NaN when either end is missing)."""
    m = np.asarray(px, dtype=float)
    b = np.asarray(bench, dtype=float)
    out = np.full(m.shape, np.nan)
    L = lookback
    if m.shape[1] > L:
        with np.errstate(divide="ignore", invalid="ignore"):
            out[:, L:] = (m[:, L:] / m[:, :-L]) / (b[L:] / b[:-L])[None, :] - 1.0
    return out


def near_high(px: np.ndarray, lookback: int) -> np.ndarray:
    """px[d] / max(px[d-L+1 .. d]) (closing high over the last L grid days; NaN until L finite values)."""
    m = np.asarray(px, dtype=float)
    mx = pd.DataFrame(m.T).rolling(lookback, min_periods=lookback).max().to_numpy().T
    with np.errstate(divide="ignore", invalid="ignore"):
        return m / mx


def xs_percentile(mat: np.ndarray) -> np.ndarray:
    """Cross-sectional percentile (pandas rank pct, ties = average) of each column among finite values."""
    df = pd.DataFrame(np.asarray(mat, dtype=float))
    return df.rank(axis=0, pct=True, method="average").to_numpy()


def score_order(scores: np.ndarray, d0: np.ndarray) -> np.ndarray:
    """Permutation for equity_ext(order=...): same-day entries by score descending (NaN last, ties by index)."""
    s = np.where(np.isfinite(scores), -np.asarray(scores, dtype=float), np.inf)
    return np.lexsort((np.arange(len(s)), s, np.asarray(d0)))


def vol_weights(sigma: np.ndarray, c: float, floor: float, max_w: float) -> np.ndarray:
    """Per-trade position size (fraction of equity) = c / max(sigma, floor), capped; NaN sigma -> c / median."""
    s = np.asarray(sigma, dtype=float)
    med = float(np.nanmedian(s)) if np.isfinite(s).any() else floor
    s = np.where(np.isfinite(s), s, med)
    return np.minimum(c / np.maximum(s, floor), max_w)


# ------------------------------------------------------------------ equity simulation

def equity_ext(px: np.ndarray, trades: Sequence[X.SimTrade], *, bench: np.ndarray | None, risk: float = 0.01,
               max_pos: int = 10, costs: dict[str, float], bench_costs: dict[str, float] | None = None,
               start: int = 0, order: np.ndarray | None = None, weights: np.ndarray | None = None,
               max_w: float = math.inf, allow: np.ndarray | None = None, exit_on_off: bool = False,
               idle_w: np.ndarray | None = None, band: float = 0.0,
               risk_mult: np.ndarray | None = None) -> dict[str, Any]:
    """D43 account (exits.equity_curve) with the D44 switches (module docstring). Returns equity, exposure
    (positions / equity), bench_frac (benchmark share of equity) and counters."""
    n = px.shape[1]
    zero = {"buy": 0.0, "sell": 0.0}
    bc = (bench_costs or zero) if bench is not None else zero
    if bench is None:
        wv = np.zeros(n)
    elif idle_w is None:
        wv = np.ones(n)
    else:
        wv = np.clip(np.nan_to_num(np.asarray(idle_w, dtype=float), nan=1.0), 0.0, 1.0)
    idx = list(range(len(trades))) if order is None else list(order)
    if order is None:
        idx.sort(key=lambda k: (trades[k].d0, trades[k].key))
    else:
        rank = {k: r for r, k in enumerate(idx)}
        idx.sort(key=lambda k: (trades[k].d0, rank[k]))
    by_day: dict[int, list[int]] = {}
    for k in idx:
        if start <= trades[k].d0 < n:
            by_day.setdefault(trades[k].d0, []).append(k)
    w0 = float(wv[start]) if n else 1.0
    b_val = w0 * (1.0 - bc["buy"])   # money in the benchmark (the initial purchase pays its cost)
    cash = 1.0 - w0
    open_pos: dict[int, list] = {}
    eq = np.full(n, np.nan)
    expo = np.zeros(n)
    bfrac = np.zeros(n)
    taken = scaled = full = regime_skip = forced = 0
    taken_idx: list[tuple[int, float]] = []
    buy, sell = costs["buy"], costs["sell"]
    for d in range(start, n):
        if bench is not None and d > start and math.isfinite(bench[d]) and math.isfinite(bench[d - 1]):
            b_val *= bench[d] / bench[d - 1]
        for k in list(open_pos):
            sh, alive = open_pos[k]
            for j, (_f, dx, xpx) in enumerate(trades[k].legs):
                if alive[j] and dx == d:
                    cash += sh[j] * xpx * (1.0 - sell)
                    alive[j] = False
            if not any(alive):
                del open_pos[k]
        off = allow is not None and not bool(allow[d])
        if off and exit_on_off and open_pos:
            for k in list(open_pos):
                sh, alive = open_pos[k]
                p = px[trades[k].sym, d]
                p = p if math.isfinite(p) else trades[k].entry
                cash += sum(s for s, a in zip(sh, alive) if a) * p * (1.0 - sell)
                del open_pos[k]
                forced += 1
        # rebalance the idle money between the benchmark and cash
        tot = b_val + cash
        if tot > 0:
            tgt = float(wv[d])
            cur = b_val / tot
            diff = tgt - cur
            if abs(diff) > band or (tgt in (0.0, 1.0) and abs(diff) > 1e-15):
                if diff > 0:
                    amt = min(cash, tgt * tot - b_val)
                    cash -= amt
                    b_val += amt * (1.0 - bc["buy"])
                elif diff < 0:
                    amt = b_val - tgt * tot
                    b_val -= amt
                    cash += amt * (1.0 - bc["sell"])

        def mtm() -> float:
            v = 0.0
            for kk, (sh, alive) in open_pos.items():
                p = px[trades[kk].sym, d]
                if not math.isfinite(p):
                    p = trades[kk].entry
                v += sum(s * p for s, a in zip(sh, alive) if a)
            return v
        equity = b_val + cash + mtm()
        mult = 1.0 if risk_mult is None else float(risk_mult[d]) if math.isfinite(risk_mult[d]) else 1.0
        for k in by_day.get(d, []):
            if off:
                regime_skip += 1
                continue
            if len(open_pos) >= max_pos:
                full += 1
                continue
            tr = trades[k]
            base = float(weights[k]) if weights is not None else (risk / tr.risk_pct if tr.risk_pct > 0 else 0.0)
            want = min(base, max_w) * mult * equity
            avail = cash + b_val * (1.0 - bc["sell"])
            size = min(want, avail)
            if size <= 1e-12:
                full += 1
                continue
            if size < want:
                scaled += 1
            if size <= cash:
                cash -= size
            else:
                b_val -= (size - cash) / (1.0 - bc["sell"])
                cash = 0.0
            shares = size / (tr.entry * (1.0 + buy))
            sh = [shares * f for f, _dx, _p in tr.legs]
            alive = [True] * len(tr.legs)
            for j, (_f, dx, xpx) in enumerate(tr.legs):
                if dx == d:
                    cash += sh[j] * xpx * (1.0 - sell)
                    alive[j] = False
            if any(alive):
                open_pos[k] = [sh, alive]
            taken += 1
            taken_idx.append((k, size / equity if equity > 0 else 0.0))
        if cash > 1e-15 and wv[d] == 1.0 and band == 0.0:
            # same-day exit proceeds of a leg closing on its entry day go back to the benchmark (D43 behaviour)
            b_val += cash * (1.0 - bc["buy"])
            cash = 0.0
        pos_val = mtm()
        eq[d] = b_val + cash + pos_val
        expo[d] = pos_val / eq[d] if eq[d] > 0 else 0.0
        bfrac[d] = b_val / eq[d] if eq[d] > 0 else 0.0
    return {"equity": eq, "exposure": expo, "bench_frac": bfrac, "taken": taken, "scaled": scaled,
            "skipped_full": full, "skipped_regime": regime_skip, "forced_exits": forced, "taken_idx": taken_idx}


# ------------------------------------------------------------------ performance / Sharpe statistics

def daily_returns(equity: np.ndarray, lo: int, hi: int) -> np.ndarray:
    e = np.asarray(equity[lo:hi + 1], dtype=float)
    e = e[np.isfinite(e)]
    return e[1:] / e[:-1] - 1.0 if e.size > 1 else np.zeros(0)


def moments(r: np.ndarray) -> dict[str, float]:
    """Per-period Sharpe, skewness, (non-excess) kurtosis and length of a return series."""
    r = np.asarray(r, dtype=float)
    r = r[np.isfinite(r)]
    n = r.size
    if n < 3:
        return {"sr": float("nan"), "skew": 0.0, "kurt": 3.0, "n": n}
    mu, sd = float(r.mean()), float(r.std(ddof=1))
    if sd == 0:
        return {"sr": float("nan"), "skew": 0.0, "kurt": 3.0, "n": n}
    z = (r - mu) / float(r.std(ddof=0))
    return {"sr": mu / sd, "skew": float((z ** 3).mean()), "kurt": float((z ** 4).mean()), "n": n}


def psr(sr: float, n: int, skew: float, kurt: float, sr_star: float = 0.0) -> float | None:
    """Probabilistic Sharpe Ratio: P(true per-period SR > sr_star) given the estimate and its higher moments."""
    if not (math.isfinite(sr) and n > 2):
        return None
    den = 1.0 - skew * sr + (kurt - 1.0) / 4.0 * sr * sr
    if den <= 0:
        return None
    return _N.cdf((sr - sr_star) * math.sqrt(n - 1) / math.sqrt(den))


def expected_max_sr(var_sr: float, n_trials: int) -> float:
    """E[max of n_trials Sharpe estimates] under H0 (true SR = 0) with cross-trial variance var_sr."""
    if n_trials < 2 or var_sr <= 0:
        return 0.0
    return math.sqrt(var_sr) * ((1 - EULER) * _N.inv_cdf(1 - 1.0 / n_trials)
                                + EULER * _N.inv_cdf(1 - 1.0 / (n_trials * math.e)))


def dsr(sr: float, n: int, skew: float, kurt: float, trial_srs: Sequence[float], n_trials: int | None = None) -> dict:
    """Deflated Sharpe Ratio: PSR against the expected maximum Sharpe of the trials."""
    t = np.asarray([x for x in trial_srs if math.isfinite(x)], dtype=float)
    nt = n_trials if n_trials is not None else t.size
    var = float(t.var(ddof=1)) if t.size > 1 else 0.0
    sr0 = expected_max_sr(var, nt)
    return {"sr0": sr0, "dsr": psr(sr, n, skew, kurt, sr0), "n_trials": nt, "var_sr": var}


def calmar(cagr: float | None, mdd: float | None) -> float | None:
    if cagr is None or mdd is None or mdd >= 0:
        return None
    return cagr / -mdd
