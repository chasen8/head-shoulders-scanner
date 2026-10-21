"""Converging triangles: ascending / descending / symmetrical (charter D38).

Design and starting thresholds: docs/triangle-research.md sections 5-7; every number is in config.TRIANGLE_CONFIG
(rules and values: docs/current-rules.md "收斂三角"). Independent of the IHS detector (only swings.pivot_mask and
detector.wilder_atr are reused as plain functions). Pure OHLCV, closed bars only.

Notation (x = bar index, prices in natural log):
  pivots      strict swing highs / lows, N bars each side, known N bars later (D4: confirm = index + N)
  s           the pattern's first pivot (a high or a low); every pivot from s up to e (the newest one in use)
              belongs to the pattern
  R(x), S(x)  resistance / support: least squares through the pattern's pivot highs / lows, R(x) = aR + sR*(x - s)
  H0          R(s) - S(s), the log width at the start (the widest point)
  apex        x_a = s + H0 / (sS - sR) (needs sS > sR: the lines converge to the right)
  p(x)        (x - s) / (x_a - s) apex progress; rho(x) = gap(x) / H0 = 1 - p(x)

No look-ahead: the pivots e_1, e_2, ... after s are taken in order. With pivots s..e the lines are known from
confirm(e) = e + N; until the next pivot is confirmed they are fixed, and those bars are checked for a breakout
(first close beyond a line plus buffer). Bars before confirm(e) must be enclosed by the lines. The first breakout
ends the pattern (later pivots are never added), so a breakout found at bar b is the same at every later bar; the
result as of bar t only reads df[:t+1] (tests/test_triangles.py truncation tests).
"""
from __future__ import annotations

import math
from typing import Any, Mapping

import numpy as np
import pandas as pd

from .config import TIMEFRAMES, tf_value, triangle_config
from .detector import wilder_atr
from .swings import get_timestamps, pivot_mask

PATTERN_TYPE = "triangle"
TYPES = ("ascending", "descending", "symmetrical")
BIAS = {"ascending": "up", "descending": "down", "symmetrical": "neutral"}
STATUSES = ("forming", "breakout_up", "breakout_down", "stale")
REQUIRED_COLUMNS = ("open", "high", "low", "close", "volume")
# score components (name, max points); formulas in score_components()
SCORE_PARTS = (("touch_score", 20), ("fit_score", 15), ("convergence_score", 20), ("fill_score", 15),
               ("breakout_score", 20), ("clarity_score", 10))


def _iso(ts: pd.Timestamp) -> str:
    t = pd.Timestamp(ts)
    t = t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def _r(x: float | None, nd: int = 6) -> float | None:
    if x is None:
        return None
    f = float(x)
    return round(f, nd) if math.isfinite(f) else None


def classify(drift_r: float, drift_s: float, slope_r: float, slope_s: float,
             cfg: Mapping[str, Any]) -> str | None:
    """Triangle type from the two lines' drifts (in units of H0) and per-bar slopes; None = not a triangle."""
    hz, sl = float(cfg["horizontal_max_drift"]), float(cfg["sloped_min_drift"])
    if abs(drift_r) <= hz and drift_s >= sl:
        return "ascending"
    if drift_r <= -sl and abs(drift_s) <= hz:
        return "descending"
    sym = float(cfg["sym_min_drift"])
    if drift_r <= -sym and drift_s >= sym:
        lo, hi = sorted((abs(slope_r), abs(slope_s)))
        if lo > 0 and hi / lo <= float(cfg["sym_max_slope_ratio"]):
            return "symmetrical"
    return None


def _lstsq(x: list[float], y: list[float]) -> tuple[float, float]:
    """(intercept at x = 0, slope). Plain Python: the point sets are tiny (<= ~20), numpy's per-call cost dominates."""
    n = len(x)
    if n == 2:
        b = (y[1] - y[0]) / (x[1] - x[0])
        return y[0] - b * x[0], b
    xm, ym = sum(x) / n, sum(y) / n
    sxy = sxx = 0.0
    for xi, yi in zip(x, y):
        dx = xi - xm
        sxy += dx * (yi - ym)
        sxx += dx * dx
    b = sxy / sxx
    return ym - b * xm, b


