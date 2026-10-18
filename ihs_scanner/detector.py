"""Inverse head-and-shoulders detection (spec steps 2-10, charter D5-D12).

Everything is computed "as of" the last bar of df: the caller passes only
closed bars, and results depend only on df (truncation tests lock this in).
"""
from __future__ import annotations

import math
from typing import Any, Mapping

import numpy as np
import pandas as pd

from .config import TIMEFRAMES, merge_config, tf_value
from .neckline import calculate_neckline
from .scoring import calculate_pattern_score, result_sort_key
from .swings import SwingPoint, find_swing_points, get_timestamps

PATTERN_TYPE = "inverse_head_and_shoulders"
REQUIRED_COLUMNS = ("open", "high", "low", "close", "volume")


def _iso(ts: pd.Timestamp | None) -> str | None:
    if ts is None:
        return None
    return pd.Timestamp(ts).tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%SZ")


def _prior_average(volume: np.ndarray, window: int) -> np.ndarray:
    """avg[i] = mean(volume[i-window : i]) -- excludes bar i itself (D8).

    Uses the available bars when fewer than `window` precede i; avg[0] is NaN.
    """
    n = volume.shape[0]
    cs = np.concatenate([[0.0], np.cumsum(volume, dtype=float)])
    i = np.arange(n)
    start = np.maximum(0, i - window)
    cnt = i - start
    with np.errstate(invalid="ignore", divide="ignore"):
        avg = (cs[i] - cs[start]) / cnt
    avg[cnt == 0] = np.nan
    return avg


def _highest_between(high_idx: np.ndarray, high_px: np.ndarray, left: int, right: int) -> int | None:
    """Position (into the swing-high list) of the highest swing high with left < index < right (D5).

    Ties: the earliest one wins (np.argmax), which keeps results deterministic.
    """
    a = int(np.searchsorted(high_idx, left, side="right"))
    b = int(np.searchsorted(high_idx, right, side="left"))
    if a >= b:
        return None
    return a + int(np.argmax(high_px[a:b]))


def _intervals_overlap(a: Mapping[str, Any], b: Mapping[str, Any]) -> bool:
    return a["LS_time"] <= b["RS_time"] and b["LS_time"] <= a["RS_time"]


def deduplicate_patterns(patterns: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """D11: among patterns of the same symbol+timeframe whose [LS, RS] intervals
    overlap (closed intervals), keep only the highest score; ties keep the newer
    one (later RS, then later LS). Greedy by that priority."""
    order = sorted(
        patterns,
        key=lambda p: (-p["pattern_score"], -pd.Timestamp(p["RS_time"]).value,
                       -pd.Timestamp(p["LS_time"]).value),
    )
    kept: list[dict[str, Any]] = []
    for p in order:
        clash = any(
            q["symbol"] == p["symbol"] and q["timeframe"] == p["timeframe"]
            and _intervals_overlap(p, q)
            for q in kept
        )
        if not clash:
            kept.append(p)
    return kept


def _validate(df: pd.DataFrame, timeframe: str) -> None:
    if timeframe not in TIMEFRAMES:
        raise ValueError(f"unsupported timeframe {timeframe!r}; expected one of {TIMEFRAMES}")
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"df is missing columns: {missing}")


