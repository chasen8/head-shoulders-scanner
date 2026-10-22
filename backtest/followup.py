"""Follow-up analyses of the D40 backtest (charter D42). Pure functions only (no network, no file IO);
backtest/followup_run.py does the IO, the per-symbol workers, the aggregation and the Excel workbook.

1. Benchmark: for every trade, the benchmark's return over the same holding period (entry bar close -> exit bar
   close, as-of lookup: the newest benchmark close at or before that time) and excess = net_ret - benchmark.
   Random-entry control: same symbol + timeframe, random entry bars, (a) the same number of bars held,
   (b) the same %-stop / %-target bracket and holding cap (engine exit rules).
2. Filters on existing trades, computed only from bars <= the entry bar t (asserted / truncation-tested):
   LS low, prior decline (peak in the lookback before LS), volume contraction around RS vs LS/H, entry and
   breakout bar volume vs the prior 20 bars, benchmark above its 200-day SMA at the entry time.
3. Neckline retest entry: after a breakout signal at t, wait bars t+1 .. t+wait for low <= level*(1+x); fill at
   min(open, limit). Stop RS, targets as the signal. Exit rules = engine (same bar stop+target -> stop).
"""
from __future__ import annotations

import math
from typing import Any, Sequence

import numpy as np

from backtest import engine as E

# ------------------------------------------------------------------ statistics


def _betacf(a: float, b: float, x: float) -> float:
    """Continued fraction for the regularized incomplete beta (modified Lentz)."""
    tiny, eps = 1e-300, 3e-14
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c, d = 1.0, 1.0 - qab * x / qap
    d = 1.0 / (d if abs(d) > tiny else tiny)
    h = d
    for m in range(1, 400):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) > tiny else tiny)
        c = 1.0 + aa / c
        c = c if abs(c) > tiny else tiny
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) > tiny else tiny)
        c = 1.0 + aa / c
        c = c if abs(c) > tiny else tiny
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < eps:
            break
    return h


def betainc(a: float, b: float, x: float) -> float:
    """Regularized incomplete beta I_x(a, b)."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    lbt = math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(x) + b * math.log1p(-x)
    bt = math.exp(lbt)
    if x < (a + 1.0) / (a + b + 2.0):
        return bt * _betacf(a, b, x) / a
    return 1.0 - bt * _betacf(b, a, 1.0 - x) / b


def t_p_two_sided(t: float, dof: float) -> float:
    """Two-sided p value of Student's t with `dof` degrees of freedom."""
    if not math.isfinite(t):
        return 0.0
    return betainc(dof / 2.0, 0.5, dof / (dof + t * t))


def one_sample_t(x: Sequence[float]) -> tuple[float | None, float | None, float | None]:
    """(mean, t, two-sided p) of H0 mean == 0. None when n < 2 or zero variance."""
    a = np.asarray(x, dtype=float)
    a = a[np.isfinite(a)]
    n = a.size
    if n < 2:
        return (float(a.mean()) if n else None), None, None
    sd = float(a.std(ddof=1))
    if sd == 0.0:
        return float(a.mean()), None, None
    t = float(a.mean()) / (sd / math.sqrt(n))
    return float(a.mean()), t, t_p_two_sided(t, n - 1)


def welch_t(a: Sequence[float], b: Sequence[float]) -> float | None:
    """Two-sided p of Welch's t test (mean a != mean b)."""
    a = np.asarray(a, dtype=float)
    b = np.asarray(b, dtype=float)
    a, b = a[np.isfinite(a)], b[np.isfinite(b)]
    if a.size < 2 or b.size < 2:
        return None
    va, vb = a.var(ddof=1) / a.size, b.var(ddof=1) / b.size
    se = math.sqrt(va + vb)
    if se == 0.0:
        return None
    t = (a.mean() - b.mean()) / se
    dof = (va + vb) ** 2 / ((va ** 2) / (a.size - 1) + (vb ** 2) / (b.size - 1))
    return t_p_two_sided(float(t), float(dof))