def fit_line(x, y, eps: float, wick: float, side: str, max_removals: int,
             keep_first: bool) -> tuple[float, float, np.ndarray, np.ndarray] | None:
    """Least squares line through (x, y); the point with the largest |residual| > eps is dropped and the line
    refitted, at most max_removals times. Then the line is moved so no remaining point sticks out by more than
    `wick` (above for side "high", below for "low") and every |residual| must still be <= eps.
    Returns (intercept, slope, kept positions, residuals of the kept points) or None.
    keep_first: the first point (the pattern's start pivot) must stay."""
    xs, ys = [float(v) for v in x], [float(v) for v in y]
    keep = list(range(len(xs)))
    for attempt in range(max_removals + 1):
        if len(keep) < 2:
            return None
        kx, ky = [xs[i] for i in keep], [ys[i] for i in keep]
        a, b = _lstsq(kx, ky)
        res = [yi - (a + b * xi) for xi, yi in zip(kx, ky)]
        worst = max(range(len(res)), key=lambda i: abs(res[i]))
        if abs(res[worst]) <= eps:
            break
        if attempt == max_removals or (keep_first and keep[worst] == 0):
            return None
        del keep[worst]
    if side == "high":
        shift = max(0.0, max(res) - wick)
    else:
        shift = min(0.0, min(res) + wick)
    if shift:
        a += shift
        res = [r - shift for r in res]
        if max(abs(r) for r in res) > eps:
            return None
    return a, b, np.array(keep, dtype=int), np.array(res)


def _validate(df: pd.DataFrame, timeframe: str) -> None:
    if timeframe not in TIMEFRAMES:
        raise ValueError(f"unsupported timeframe {timeframe!r}; expected one of {TIMEFRAMES}")
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"df is missing columns: {missing}")


def _clip01(x: float) -> float:
    x = float(x)
    return 0.0 if not math.isfinite(x) else max(0.0, min(1.0, x))


def score_components(*, touches: int, fit_err: float, rho: float, crossings: int, broke: bool,
                     dist_frac: float, clarity: float, ideal_rho: float) -> dict[str, float]:
    """0-100 total, see docs/current-rules.md 收斂三角 §8:
      touch_score       20 * clip((touches - 4) / 4)            5 touches -> 5, 8+ -> 20
      fit_score         15 * clip(1 - fit_err)                  fit_err = mean |residual| / eps over both lines
      convergence_score 20 * clip(1 - |rho - ideal| / 0.35)     rho = gap / H0 at the breakout bar (or now)
      fill_score        15 * clip(crossings / (touches - 1))    closes crossing the mid line (R+S)/2
      breakout_score    20 if broken out, else 20 * clip(1 - dist_frac)
                        dist_frac = distance to the line in the bias direction / gap (neutral: 2 * nearer line)
      clarity_score     10 * clarity                            margins past the classification thresholds
    """
    parts = {
        "touch_score": 20.0 * _clip01((touches - 4) / 4.0),
        "fit_score": 15.0 * _clip01(1.0 - fit_err),
        "convergence_score": 20.0 * _clip01(1.0 - abs(rho - ideal_rho) / 0.35),
        "fill_score": 15.0 * _clip01(crossings / max(1.0, touches - 1.0)),
        "breakout_score": 20.0 if broke else 20.0 * _clip01(1.0 - dist_frac),
        "clarity_score": 10.0 * _clip01(clarity),
    }
    return {k: round(float(v), 2) for k, v in parts.items()}