def find_pattern_candidates(df: pd.DataFrame, timeframe: str,
                            config: Mapping[str, Any] | None = None,
                            symbol: str | None = None) -> list[dict[str, Any]]:
    """All structurally valid, non-invalidated patterns after mode/status filters,
    BEFORE de-duplication. Exposed for tests / research; normal callers use
    detect_inverse_head_shoulders()."""
    cfg = merge_config(config)
    _validate(df, timeframe)
    n = len(df)
    N = int(tf_value(cfg, "pivot_window_by_timeframe", timeframe))
    if n < 2 * N + 1:
        return []

    shoulder_limit = float(tf_value(cfg, "shoulder_diff_limit_by_timeframe", timeframe))
    min_depth = float(tf_value(cfg, "min_head_depth_by_timeframe", timeframe))
    max_angle = float(cfg["max_neckline_angle"])
    angle_scale = float(cfg["angle_scale"])
    buffer = float(tf_value(cfg, "breakout_buffer_by_timeframe", timeframe))
    max_bars = int(tf_value(cfg, "max_pattern_bars_by_timeframe", timeframe))
    max_after_rs = int(tf_value(cfg, "max_bars_after_rs", timeframe))
    max_age = int(tf_value(cfg, "max_breakout_age", timeframe))
    breakout_mode = bool(cfg["enable_breakout_filter"])
    volume_filter = bool(cfg["enable_volume_filter"])
    min_ratio = float(cfg["min_volume_ratio"])
    include_stale = bool(cfg["include_stale"])
    ideal_depth = float(cfg["ideal_head_depth"])
    dist_range = float(cfg["breakout_distance_range"])
    vol_window = int(cfg["volume_avg_window"])
    mode = "breakout" if breakout_mode else "candidate"
    # D19 extra shape rules
    balance_rule = bool(cfg["enable_balance_rule"])
    sym_ratio = cfg["max_time_symmetry_ratio"]
    sym_ratio = None if sym_ratio is None else float(sym_ratio)
    min_bars = cfg["min_pattern_bars"]
    min_bars = None if min_bars is None else int(min_bars)
    prior_downtrend = bool(cfg["require_prior_downtrend"])
    prior_bars = int(tf_value(cfg, "prior_trend_bars", timeframe))

    times = get_timestamps(df)
    low = df["low"].to_numpy(dtype=float)
    close = df["close"].to_numpy(dtype=float)
    volume = df["volume"].to_numpy(dtype=float)

    swings = find_swing_points(df, N)
    lows: list[SwingPoint] = swings["lows"]
    highs: list[SwingPoint] = swings["highs"]
    if len(lows) < 3 or len(highs) < 2:
        return []
    low_idx = np.array([p.index for p in lows], dtype=np.int64)
    high_idx = np.array([p.index for p in highs], dtype=np.int64)
    high_px = np.array([p.price for p in highs], dtype=float)

    avg_prior = _prior_average(volume, vol_window)
    with np.errstate(invalid="ignore", divide="ignore"):
        vol_ratio_all = np.where(avg_prior > 0, volume / avg_prior, np.nan)

    last = n - 1
    current_close = float(close[last])
    out: list[dict[str, Any]] = []

    for k, H in enumerate(lows):
        hi, h_low = H.index, H.price
        # D6: H must be the lowest low of [LS, RS] -> LS/RS cannot lie beyond the
        # nearest bar (on each side) whose low is below H.low. Also caps by max_bars.
        lb = max(0, hi - max_bars)
        below = np.flatnonzero(low[lb:hi] < h_low)
        left_limit = lb + int(below[-1]) + 1 if below.size else lb
        ub = min(last, hi + max_bars)
        below = np.flatnonzero(low[hi + 1: ub + 1] < h_low)
        right_limit = hi + int(below[0]) if below.size else ub

        a = int(np.searchsorted(low_idx, left_limit, side="left"))
        b = int(np.searchsorted(low_idx, right_limit, side="right"))
        ls_list = [p for p in lows[a:k] if p.price > h_low]  # H.low < LS.low (strict)
        rs_list = [p for p in lows[k + 1:b] if p.price > h_low]  # H.low < RS.low (strict)
        if not ls_list or not rs_list:
            continue

        n2_cache: dict[int, int | None] = {}
        for LS in ls_list:
            n1_pos = _highest_between(high_idx, high_px, LS.index, hi)  # D5
            if n1_pos is None:
                continue
            N1 = highs[n1_pos]
            for RS in rs_list:
                if RS.index - LS.index > max_bars:  # D6 total length
                    break  # rs_list is sorted by index
                if min_bars is not None and RS.index - LS.index < min_bars:  # D19 optional
                    continue
                # D19 symmetry (neurotrader888 check_ihs_pattern): neither side may be
                # more than sym_ratio times as long (in bars) as the other
                l_to_h = hi - LS.index
                r_to_h = RS.index - hi
                if sym_ratio is not None and (r_to_h > sym_ratio * l_to_h
                                              or l_to_h > sym_ratio * r_to_h):
                    continue
                # spec step 3: shoulder difference
                shoulder_avg = (LS.price + RS.price) / 2.0
                shoulder_diff = abs(LS.price - RS.price) / shoulder_avg
                if shoulder_diff > shoulder_limit:
                    continue
                # spec step 4: head depth
                head_depth = (shoulder_avg - h_low) / shoulder_avg
                if head_depth <= 0 or head_depth < min_depth:
                    continue
                if RS.index not in n2_cache:
                    n2_cache[RS.index] = _highest_between(high_idx, high_px, hi, RS.index)
                n2_pos = n2_cache[RS.index]
                if n2_pos is None:
                    continue
                N2 = highs[n2_pos]
                # D19 balance (neurotrader888 check_ihs_pattern): each shoulder must not be
                # above the midpoint of the other shoulder and its neckline high
                if balance_rule:
                    r_midpoint = 0.5 * (RS.price + N2.price)
                    l_midpoint = 0.5 * (LS.price + N1.price)
                    if LS.price > r_midpoint or RS.price > l_midpoint:
                        continue
                # spec step 5: neckline angle
                neck = calculate_neckline(N1, N2, angle_scale)
                angle = neck.angle
                if abs(angle) > max_angle:
                    continue
                # D19 optional prior downtrend (concept: yanivil/SignalSync): the highest close
                # in the prior_bars bars before LS must be >= LS.low + head height
                if prior_downtrend:
                    p0 = max(0, LS.index - prior_bars)
                    if p0 >= LS.index:
                        continue
                    head_height = float(neck.price_at(hi)) - h_low
                    if float(np.max(close[p0:LS.index])) - LS.price < head_height:
                        continue

                # D7 invalidation + D8 breakout, scanning bars after RS
                breakout_i: int | None = None
                cross_i: int | None = None  # first price-only cross (D14)
                start = RS.index + 1
                if start <= last:
                    idx = np.arange(start, n)
                    neck_vals = neck.price_at(idx)
                    ok = close[start:] > neck_vals * (1.0 + buffer)
                    cross_hits = np.flatnonzero(ok)
                    if cross_hits.size:
                        cross_i = start + int(cross_hits[0])
                    if volume_filter:
                        ratio = vol_ratio_all[start:]
                        ok &= np.nan_to_num(ratio, nan=-1.0) >= min_ratio
                    hits = np.flatnonzero(ok)
                    if hits.size:
                        breakout_i = start + int(hits[0])
                    stop = breakout_i if breakout_i is not None else n  # lows strictly before breakout
                    if np.any(low[start:stop] < RS.price):
                        continue  # D7: right shoulder broken before breakout

                breakout = breakout_i is not None
                # D9 status. D14: age runs from the first price cross and is not reset by a
                # later volume-confirmed re-break (SignalSync: resetting raised false positives).
                if breakout:
                    status = "confirmed" if last - cross_i <= max_age else "stale"
                elif cross_i is not None:  # crossed on low volume: still watchlist, same age clock
                    fresh = last - cross_i <= max_age and last - RS.index <= max_after_rs
                    status = "watchlist" if fresh else "stale"
                else:
                    status = "watchlist" if last - RS.index <= max_after_rs else "stale"
                if status == "stale" and not include_stale:
                    continue
                if breakout_mode and not breakout:
                    continue

                current_neck = float(neck.price_at(last))  # D12
                distance = abs(current_close - current_neck) / current_neck
                scores = calculate_pattern_score(
                    shoulder_diff, shoulder_limit, head_depth, angle, max_angle,
                    breakout, distance, ideal_depth, dist_range)

                volume_ratio = None
                avg_vol = None
                if breakout:
                    av = avg_prior[breakout_i]
                    if math.isfinite(av):
                        avg_vol = float(av)
                    vr = vol_ratio_all[breakout_i]
                    if math.isfinite(vr):
                        volume_ratio = float(vr)

                ls_t, h_t, rs_t = _iso(LS.time), _iso(H.time), _iso(RS.time)
                out.append({
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "pattern_type": PATTERN_TYPE,
                    "mode": mode,
                    "status": status,
                    "pattern_score": round(scores["pattern_score"], 2),
                    "shoulder_score": round(scores["shoulder_score"], 2),
                    "head_depth_score": round(scores["head_depth_score"], 2),
                    "neckline_score": round(scores["neckline_score"], 2),
                    "breakout_score": round(scores["breakout_score"], 2),
                    "LS_time": LS.time, "LS_price": LS.price,
                    "N1_time": N1.time, "N1_price": N1.price,
                    "H_time": H.time, "H_price": H.price,
                    "N2_time": N2.time, "N2_price": N2.price,
                    "RS_time": RS.time, "RS_price": RS.price,
                    "RS_confirm_time": times[RS.confirm_index],
                    "shoulder_diff": shoulder_diff,
                    "head_depth": head_depth,
                    "neckline_angle": angle,
                    "neckline_slope_pct_per_bar": neck.slope_pct_per_bar,
                    "time_symmetry_ratio": max(l_to_h, r_to_h) / min(l_to_h, r_to_h),
                    "current_close": current_close,
                    "current_neckline_price": current_neck,
                    "distance_to_neckline": distance,
                    "breakout_detected": breakout,
                    "breakout_time": times[breakout_i] if breakout else None,
                    "breakout_price": float(close[breakout_i]) if breakout else None,
                    "volume_ratio": volume_ratio,
                    "average_volume_20": avg_vol,
                    "bars_since_rs": last - RS.index,
                    "bars_since_breakout": (last - breakout_i) if breakout else None,
                    "first_cross_time": times[cross_i] if cross_i is not None else None,
                    # earliest bar at which this signal is knowable live: a breakout can
                    # happen inside the RS confirmation window (RS.index < b < RS.index+N),
                    # but it can only be reported once RS is confirmed.
                    "signal_time": times[max(RS.confirm_index, breakout_i)] if breakout
                    else times[RS.confirm_index],
                    "as_of_time": times[last],
                    "pattern_id": f"{symbol}|{timeframe}|{ls_t}|{h_t}|{rs_t}",
                })
    return out


def detect_inverse_head_shoulders(df: pd.DataFrame, timeframe: str,
                                  config: Mapping[str, Any] | None = None,
                                  symbol: str | None = None) -> list[dict[str, Any]]:
    """Detect IHS patterns as of the last bar of df.

    config: partial overrides are fine (merged onto DEFAULT_CONFIG).
    mode: enable_breakout_filter=False -> "candidate" (watchlist + confirmed),
          True -> "breakout" (only patterns with a detected breakout).
    Returns de-duplicated (D11) results sorted by spec step 11 order.
    """
    candidates = find_pattern_candidates(df, timeframe, config, symbol)
    return sorted(deduplicate_patterns(candidates), key=result_sort_key)