def profit_factor(r: Sequence[float]) -> float | None:
    a = np.asarray(r, dtype=float)
    a = a[np.isfinite(a)]
    loss = -a[a < 0].sum()
    return float(a[a > 0].sum() / loss) if loss > 0 else None


# ------------------------------------------------------------------ benchmark

BENCH_COSTS = {"tw": {"buy": 0.001425, "sell": 0.001425 + 0.001},  # ETF: securities tax 0.1%
               "crypto": {"buy": 0.001, "sell": 0.001}}


def asof_index(times_ns: np.ndarray, query_ns: np.ndarray) -> np.ndarray:
    """Index of the newest benchmark bar with close time <= query (-1 when none)."""
    return np.searchsorted(times_ns, np.asarray(query_ns, dtype=np.int64), side="right") - 1


def asof_values(times_ns: np.ndarray, values: np.ndarray, query_ns: np.ndarray) -> np.ndarray:
    j = asof_index(times_ns, query_ns)
    out = np.full(j.shape, np.nan)
    ok = j >= 0
    out[ok] = values[j[ok]]
    return out


def bench_return(times_ns: np.ndarray, values: np.ndarray, t0_ns: np.ndarray, t1_ns: np.ndarray,
                 market: str | None = None) -> np.ndarray:
    """Benchmark return from the as-of value at t0 to the as-of value at t1 (after costs when `market`)."""
    v0 = asof_values(times_ns, values, t0_ns)
    v1 = asof_values(times_ns, values, t1_ns)
    if market is None:
        return v1 / v0 - 1.0
    c = BENCH_COSTS[market]
    return v1 * (1.0 - c["sell"]) / (v0 * (1.0 + c["buy"])) - 1.0


def sma_ratio(values: np.ndarray, n: int = 200) -> np.ndarray:
    """close / SMA(n) using bars <= i only (NaN for the first n-1 bars)."""
    v = np.asarray(values, dtype=float)
    out = np.full(v.shape, np.nan)
    if v.size >= n:
        cs = np.concatenate([[0.0], np.cumsum(v)])
        sma = (cs[n:] - cs[:-n]) / n
        out[n - 1:] = v[n - 1:] / sma
    return out


def above_sma_asof(times_ns: np.ndarray, values: np.ndarray, query_ns: np.ndarray, n: int = 200) -> np.ndarray:
    """1.0 when the newest benchmark close at or before the query time is above its SMA(n), 0.0 below, NaN unknown."""
    r = asof_values(times_ns, sma_ratio(values, n), query_ns)
    out = np.where(np.isfinite(r), (r > 1.0).astype(float), np.nan)
    return out


# ------------------------------------------------------------------ random-entry control

def fixed_hold_returns(close: np.ndarray, starts: np.ndarray, h: int, market: str) -> np.ndarray:
    """Net return of buying close[s] and selling close[s + h] (engine costs)."""
    c = E.COSTS[market]
    s = np.asarray(starts, dtype=int)
    return close[s + h] * (1.0 - c["sell"]) / (close[s] * (1.0 + c["buy"])) - 1.0


def bracket_sim(b: E.Bars, t: int, stop: float, tp: float, max_hold: int) -> tuple[int, float, str]:
    """Vectorised engine.simulate_single for a long (identical result, tested)."""
    end = min(t + max_hold, b.n - 1)
    lo = b.low[t + 1:end + 1]
    hi = b.high[t + 1:end + 1]
    s = np.flatnonzero(lo <= stop)
    p = np.flatnonzero(hi >= tp)
    si = int(s[0]) if s.size else None
    pi = int(p[0]) if p.size else None
    if si is not None and (pi is None or si <= pi):
        i = t + 1 + si
        return i, float(min(b.open[i], stop)), "stop"
    if pi is not None:
        return t + 1 + pi, float(tp), "tp"
    if t + max_hold <= b.n - 1:
        return t + max_hold, float(b.close[t + max_hold]), "timeout"
    return b.n - 1, float(b.close[b.n - 1]), "open"