def _clarity(ttype: str, drift_r: float, drift_s: float, slope_r: float, slope_s: float,
             cfg: Mapping[str, Any]) -> float:
    hz, sl, sym = float(cfg["horizontal_max_drift"]), float(cfg["sloped_min_drift"]), float(cfg["sym_min_drift"])
    if ttype == "ascending":
        return (_clip01(1 - abs(drift_r) / hz) + _clip01((drift_s - sl) / sl)) / 2
    if ttype == "descending":
        return (_clip01(1 - abs(drift_s) / hz) + _clip01((-drift_r - sl) / sl)) / 2
    lo, hi = sorted((abs(slope_r), abs(slope_s)))
    ratio = hi / lo if lo > 0 else float("inf")
    rmax = float(cfg["sym_max_slope_ratio"])
    return (_clip01((-drift_r - sym) / sym) + _clip01((drift_s - sym) / sym)
            + _clip01(1 - (ratio - 1) / max(rmax - 1, 1e-9))) / 3


class _Series:
    """Arrays of one series, shared by every candidate."""

    def __init__(self, df: pd.DataFrame, timeframe: str, cfg: Mapping[str, Any]):
        self.ts = get_timestamps(df)
        self.high = df["high"].to_numpy(dtype=float)
        self.low = df["low"].to_numpy(dtype=float)
        self.close = df["close"].to_numpy(dtype=float)
        with np.errstate(divide="ignore", invalid="ignore"):
            self.lh = np.log(self.high)
            self.ll = np.log(self.low)
            self.lc = np.log(self.close)
        self.n = len(df)
        self.last = self.n - 1
        self.N = int(tf_value(cfg, "pivot_window_by_timeframe", timeframe))
        self.atr = wilder_atr(df, int(cfg["atr_length"]))
        hmask = pivot_mask(self.high, self.N, "high")
        lmask = pivot_mask(self.low, self.N, "low")
        # merged pivot sequence in index order; at one index a high comes before a low (outside bars)
        piv = [(int(i), 0) for i in np.flatnonzero(hmask)] + [(int(i), 1) for i in np.flatnonzero(lmask)]
        piv.sort()
        self.px = np.array([p[0] for p in piv], dtype=int)
        self.pk = np.array([p[1] for p in piv], dtype=int)  # 0 = high, 1 = low

    def time_at(self, x: float, s: int) -> pd.Timestamp:
        """Bar time at (possibly fractional / future) index x: bars inside the data are exact; beyond the last bar
        the pattern's mean bar duration ((t_last - t_s) / (last - s)) is used (TW 4h bars are uneven)."""
        xi = int(round(x))
        if xi <= self.last:
            return self.ts[max(0, xi)]
        span = max(1, self.last - s)
        step = (self.ts[self.last] - self.ts[s]) / span
        return self.ts[self.last] + step * (xi - self.last)


