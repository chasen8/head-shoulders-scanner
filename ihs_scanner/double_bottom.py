"""Double bottom ("W", charter D49): B1 - P - B2 with a horizontal neckline at P.

Reuses the IHS machinery as plain functions only (swings.pivot_mask / get_timestamps, detector.wilder_atr); the IHS
and triangle detectors are untouched. Pure OHLCV, closed bars only. Rules and values: config.DOUBLE_BOTTOM_CONFIG,
docs/backtest-double-bottom.md section 2.

  B1, B2   strict swing lows (N bars each side, known N bars later, D4); B2 may be PROVISIONAL (status forming): the
           lowest low of the last N bars when it is strictly below the N bars before it and the bars after it
  P        the highest high between B1 and B2; must itself be a confirmed swing high
  H        min(B1, B2); invalidation price
  Also required: prior decline into B1, bottoms within tolerance, rally to P, bars between B1 and B2, no close below
  H between B1 and B2 (nor after B2 before the breakout): see the config comments.

Status: forming (B2 provisional) / watchlist (B2 confirmed, no breakout) / confirmed (a bar's HIGH above
P * (1 + buffer), D36, age <= max_breakout_age) / stale. D36 "breakout first" for the same B1: a candidate whose B2
comes after another valid candidate's breakout is dropped unless that breakout failed.

No look-ahead: the result as of bar t is a function of df[:t+1] only (tests/test_double_bottom.py truncation tests).
"""
from __future__ import annotations

import math
from typing import Any, Mapping

import numpy as np
import pandas as pd

from .config import TIMEFRAMES, double_bottom_config, tf_value
from .detector import wilder_atr
from .swings import get_timestamps, pivot_mask

PATTERN_TYPE = "double_bottom"
STATUSES = ("forming", "watchlist", "confirmed", "stale")
REQUIRED_COLUMNS = ("open", "high", "low", "close", "volume")


def _iso(ts: pd.Timestamp) -> str:
    t = pd.Timestamp(ts)
    t = t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def _clip01(x: float) -> float:
    x = float(x)
    return 0.0 if not math.isfinite(x) else max(0.0, min(1.0, x))


def _validate(df: pd.DataFrame, timeframe: str) -> None:
    if timeframe not in TIMEFRAMES:
        raise ValueError(f"unsupported timeframe {timeframe!r}; expected one of {TIMEFRAMES}")
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"df is missing columns: {missing}")