def random_bracket(b: E.Bars, starts: Sequence[int], risk_pct: float, tp_pct: float, max_hold: int,
                   market: str) -> tuple[list[float], list[bool]]:
    """Random entries at close[s] with stop close*(1-risk_pct), target close*(1+tp_pct). 'open' results dropped."""
    rets, wins = [], []
    for s in starts:
        entry = float(b.close[s])
        _i, px, reason = bracket_sim(b, int(s), entry * (1.0 - risk_pct), entry * (1.0 + tp_pct), max_hold)
        if reason == "open":
            continue
        rets.append(E.net_return(entry, px, market))
        wins.append(reason == "tp")
    return rets, wins


def draw_starts(rng: np.random.Generator, lo: int, hi: int, k: int) -> np.ndarray:
    """k uniform integer bars in [lo, hi] (empty when hi < lo)."""
    if hi < lo:
        return np.empty(0, dtype=int)
    return rng.integers(lo, hi + 1, size=k)


# ------------------------------------------------------------------ filter features (bars <= t only)

def month_bars(timeframe: str) -> float:
    return {"1d": 21.0, "1w": 30.4375 / 7.0, "4h": 21.0 * 2}[timeframe]  # TW 4h: ~2 bars / trading day


def prior_decline(high: np.ndarray, low: np.ndarray, ls_i: int, lookback: int) -> dict[str, Any]:
    """Highest high in bars [ls_i - lookback, ls_i) -> LS low: bars from the peak and the fall (LS/peak - 1).
    Uses bars < LS only (LS is before the entry bar, so nothing after the entry is read)."""
    lo = max(0, ls_i - lookback)
    if ls_i - lo < 1:
        return {"decline_bars": None, "decline_depth": None, "decline_at_edge": None, "decline_window": 0}
    seg = high[lo:ls_i]
    k = int(np.argmax(seg))
    peak = float(seg[k])
    return {"decline_bars": ls_i - (lo + k), "decline_depth": float(low[ls_i]) / peak - 1.0 if peak > 0 else None,
            "decline_at_edge": k == 0 and lo > 0, "decline_window": ls_i - lo}


def mean_volume(vol: np.ndarray, centers: Sequence[int], n: int, t: int) -> float | None:
    """Mean volume of bars [c - n, c + n] for each center, clipped to [0, t] (never past the entry bar)."""
    idx = set()
    for c in centers:
        idx.update(range(max(0, c - n), min(t, c + n) + 1))
    if not idx:
        return None
    v = vol[sorted(idx)]
    return float(v.mean())


def volume_contraction(vol: np.ndarray, ls_i: int, h_i: int, rs_i: int, t: int, n: int) -> float | None:
    """Mean volume around RS / mean volume around LS and H (all windows clipped to bars <= t)."""
    rs = mean_volume(vol, [rs_i], n, t)
    base = mean_volume(vol, [ls_i, h_i], n, t)
    if rs is None or not base:
        return None
    return rs / base


def volume_surge(vol: np.ndarray, i: int, n: int = 20) -> float | None:
    """Volume of bar i / mean volume of bars [i - n, i - 1]."""
    if i - n < 0:
        return None
    prior = float(vol[i - n:i].mean())
    return float(vol[i]) / prior if prior > 0 else None


def bin_label(v: float | None, edges: Sequence[float], labels: Sequence[str]) -> str:
    """labels[k] for edges[k] <= v < edges[k+1] (len(labels) == len(edges) - 1); '未知' for None/NaN."""
    if v is None or (isinstance(v, float) and not math.isfinite(v)):
        return "未知"
    for k in range(len(labels)):
        if edges[k] <= v < edges[k + 1]:
            return labels[k]
    return "未知"


# ------------------------------------------------------------------ neckline retest entry