def _eval_candidate(S: _Series, k: int, j: int, cfg: Mapping[str, Any], timeframe: str) -> dict[str, Any] | None:
    """Lines through pivots k..j (positions in the merged pivot list) if they form a valid triangle, else None."""
    px, pk = S.px[k:j + 1], S.pk[k:j + 1]
    s = int(px[0])
    hx, lx = px[pk == 0], px[pk == 1]
    nmin = int(cfg["min_touches_per_line"])
    if hx.shape[0] < nmin or lx.shape[0] < nmin or hx.shape[0] + lx.shape[0] < int(cfg["min_touches_total"]):
        return None
    ws = int(px[-1]) + S.N  # the newest pivot is known from here
    c_ref = S.close[ws]
    atr_ref = S.atr[ws]
    atr_pct = float(atr_ref / c_ref) if math.isfinite(atr_ref) and c_ref > 0 else 0.0
    eps = math.log1p(max(float(cfg["touch_atr_mult"]) * atr_pct, float(cfg["touch_min_pct"])))
    wick = math.log1p(float(cfg["enclosure_wick_tol"]))
    rem = int(cfg["max_fit_removals"])
    fr = fit_line((hx - s).astype(float), S.lh[hx], eps, wick, "high", rem, keep_first=pk[0] == 0)
    if fr is None:
        return None
    fs = fit_line((lx - s).astype(float), S.ll[lx], eps, wick, "low", rem, keep_first=pk[0] == 1)
    if fs is None:
        return None
    aR, sR, keepR, resR = fr
    aS, sS, keepS, resS = fs
    tR, tS = hx[keepR], lx[keepS]
    if tR.shape[0] < nmin or tS.shape[0] < nmin or tR.shape[0] + tS.shape[0] < int(cfg["min_touches_total"]):
        return None
    H0 = aR - aS
    if not (H0 > 0) or sS <= sR:
        return None  # crossed at the start, parallel or diverging
    if H0 < float(cfg["min_height_atr"]) * atr_pct or math.expm1(H0) > float(cfg["max_height_pct"]):
        return None
    span = int(max(tR[-1], tS[-1])) - s
    if abs(int(tR[0]) - int(tS[0])) > float(cfg["max_start_offset_of_width"]) * max(span, 1):
        return None
    drift_r = sR * float(tR[-1] - tR[0]) / H0
    drift_s = sS * float(tS[-1] - tS[0]) / H0
    ttype = classify(drift_r, drift_s, sR, sS, cfg)
    if ttype is None:
        return None
    # enclosure of everything before the newest pivot's confirmation
    xs = np.arange(s, ws)
    rx, sx = aR + sR * (xs - s), aS + sS * (xs - s)
    ctol, wtol = math.log1p(float(cfg["enclosure_close_tol"])), wick
    if (np.any(S.lc[xs] > rx + ctol) or np.any(S.lc[xs] < sx - ctol)
            or np.any(S.lh[xs] > rx + wtol) or np.any(S.ll[xs] < sx - wtol)):
        return None
    apex = s + H0 / (sS - sR)
    return {"s": s, "ws": ws, "aR": aR, "sR": sR, "aS": aS, "sS": sS, "H0": H0, "apex": apex, "eps": eps,
            "tR": tR, "tS": tS, "resR": resR, "resS": resS, "type": ttype, "drift_r": drift_r,
            "drift_s": drift_s, "all_h": hx, "all_l": lx}


def _scan_window(S: _Series, c: Mapping[str, Any], lo: int, hi: int, cfg: Mapping[str, Any], timeframe: str):
    """First event in bars lo..hi with the lines of candidate c: ("apex", x) when p reaches 1, ("both", x) when one
    bar crosses both lines, ("up"|"down", x) for a breakout; None when nothing happens."""
    if hi < lo:
        return None
    xs = np.arange(lo, hi + 1)
    s = c["s"]
    rx, sx = c["aR"] + c["sR"] * (xs - s), c["aS"] + c["sS"] * (xs - s)
    buf = float(tf_value(cfg, "breakout_buffer_by_timeframe", timeframe))
    up_l, dn_l = math.log1p(buf), math.log1p(-buf)
    if cfg["breakout_price"] == "high":
        up = S.lh[xs] > rx + up_l
        dn = S.ll[xs] < sx + dn_l
    else:
        up = S.lc[xs] > rx + up_l
        dn = S.lc[xs] < sx + dn_l
    past = xs >= c["apex"]
    ev = up | dn | past
    if not ev.any():
        return None
    i = int(np.argmax(ev))
    x = int(xs[i])
    if past[i]:
        return ("apex", x)
    if up[i] and dn[i]:
        return ("both", x)
    return ("up" if up[i] else "down", x)