def find_double_bottom_candidates(df: pd.DataFrame, timeframe: str, cfg: Mapping[str, Any],
                                  symbol: str = "") -> list[dict[str, Any]]:
    """Every structurally valid W as of the last bar, with status (stale included), BEFORE de-duplication."""
    _validate(df, timeframe)
    n = len(df)
    N = int(tf_value(cfg, "pivot_window_by_timeframe", timeframe))
    if n < 2 * N + 1:
        return []
    last = n - 1
    ts = get_timestamps(df)
    high = df["high"].to_numpy(dtype=float)
    low = df["low"].to_numpy(dtype=float)
    close = df["close"].to_numpy(dtype=float)
    atr = wilder_atr(df, int(cfg["atr_length"]))

    tol_mult = float(cfg["bottom_tol_atr_mult"])
    tol_floor = float(cfg["bottom_tol_floor_pct"])
    tol_cap = float(tf_value(cfg, "bottom_tol_cap_pct_by_timeframe", timeframe))
    undercut = float(cfg["max_undercut_of_tol"])
    min_rise = float(tf_value(cfg, "min_rise_pct_by_timeframe", timeframe))
    min_rise_atr = float(cfg["min_rise_atr"])
    min_between = int(tf_value(cfg, "min_bars_between_by_timeframe", timeframe))
    max_between = int(tf_value(cfg, "max_bars_between_by_timeframe", timeframe))
    lookback = int(tf_value(cfg, "prior_lookback_by_timeframe", timeframe))
    min_decl = float(tf_value(cfg, "min_prior_decline_pct_by_timeframe", timeframe))
    min_decl_atr = float(cfg["min_prior_decline_atr"])
    basis = str(cfg["breakout_price"])
    if basis not in ("close", "high"):
        raise ValueError(f"breakout_price must be 'close' or 'high', got {basis!r}")
    brk_px = high if basis == "high" else close
    buffer = float(tf_value(cfg, "breakout_buffer_by_timeframe", timeframe))
    max_after = int(tf_value(cfg, "max_bars_after_b2", timeframe))
    max_age = int(tf_value(cfg, "max_breakout_age", timeframe))
    max_runaway = cfg["max_runaway_of_height"]
    max_fallback = cfg["max_fallback_of_height"]
    tp1_level = float(cfg["tp1_fib_level"])
    ideal_rise = float(tf_value(cfg, "ideal_rise_pct_by_timeframe", timeframe))
    ideal_decl = float(tf_value(cfg, "ideal_prior_decline_pct_by_timeframe", timeframe))
    include_stale = bool(cfg["include_stale"])

    lmask = pivot_mask(low, N, "low")
    hmask = pivot_mask(high, N, "high")
    lows_idx = [int(i) for i in np.flatnonzero(lmask)]
    # provisional B2: lowest low of the last N bars, strictly below the N bars before it and the bars after it
    prov = None
    if last - N + 1 >= N:
        j = last - N + 1 + int(np.argmin(low[last - N + 1:last + 1]))
        if (j >= N and not lmask[j] and bool(np.all(low[j] < low[j - N:j])) and bool(np.all(low[j] < low[j + 1:last + 1]))):
            prov = j
    b2_list = lows_idx + ([prov] if prov is not None else [])

    cands: list[tuple[int, int, int | None, dict[str, Any]]] = []
    for b2 in b2_list:
        v2 = float(low[b2])
        a2 = float(atr[b2])
        if not math.isfinite(a2) or a2 <= 0 or not math.isfinite(v2) or v2 <= 0:
            continue
        confirmed_b2 = b2 != prov
        for b1 in lows_idx:
            gap = b2 - b1
            if gap < min_between:
                break  # lows_idx ascending: every later b1 is closer still
            if gap > max_between:
                continue
            v1 = float(low[b1])
            avg = 0.5 * (v1 + v2)
            tol = min(max(tol_mult * a2, tol_floor * avg), tol_cap * avg)
            d = v2 - v1
            if d > tol or d < -undercut * tol:
                continue
            p = b1 + 1 + int(np.argmax(high[b1 + 1:b2]))
            if not hmask[p]:
                continue
            pv = float(high[p])
            hi_b, h_low = max(v1, v2), min(v1, v2)
            if pv / hi_b - 1.0 < min_rise or pv - hi_b < min_rise_atr * a2:
                continue
            if close[b1 + 1:b2].min() < h_low:
                continue
            a1 = float(atr[b1])
            if not math.isfinite(a1) or b1 < 1:
                continue
            pre = float(high[max(0, b1 - lookback):b1].max())
            decl = (pre - v1) / pre
            if pre < pv or decl < min_decl or pre - v1 < min_decl_atr * a1:
                continue
            # --- after B2: breakout (first bar whose high / close is above P * (1 + buffer)) and invalidation
            start = b2 + 1
            cross_i = None
            if start <= last:
                hits = np.flatnonzero(brk_px[start:] > pv * (1.0 + buffer))
                if hits.size:
                    cross_i = start + int(hits[0])
            stop = cross_i if cross_i is not None else n
            if start < stop and close[start:stop].min() < h_low:
                continue  # closed below H before breaking out: the W is broken
            if not confirmed_b2:
                status = "forming"
            elif cross_i is not None:
                status = "confirmed" if last - cross_i <= max_age else "stale"
            else:
                status = "watchlist" if last - b2 <= max_after else "stale"
            height = pv - h_low
            cur = float(close[last])
            runaway = (cur - pv) / height
            fallback = (pv - cur) / height if cross_i is not None else None
            stale_reason = "age" if status == "stale" else None
            if status != "stale":
                if max_runaway is not None and runaway > float(max_runaway):
                    status, stale_reason = "stale", "runaway"
                elif max_fallback is not None and fallback is not None and fallback > float(max_fallback):
                    status, stale_reason = "stale", "failed_breakout"
            if status == "stale" and not include_stale:
                continue
            rise = pv / hi_b - 1.0
            broke = cross_i is not None
            parts = {
                "symmetry_score": 30.0 * _clip01(1.0 - abs(d) / tol),
                "rise_score": 25.0 * _clip01(rise / ideal_rise),
                "decline_score": 20.0 * _clip01(decl / ideal_decl),
                "breakout_score": 25.0 if broke else 25.0 * _clip01(1.0 - max(0.0, pv - cur) / height),
            }
            # earliest bar at which the signal is knowable live (like IHS): confirmed B2 -> B2 + N; provisional B2 ->
            # once the B2 bar has closed and P is confirmed
            known = max(b2, p + N) if not confirmed_b2 else b2 + N
            signal_i = max(known, cross_i) if broke else known
            b1_t, p_t, b2_t = _iso(ts[b1]), _iso(ts[p]), _iso(ts[b2])
            row = {
                "symbol": symbol, "timeframe": timeframe, "pattern_type": PATTERN_TYPE, "status": status,
                "stale_reason": stale_reason, "pattern_score": round(sum(parts.values()), 2),
                **{k: round(v, 2) for k, v in parts.items()},
                "B1_time": b1_t, "B1_price": v1, "P_time": p_t, "P_price": pv,
                "B2_time": b2_t, "B2_price": v2,
                "B2_confirm_time": _iso(ts[b2 + N]) if confirmed_b2 else None,
                "B2_provisional": not confirmed_b2,
                "bottom_diff_pct": float(d / avg), "bottom_tol_pct": float(tol / avg),
                "rise_pct": float(rise), "prior_decline_pct": float(decl), "bars_between": int(gap),
                "prior_high": pre, "neckline": pv, "breakout_level": pv, "height": float(height),
                "tp1": float(h_low * (pv / h_low) ** tp1_level), "tp2": float(pv * (1.0 + height / h_low)),
                "invalidation_price": float(h_low),
                "current_close": cur, "distance_to_neckline": float(abs(cur - pv) / pv),
                "breakout_detected": broke, "breakout_time": _iso(ts[cross_i]) if broke else None,
                "breakout_price": float(brk_px[cross_i]) if broke else None, "breakout_basis": basis,
                "bars_since_b2": int(last - b2), "bars_since_breakout": (last - cross_i) if broke else None,
                "signal_time": _iso(ts[signal_i]), "as_of_time": _iso(ts[last]),
                "pattern_id": f"{symbol}|{timeframe}|db|{b1_t}|{p_t}|{b2_t}",
            }
            cands.append((b1, b2, cross_i, row))
    return _breakout_first(cands, low)