def retest_fill(b: E.Bars, t: int, level: float, rs: float, x: float, wait: int,
                mode: str = "limit") -> tuple[int | None, float | None, str]:
    """Wait bars t+1 .. t+wait (the order is placed after the signal bar t closed).

    limit:         first bar with low <= limit (= level*(1+x)) fills at min(open, limit).
                   (A bar closing below RS always trades through the limit first, since limit > RS, so the
                   RS-close cancel can never come before a limit fill; a failed retest shows up as a stop.)
    close_confirm: first bar with low <= limit AND close >= level enters at that bar's close;
                   a bar closing below RS (not confirmed) cancels.
    Returns (fill bar, fill price, reason) with reason filled / cancel_rs / no_retest / pending (data ended
    before the wait was over) / bad_level (limit <= RS)."""
    limit = level * (1.0 + x)
    if not (limit > rs > 0):
        return None, None, "bad_level"
    last = t + wait
    for i in range(t + 1, min(last, b.n - 1) + 1):
        if mode == "limit":
            if b.low[i] <= limit:
                return i, float(min(b.open[i], limit)), "filled"
        else:
            if b.low[i] <= limit and b.close[i] >= level:
                return i, float(b.close[i]), "filled"
            if b.close[i] < rs:
                return None, None, "cancel_rs"
    if last > b.n - 1:
        return None, None, "pending"
    return None, None, "no_retest"


def simulate_from_fill(b: E.Bars, f: int, fill_px: float, stop: float, tp: float, max_hold: int,
                       fill_at_open: bool, intrabar_fill: bool = True) -> tuple[int, float, str]:
    """Exit of a long filled on bar f. On the fill bar itself (intrabar_fill: a limit fill):
      low <= stop -> stop at min(fill, stop) (we may have been filled first, then stopped);
      high >= tp  -> counted only when filled at the open (otherwise the high may have come before the dip).
    A close_confirm entry (intrabar_fill=False) fills at the close: the fill bar has no exit.
    Bars f+1 .. f+max_hold: engine rules (same bar stop + target -> stop)."""
    if intrabar_fill:
        if b.low[f] <= stop:
            return f, float(min(fill_px, stop)), "stop"
        if fill_at_open and b.high[f] >= tp:
            return f, float(tp), "tp"
    return E.simulate_single(b, f, stop, tp, max_hold, 1)


def retest_trade(b: E.Bars, t: int, level: float, rs: float, tp: float, x: float, wait: int, max_hold: int,
                 market: str, mode: str = "limit") -> dict[str, Any]:
    """Retest entry + exit for one signal. Keys: status, fill_bar, fill_price, exit_bar, exit_price,
    exit_reason, net_ret, R, risk_pct."""
    f, px, reason = retest_fill(b, t, level, rs, x, wait, mode)
    out: dict[str, Any] = {"status": reason, "fill_bar": f, "fill_price": px}
    if f is None:
        return out
    if tp <= px:
        out["status"] = "target_below_fill"
        return out
    at_open = mode == "limit" and float(b.open[f]) <= level * (1.0 + x)
    ei, epx, er = simulate_from_fill(b, f, px, rs, tp, max_hold, at_open, intrabar_fill=(mode == "limit"))
    ret = E.net_return(px, epx, market)
    risk = (px - rs) / px
    out.update({"exit_bar": ei, "exit_price": epx, "exit_reason": er, "net_ret": ret, "risk_pct": risk,
                "R": ret / risk if risk > 0 else None, "is_open": er == "open", "tp_hit": er == "tp"})
    return out


def breakout_trade(b: E.Bars, t: int, rs: float, tp: float, max_hold: int, market: str) -> dict[str, Any]:
    """The original A entry (close[t]) for one signal, with no one-position-per-series constraint."""
    entry = float(b.close[t])
    if not (entry > rs > 0) or tp <= entry:
        return {"status": "skip"}
    ei, epx, er = E.simulate_single(b, t, rs, tp, max_hold, 1)
    ret = E.net_return(entry, epx, market)
    risk = (entry - rs) / entry
    return {"status": "entered", "entry_price": entry, "exit_bar": ei, "exit_reason": er, "net_ret": ret,
            "risk_pct": risk, "R": ret / risk if risk > 0 else None, "is_open": er == "open", "tp_hit": er == "tp"}