def _build(S: _Series, c: Mapping[str, Any], cfg: Mapping[str, Any], timeframe: str, symbol: str,
           end: int, broke: str | None, stale_reason: str | None) -> dict[str, Any]:
    """The output row for candidate c whose formation ends at bar `end` (breakout bar, apex bar or the last bar)."""
    s, last = c["s"], S.last
    aR, sR, aS, sS, H0 = c["aR"], c["sR"], c["aS"], c["sS"], c["H0"]

    def R(x):
        return aR + sR * (x - s)

    def Sl(x):
        return aS + sS * (x - s)

    ttype = c["type"]
    apex = c["apex"]
    p_end = (end - s) / (apex - s)
    rho_end = 1.0 - p_end
    # status / age / failure
    bars_since = None
    status = "forming"
    if broke:
        status = "breakout_up" if broke == "up" else "breakout_down"
        bars_since = last - end
        if bars_since > int(tf_value(cfg, "max_breakout_age", timeframe)):
            status, stale_reason = "stale", stale_reason or "breakout_age"
        xs = np.arange(end + 1, last + 1)
        if xs.shape[0]:
            fb = float(cfg["max_fallback_of_height"]) * H0
            if broke == "up":
                failed = np.any(S.lc[xs] < R(xs) - fb)
            else:
                failed = np.any(S.lc[xs] > Sl(xs) + fb)
            if failed:
                status, stale_reason = "stale", "failed_breakout"
    if stale_reason == "apex":
        status = "stale"
    # prices
    res_price = lambda x: float(math.exp(R(x)))  # noqa: E731
    sup_price = lambda x: float(math.exp(Sl(x)))  # noqa: E731
    pat_hi = float(S.high[c["all_h"]].max())
    pat_lo = float(S.low[c["all_l"]].min())
    if ttype == "ascending":
        height = res_price(s) - pat_lo
    elif ttype == "descending":
        height = pat_hi - sup_price(s)
    else:
        height = pat_hi - pat_lo
    direction = broke or ("down" if BIAS[ttype] == "down" else "up")
    if broke:
        brk_price = res_price(end) if broke == "up" else sup_price(end)
    else:
        brk_price = res_price(last) if direction == "up" else sup_price(last)
    rate = float(cfg["target_hit_rate"][ttype][direction])
    sign = 1.0 if direction == "up" else -1.0
    target = brk_price + sign * height
    target_cons = brk_price + sign * height * rate
    # invalidation (report 5.8): up -> the newest pivot low of the pattern; down -> ascending: the resistance
    # price at the breakout (or now), others: the newest pivot high
    if direction == "up":
        inval = float(S.low[int(c["all_l"][-1])])
    elif ttype == "ascending":
        inval = res_price(end if broke else last)
    else:
        inval = float(S.high[int(c["all_h"][-1])])
    # current position
    lc_now = S.lc[last]
    gap_now = R(last) - Sl(last)
    close_now = float(S.close[last])
    dist_r = res_price(last) / close_now - 1.0
    dist_s = close_now / sup_price(last) - 1.0
    if gap_now > 0:
        fr_r = (R(last) - lc_now) / gap_now
        fr_s = (lc_now - Sl(last)) / gap_now
    else:
        fr_r = fr_s = 0.0
    if BIAS[ttype] == "up":
        dist_frac = fr_r
    elif BIAS[ttype] == "down":
        dist_frac = fr_s
    else:
        dist_frac = 2.0 * min(fr_r, fr_s)
    # fill: closes crossing the mid line between the first pivot and the end
    xs = np.arange(s, end + 1)
    side = np.sign(S.lc[xs] - (R(xs) + Sl(xs)) / 2)
    side = side[side != 0]
    crossings = int(np.count_nonzero(side[1:] != side[:-1])) if side.shape[0] > 1 else 0
    tR, tS = c["tR"], c["tS"]
    n_touch = int(tR.shape[0] + tS.shape[0])
    err_r = float(np.abs(c["resR"]).mean() / c["eps"])
    err_s = float(np.abs(c["resS"]).mean() / c["eps"])
    clarity = _clarity(ttype, c["drift_r"], c["drift_s"], sR, sS, cfg)
    parts = score_components(touches=n_touch, fit_err=(err_r + err_s) / 2, rho=rho_end, crossings=crossings,
                             broke=bool(broke), dist_frac=dist_frac, clarity=clarity,
                             ideal_rho=float(cfg["ideal_converge_ratio"]))
    score = round(sum(parts.values()), 2)
    touches = sorted([{"time": _iso(S.ts[int(x)]), "price": float(S.high[int(x)]), "side": "resistance"} for x in tR]
                     + [{"time": _iso(S.ts[int(x)]), "price": float(S.low[int(x)]), "side": "support"} for x in tS],
                     key=lambda t: t["time"])
    start_time = _iso(S.ts[s])
    apex_time = _iso(S.time_at(apex, s))
    row = {
        "pattern_type": PATTERN_TYPE,
        "triangle_type": ttype,
        "bias": BIAS[ttype],
        "status": status,
        "stale_reason": stale_reason if status == "stale" else None,
        "breakout_direction": broke,
        "symbol": symbol,
        "timeframe": timeframe,
        "score": score,
        **parts,
        "start_time": start_time,
        "end_time": _iso(S.ts[end]),
        "pattern_bars": int(end - s),
        "resistance_start_time": start_time, "resistance_start_price": _r(res_price(s), 10),
        "resistance_end_time": _iso(S.ts[end]), "resistance_end_price": _r(res_price(end), 10),
        "support_start_time": start_time, "support_start_price": _r(sup_price(s), 10),
        "support_end_time": _iso(S.ts[end]), "support_end_price": _r(sup_price(end), 10),
        "resistance_slope_pct_per_bar": _r(math.expm1(sR)),
        "support_slope_pct_per_bar": _r(math.expm1(sS)),
        "resistance_drift": _r(c["drift_r"]), "support_drift": _r(c["drift_s"]),
        "touches": touches,
        "touches_resistance": int(tR.shape[0]),
        "touches_support": int(tS.shape[0]),
        "touches_total": n_touch,
        "fit_error_resistance": _r(err_r), "fit_error_support": _r(err_s),
        "touch_tolerance_pct": _r(math.expm1(c["eps"])),
        "midline_crossings": crossings,
        "apex_time": apex_time,
        "apex_price": _r(res_price(apex), 10),
        "apex_bars_ahead": _r(apex - last, 2),
        "apex_progress": _r(p_end, 4),
        "apex_progress_now": _r((last - s) / (apex - s), 4),
        "converge_ratio": _r(rho_end, 4),
        "near_apex": bool(p_end >= float(cfg["near_apex_progress"])),
        "height_start": _r(res_price(s) - sup_price(s), 10),
        "height_start_pct": _r(math.expm1(H0), 6),
        "target_height": _r(height, 10),
        "current_close": close_now,
        "current_resistance": _r(res_price(last), 10),
        "current_support": _r(sup_price(last), 10),
        "distance_to_resistance": _r(dist_r),
        "distance_to_support": _r(dist_s),
        "breakout_time": _iso(S.ts[end]) if broke else None,
        "breakout_price": _r(brk_price, 10) if broke else None,
        "breakout_close": float(S.close[end]) if broke else None,
        "breakout_basis": str(cfg["breakout_price"]),
        "bars_since_breakout": bars_since,
        "target_direction": direction,
        "target_basis_price": _r(brk_price, 10),
        "target_estimated": not broke,
        "target": _r(target, 10) if target > 0 else None,
        "target_conservative": _r(target_cons, 10) if target_cons > 0 else None,
        "target_hit_rate": rate,
        "invalidation_price": _r(inval, 10),
        "signal_time": _iso(S.ts[end]) if broke else _iso(S.ts[c["ws"]]),
        "as_of_time": _iso(S.ts[last]),
        "pattern_id": f"{symbol}|{timeframe}|tri|{start_time}",
    }
    return row