def _breakout_first(cands: list[tuple[int, int, int | None, dict[str, Any]]], low: np.ndarray) -> list[dict[str, Any]]:
    """D36 B for W: among candidates with the same B1, one whose B2 comes after another candidate's breakout bar is
    that breakout's retest, not a new W -- unless the breakout failed (a low from the breakout bar up to this B2
    went below the earlier candidate's H)."""
    by_b1: dict[int, list] = {}
    for item in cands:
        by_b1.setdefault(item[0], []).append(item)
    out = []
    for items in by_b1.values():
        items.sort(key=lambda x: x[1])
        crossed: list[tuple[int, float]] = []  # (breakout bar, H) of the kept candidates that broke out
        for _b1, b2, cross_i, row in items:
            if any(c < b2 and float(low[c:b2 + 1].min()) >= h_a for c, h_a in crossed):
                continue
            out.append(row)
            if cross_i is not None:
                crossed.append((cross_i, row["invalidation_price"]))
    return out


def deduplicate_double_bottoms(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Of candidates whose [B1, B2] spans overlap (more than a shared end point) keep one: breakout first, then the
    better bottom match (|diff| / tolerance), then the newer B2. The kept rows come back in that order."""
    def key(r):
        return (0 if r["breakout_detected"] else 1,
                abs(r["bottom_diff_pct"]) / max(r["bottom_tol_pct"], 1e-12),
                -pd.Timestamp(r["B2_time"]).value)
    kept: list[dict[str, Any]] = []
    spans: list[tuple[int, int]] = []
    for r in sorted(rows, key=key):
        a0, a1 = pd.Timestamp(r["B1_time"]).value, pd.Timestamp(r["B2_time"]).value
        if any(a0 < e and s < a1 for s, e in spans):
            continue
        kept.append(r)
        spans.append((a0, a1))
    return kept


def sort_double_bottoms(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rank = {"confirmed": 0, "watchlist": 1, "forming": 2, "stale": 3}
    return sorted(rows, key=lambda r: (rank.get(r["status"], 9), -r["pattern_score"], r["symbol"], r["timeframe"]))


def detect_double_bottoms(df: pd.DataFrame, timeframe: str, config: Mapping[str, Any] | None = None,
                          symbol: str = "", market: str | None = None) -> list[dict[str, Any]]:
    """W bottoms of one series as of its last bar: candidates -> stale filter (unless include_stale) -> min_score ->
    de-duplication -> sorted. Rows are JSON-ready (times are ISO 8601 UTC strings)."""
    cfg = double_bottom_config(config, market=market)
    rows = find_double_bottom_candidates(df, timeframe, cfg, symbol=symbol)
    if not cfg["include_stale"]:
        rows = [r for r in rows if r["status"] != "stale"]
    rows = [r for r in rows if r["pattern_score"] >= float(cfg["min_score"])]
    return sort_double_bottoms(deduplicate_double_bottoms(rows))