def find_triangle_candidates(df: pd.DataFrame, timeframe: str, config: Mapping[str, Any] | None = None,
                             symbol: str = "") -> list[dict[str, Any]]:
    """One result per start pivot (its state as of the last bar), before the stale filter and de-duplication."""
    cfg = triangle_config(config)
    _validate(df, timeframe)
    S = _Series(df, timeframe, cfg)
    if S.n < 2 * S.N + 2 or S.px.shape[0] < int(cfg["min_touches_total"]):
        return []
    if not (np.all(np.isfinite(S.lh)) and np.all(np.isfinite(S.ll)) and np.all(np.isfinite(S.lc))):
        raise ValueError("prices must be positive and finite")
    last, P = S.last, S.px.shape[0]
    min_bars = int(tf_value(cfg, "min_pattern_bars_by_timeframe", timeframe))
    max_bars = int(tf_value(cfg, "max_pattern_bars_by_timeframe", timeframe))
    age = int(tf_value(cfg, "max_breakout_age", timeframe))
    min_p = float(cfg["min_apex_progress"])
    earliest = last - max_bars - age - S.N
    out: list[dict[str, Any]] = []
    for k in range(P):
        s = int(S.px[k])
        if s < earliest:
            continue
        found = None
        for j in range(k, P):
            e = int(S.px[j])
            if e - s > max_bars:
                break
            ws = e + S.N
            we = int(S.px[j + 1]) + S.N - 1 if j + 1 < P else last
            if we < ws:
                continue  # a second pivot at the same bar: its window comes next
            c = _eval_candidate(S, k, j, cfg, timeframe)
            if c is None:
                found = None
                continue
            ev = _scan_window(S, c, ws, we, cfg, timeframe)
            if ev is None:
                found = None
                if we == last:
                    p_now = (last - s) / (c["apex"] - s)
                    if p_now >= min_p and min_bars <= last - s <= max_bars:
                        found = _build(S, c, cfg, timeframe, symbol, last, None, None)
                continue
            kind, x = ev
            if kind == "apex":
                found = _build(S, c, cfg, timeframe, symbol, x, None, "apex") if x - s >= min_bars else None
            elif kind == "both":
                found = None
            else:
                p_b = (x - s) / (c["apex"] - s)
                ok = p_b >= min_p and min_bars <= x - s <= max_bars
                found = _build(S, c, cfg, timeframe, symbol, x, kind, None) if ok else None
            break
        if found is not None:
            out.append(found)
    return out


def _overlap(a: Mapping[str, Any], b: Mapping[str, Any]) -> bool:
    return a["start_time"] <= b["end_time"] and b["start_time"] <= a["end_time"]


def dedup_key(r: Mapping[str, Any]) -> tuple:
    """Sort key: more touches, then smaller mean fit error, then newer (later start), then score."""
    err = ((r["fit_error_resistance"] or 0.0) + (r["fit_error_support"] or 0.0)) / 2
    return (-int(r["touches_total"]), round(err, 6), _neg_time(r["start_time"]), -float(r["score"]))


def _neg_time(iso: str) -> float:
    return -pd.Timestamp(iso).value / 1e9


def deduplicate_triangles(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Within one symbol + timeframe (+ exchange / market when tagged), overlapping [start_time, end_time] keep one
    triangle, chosen by dedup_key (charter D38)."""
    kept: list[dict[str, Any]] = []
    for r in sorted(rows, key=dedup_key):
        if any(q["symbol"] == r["symbol"] and q["timeframe"] == r["timeframe"]
               and q.get("exchange") == r.get("exchange") and q.get("market") == r.get("market")
               and _overlap(q, r) for q in kept):
            continue
        kept.append(r)
    return kept


def sort_triangles(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(rows, key=lambda r: (-float(r["score"]), r["symbol"], TIMEFRAMES.index(r["timeframe"]),
                                       r["start_time"]))


def detect_triangles(df: pd.DataFrame, timeframe: str, config: Mapping[str, Any] | None = None,
                     symbol: str = "") -> list[dict[str, Any]]:
    """Triangles of one series as of its last bar: candidates -> stale filter (unless include_stale) -> min_score
    -> de-duplication -> sorted by score. Rows are JSON-ready (times are ISO 8601 UTC strings)."""
    cfg = triangle_config(config)
    rows = find_triangle_candidates(df, timeframe, cfg, symbol=symbol)
    if not cfg["include_stale"]:
        rows = [r for r in rows if r["status"] != "stale"]
    rows = [r for r in rows if r["score"] >= float(cfg["min_score"])]
    return sort_triangles(deduplicate_triangles(rows))
