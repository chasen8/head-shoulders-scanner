"""Inverse head-and-shoulders detection (spec steps 2-10, charter D5-D12, D19, D21, D22).

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
from .scoring import calculate_pattern_score, calculate_template_score, result_sort_key
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


def wilder_atr(df: pd.DataFrame, length: int = 14) -> np.ndarray:
    """D21 R1: Wilder ATR. TR[0] = high-low; TR[i] = max(high-low, |high-close[i-1]|, |low-close[i-1]|);
    ATR[length-1] = mean(TR[0:length]); ATR[i] = (ATR[i-1]*(length-1) + TR[i]) / length; earlier = NaN.

    ATR[i] depends only on bars <= i, so it never looks ahead (truncation test locks this in)."""
    if length < 1:
        raise ValueError("atr_length must be >= 1")
    high = df["high"].to_numpy(dtype=float)
    low = df["low"].to_numpy(dtype=float)
    close = df["close"].to_numpy(dtype=float)
    n = high.shape[0]
    out = np.full(n, np.nan)
    if n < length:
        return out
    prev_close = np.concatenate([[np.nan], close[:-1]])
    tr = np.fmax(high - low, np.fmax(np.abs(high - prev_close), np.abs(low - prev_close)))
    tr[0] = high[0] - low[0]
    atr = float(np.mean(tr[:length]))
    out[length - 1] = atr
    for i in range(length, n):
        atr = (atr * (length - 1) + tr[i]) / length
        out[i] = atr
    return out


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


def _opt_float(value: Any) -> float | None:
    return None if value is None else float(value)


def find_pattern_candidates(df: pd.DataFrame, timeframe: str,
                            config: Mapping[str, Any] | None = None,
                            symbol: str | None = None) -> list[dict[str, Any]]:
    """All structurally valid, non-invalidated patterns after mode/status filters,
    BEFORE de-duplication. Exposed for tests / research; normal callers use
    detect_inverse_head_shoulders().

    Both profiles (D22) run through this one loop; every rule is a config switch. Per head H the
    LS x RS candidate pairs are first filtered with vectorised rules (length, time symmetry, spec
    shoulder diff / head depth, D22 T1-T3), then the remaining pairs get the scalar rules."""
    cfg = merge_config(config)
    _validate(df, timeframe)
    n = len(df)
    N = int(tf_value(cfg, "pivot_window_by_timeframe", timeframe))
    if n < 2 * N + 1:
        return []

    # spec step 3 shoulder diff: None = off (D22 template); the spec score then uses the classic limit
    shoulder_limit = _opt_float(tf_value(cfg, "shoulder_diff_limit_by_timeframe", timeframe))
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
    profile = str(cfg["profile"])
    score_method = str(cfg["score_method"])
    if score_method not in ("spec", "template"):
        raise ValueError(f"score_method must be 'spec' or 'template', got {score_method!r}")
    score_shoulder_limit = shoulder_limit if shoulder_limit is not None else float(
        {"4h": 0.05, "1d": 0.10, "1w": 0.10}[timeframe])  # spec step 3 values, score only
    # D19 extra shape rules
    balance_rule = bool(cfg["enable_balance_rule"])
    sym_ratio = _opt_float(cfg["max_time_symmetry_ratio"])
    min_bars = tf_value(cfg, "min_pattern_bars", timeframe)  # int, per-timeframe dict (D22) or None
    min_bars = None if min_bars is None else int(min_bars)
    prior_downtrend = bool(cfg["require_prior_downtrend"])
    prior_bars = int(tf_value(cfg, "prior_trend_bars", timeframe))
    # D21 rules (formulas and sources: config.py); None / False = off
    min_head_atr = _opt_float(cfg["min_head_atr"])
    start_rule = bool(cfg["enable_pattern_start_rule"])
    start_lookback = cfg["pattern_start_lookback"]
    start_lookback = None if start_lookback is None else int(start_lookback)
    start_price_key = str(cfg["pattern_start_price"])
    if start_price_key not in ("close", "high"):
        raise ValueError(f"pattern_start_price must be 'close' or 'high', got {start_price_key!r}")
    max_total_slope = _opt_float(cfg["max_neckline_total_slope"])
    max_sd_of_head = _opt_float(cfg["max_shoulder_diff_of_head"])
    min_depth_ratio = _opt_float(cfg["min_shoulder_depth_ratio"])
    max_depth_ratio = _opt_float(cfg["max_shoulder_depth_ratio"])
    # D22 template rules T1-T6 (formulas: config.py); None / False = off
    min_drop = _opt_float(tf_value(cfg, "min_shoulder_drop_by_timeframe", timeframe))
    max_drop_diff = _opt_float(cfg["max_shoulder_drop_diff"])
    max_drop_rel = _opt_float(cfg["max_shoulder_drop_rel_diff"])
    min_rise = _opt_float(tf_value(cfg, "min_head_rise_by_timeframe", timeframe))
    min_prom = _opt_float(cfg["min_head_prominence"])
    max_violation = _opt_float(cfg["max_neckline_violation"])
    max_tilt = _opt_float(cfg["max_neckline_tilt_of_head"])
    forming_on = bool(cfg["enable_forming_rs"])
    n1_first = bool(cfg["require_n1_first_high_after_ls"])
    lead_in = _opt_float(cfg["head_lead_in"])
    max_neck_rise = _opt_float(cfg["max_neckline_rise"])
    max_wick = _opt_float(cfg["max_neckline_wick_of_head"])
    ideal_prom = float(cfg["ideal_head_prominence"])
    ideal_rise = float(cfg["ideal_head_rise"])

    times = get_timestamps(df)
    high = df["high"].to_numpy(dtype=float)
    low = df["low"].to_numpy(dtype=float)
    close = df["close"].to_numpy(dtype=float)
    volume = df["volume"].to_numpy(dtype=float)
    body_top = np.maximum(df["open"].to_numpy(dtype=float), close)
    start_px = df[start_price_key].to_numpy(dtype=float)
    atr_all = wilder_atr(df, int(cfg["atr_length"]))

    swings = find_swing_points(df, N)
    lows: list[SwingPoint] = swings["lows"]
    highs: list[SwingPoint] = swings["highs"]
    if len(lows) < (2 if forming_on else 3) or len(highs) < (1 if forming_on else 2):
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
        below_r = np.flatnonzero(low[hi + 1: ub + 1] < h_low)
        right_limit = hi + int(below_r[0]) if below_r.size else ub

        a = int(np.searchsorted(low_idx, left_limit, side="left"))
        b = int(np.searchsorted(low_idx, right_limit, side="right"))
        ls_pts = [p for p in lows[a:k] if p.price > h_low]  # H.low < LS.low (strict)
        if not ls_pts:
            continue
        rs_pts = [p for p in lows[k + 1:b] if p.price > h_low]  # H.low < RS.low (strict)
        rs_forming = [False] * len(rs_pts)
        # D22 T6 forming RS: no low below H.low since H (else the pattern is void), N2 = highest
        # confirmed swing high after H, RS = lowest low after N2 while it cannot be a confirmed
        # pivot yet (index > last - N). Ties: the earliest lowest bar.
        if forming_on and right_limit == last and not below_r.size:
            n2f = _highest_between(high_idx, high_px, hi, last + 1)
            if n2f is not None and highs[n2f].index < last:
                s = highs[n2f].index + 1
                j = s + int(np.argmin(low[s:last + 1]))
                if j > last - N and low[j] > h_low:
                    rs_pts.append(SwingPoint(kind="low", index=j, confirm_index=j + N,
                                             price=float(low[j]), time=times[j]))
                    rs_forming.append(True)
        if not rs_pts:
            continue

        # D22 T8: last bar before H (anywhere, not only within max_bars) whose low is below H.low
        prev_lower = None
        if lead_in is not None:
            lower = np.flatnonzero(low[:hi] < h_low)
            prev_lower = int(lower[-1]) if lower.size else None
        # N1 per LS / N2 per RS (D5); candidates without one drop out
        ls_keep, n1_pos = [], []
        for p in ls_pts:
            if prev_lower is not None and prev_lower >= p.index - math.ceil(lead_in * (hi - p.index)):
                continue  # T8: price was below the head within the lead-in window before LS
            pos = _highest_between(high_idx, high_px, p.index, hi)
            # D22 T7: N1 must be the first swing high after LS, and LS the lowest low of [LS, N1]
            if pos is not None and n1_first and (
                    pos != int(np.searchsorted(high_idx, p.index, side="right"))
                    or float(np.min(low[p.index:highs[pos].index])) < p.price):
                continue
            if pos is not None:
                ls_keep.append(p)
                n1_pos.append(pos)
        rs_keep, n2_pos, form_keep = [], [], []
        for p, f in zip(rs_pts, rs_forming):
            pos = _highest_between(high_idx, high_px, hi, p.index)
            if pos is not None:
                rs_keep.append(p)
                n2_pos.append(pos)
                form_keep.append(f)
        if not ls_keep or not rs_keep:
            continue

        ls_i = np.array([p.index for p in ls_keep], dtype=float)[:, None]
        ls_p = np.array([p.price for p in ls_keep], dtype=float)[:, None]
        n1_i = high_idx[n1_pos].astype(float)[:, None]
        n1_p = high_px[n1_pos][:, None]
        rs_i = np.array([p.index for p in rs_keep], dtype=float)[None, :]
        rs_p = np.array([p.price for p in rs_keep], dtype=float)[None, :]
        n2_i = high_idx[n2_pos].astype(float)[None, :]
        n2_p = high_px[n2_pos][None, :]

        # ---- vectorised pair rules ----
        width = rs_i - ls_i
        ok = width <= max_bars  # D6 total length
        if min_bars is not None:  # D19 optional
            ok &= width >= min_bars
        l_to_h_m = hi - ls_i
        r_to_h_m = rs_i - hi
        if sym_ratio is not None:  # D19 symmetry (neurotrader888 check_ihs_pattern)
            ok &= ~((r_to_h_m > sym_ratio * l_to_h_m) | (l_to_h_m > sym_ratio * r_to_h_m))
        sh_avg_m = (ls_p + rs_p) / 2.0
        if shoulder_limit is not None:  # spec step 3
            ok &= np.abs(ls_p - rs_p) / sh_avg_m <= shoulder_limit
        depth_m = (sh_avg_m - h_low) / sh_avg_m  # spec step 4
        ok &= (depth_m > 0) & (depth_m >= min_depth)
        with np.errstate(invalid="ignore", divide="ignore"):
            drop_l = (n1_p - ls_p) / n1_p  # D22: left shoulder fall from N1
            drop_r = (n2_p - rs_p) / n2_p
            drop_diff_m = np.abs(drop_l - drop_r)
            neck_h_m = n1_p + (n2_p - n1_p) * ((hi - n1_i) / (n2_i - n1_i))
            head_rise_m = neck_h_m / h_low - 1.0
            rise_m = np.maximum(n1_p / ls_p - 1.0, n2_p / rs_p - 1.0)
            prom_m = np.where(rise_m > 0, head_rise_m / rise_m, np.nan)
        if min_drop is not None:  # T1
            ok &= (drop_l >= min_drop) & (drop_r >= min_drop)
        if max_drop_diff is not None:  # T2 absolute
            ok &= drop_diff_m <= max_drop_diff
        if max_drop_rel is not None:  # T2 relative
            ok &= drop_diff_m <= max_drop_rel * np.maximum(drop_l, drop_r)
        if min_rise is not None:  # T3 head rise
            ok &= head_rise_m >= min_rise
        if min_prom is not None:  # T3 head prominence (NaN compares False -> rejected)
            ok &= prom_m >= min_prom

        atr_h = float(atr_all[hi])  # D21 R1: ATR at the head bar (bars <= H.index only)
        atr_ok = math.isfinite(atr_h) and atr_h > 0
        for ia, ib in np.argwhere(ok):
            LS, N1 = ls_keep[ia], highs[n1_pos[ia]]
            RS, N2 = rs_keep[ib], highs[n2_pos[ib]]
            forming = form_keep[ib]
            l_to_h = hi - LS.index
            r_to_h = RS.index - hi
            shoulder_avg = (LS.price + RS.price) / 2.0
            shoulder_diff = abs(LS.price - RS.price) / shoulder_avg
            head_depth = (shoulder_avg - h_low) / shoulder_avg
            # D21 R1 (SignalSync L1436): head at least k ATR below BOTH shoulders:
            # H.low <= min(LS.low, RS.low) - k * ATR[H]. Skipped when ATR[H] is NaN.
            head_gap = min(LS.price, RS.price) - h_low
            if min_head_atr is not None and atr_ok and head_gap < min_head_atr * atr_h:
                continue
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
            head_height = float(neck.price_at(hi)) - h_low
            if prior_downtrend:
                p0 = max(0, LS.index - prior_bars)
                if p0 >= LS.index:
                    continue
                if float(np.max(close[p0:LS.index])) - LS.price < head_height:
                    continue
            if head_height <= 0:  # cannot happen (N1/N2 highs > H.low); guards the ratios below
                continue
            # D21 R3 (SignalSync L1450-1456): total neckline rise/fall over LS->RS, fraction of close[H]
            slope_px = (N2.price - N1.price) / (N2.index - N1.index)
            total_slope = float(abs(slope_px * (RS.index - LS.index)) / close[hi])
            if max_total_slope is not None and total_slope > max_total_slope:
                continue
            # D21 R2 (SignalSync L1457-1459): shoulder difference in head heights
            sd_of_head = abs(LS.price - RS.price) / head_height
            if max_sd_of_head is not None and sd_of_head > max_sd_of_head:
                continue
            # D21 R21 (Savin et al. R6/R7, report definition): mean shoulder depth / head height
            depth_ratio = 0.5 * ((N1.price - LS.price) + (N2.price - RS.price)) / head_height
            if min_depth_ratio is not None and depth_ratio < min_depth_ratio:
                continue
            if max_depth_ratio is not None and depth_ratio > max_depth_ratio:
                continue
            # D22 T5: neckline tilt in head heights
            tilt_of_head = abs(N2.price - N1.price) / head_height
            if max_tilt is not None and tilt_of_head > max_tilt:
                continue
            # D22 T9: rising neckline cap
            neck_change = N2.price / N1.price - 1.0
            if max_neck_rise is not None and neck_change > max_neck_rise:
                continue
            # D22 T10: neckline highs are not lone wick spikes (body tops within +-N bars)
            wick_of_head = max((P.price - float(np.max(body_top[max(0, P.index - N):P.index + N + 1])))
                               for P in (N1, N2)) / head_height
            if max_wick is not None and wick_of_head > max_wick:
                continue
            # D22 T4: no bar strictly between N1 and RS (N2 itself excluded) pokes above the neckline
            # (extended past N2) by more than the tolerance
            seg = np.concatenate([np.arange(N1.index + 1, N2.index), np.arange(N2.index + 1, RS.index)])
            violation = float(np.max(high[seg] / neck.price_at(seg) - 1.0)) if seg.size else None
            if max_violation is not None and violation is not None and violation > max_violation:
                continue
            # D21 R11 (neurotrader888 L227-244): walking left from LS, the first bar whose price is
            # above the extended neckline marks the pattern start; none within the lookback -> reject
            look = start_lookback if start_lookback is not None else (N2.index - N1.index) - 1
            j0 = max(0, LS.index - look)
            pat_start_gap = None
            if look > 0 and LS.index > 0:
                js = np.arange(LS.index - 1, j0 - 1, -1)
                above = np.flatnonzero(start_px[js] > neck.price_at(js))
                if above.size:
                    pat_start_gap = int(above[0]) + 1  # bars before LS
            if start_rule and pat_start_gap is None:
                continue

            # D7 invalidation + D8 breakout, scanning bars after RS
            breakout_i: int | None = None
            cross_i: int | None = None  # first price-only cross (D14)
            start = RS.index + 1
            if start <= last:
                idx = np.arange(start, n)
                neck_vals = neck.price_at(idx)
                okb = close[start:] > neck_vals * (1.0 + buffer)
                cross_hits = np.flatnonzero(okb)
                if cross_hits.size:
                    cross_i = start + int(cross_hits[0])
                if volume_filter:
                    ratio = vol_ratio_all[start:]
                    okb &= np.nan_to_num(ratio, nan=-1.0) >= min_ratio
                hits = np.flatnonzero(okb)
                if hits.size:
                    breakout_i = start + int(hits[0])
                stop = breakout_i if breakout_i is not None else n  # lows strictly before breakout
                if np.any(low[start:stop] < RS.price):
                    continue  # D7: right shoulder broken before breakout (never true for a forming RS)

            breakout = breakout_i is not None
            # D9 status. D14: age runs from the first price cross and is not reset by a
            # later volume-confirmed re-break (SignalSync: resetting raised false positives).
            # D22: a provisional (unconfirmed) RS is always "forming", breakout or not.
            if forming:
                status = "forming"
            elif breakout:
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
            # D22 shape values (computed in both profiles)
            drop_left = (N1.price - LS.price) / N1.price
            drop_right = (N2.price - RS.price) / N2.price
            drop_diff = abs(drop_left - drop_right)
            head_rise = (head_height + h_low) / h_low - 1.0
            rise_max = max(N1.price / LS.price - 1.0, N2.price / RS.price - 1.0)
            prominence = head_rise / rise_max if rise_max > 0 else None
            if score_method == "template":
                scores = calculate_template_score(
                    drop_diff, max_drop_diff, prominence if prominence is not None else 0.0, head_rise,
                    tilt_of_head, max_tilt, breakout, distance, ideal_prom, ideal_rise, dist_range)
            else:
                scores = calculate_pattern_score(
                    shoulder_diff, score_shoulder_limit, head_depth, angle, max_angle,
                    breakout, distance, ideal_depth, dist_range)
            # D22 targets (display only): breakout level = neckline at the breakout bar, else at the
            # last bar (estimate); TP1 = level + head height, TP2 = level * (1 + head_rise)
            breakout_level = float(neck.price_at(breakout_i)) if breakout else current_neck

            volume_ratio = None
            avg_vol = None
            if breakout:
                av = avg_prior[breakout_i]
                if math.isfinite(av):
                    avg_vol = float(av)
                vr = vol_ratio_all[breakout_i]
                if math.isfinite(vr):
                    volume_ratio = float(vr)

            # earliest bar at which this signal is knowable live. Confirmed RS: a breakout can happen
            # inside the RS confirmation window (RS.index < b < RS.index+N), but it can only be
            # reported once RS is confirmed. Forming RS (D22): knowable once both the RS bar has
            # closed and N2 is confirmed.
            known = max(RS.index, N2.confirm_index) if forming else RS.confirm_index
            signal_i = max(known, breakout_i) if breakout else known

            ls_t, h_t, rs_t = _iso(LS.time), _iso(H.time), _iso(RS.time)
            out.append({
                "symbol": symbol,
                "timeframe": timeframe,
                "pattern_type": PATTERN_TYPE,
                "profile": profile,
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
                "RS_confirm_time": None if forming else times[RS.confirm_index],
                "shoulder_diff": shoulder_diff,
                "head_depth": head_depth,
                "neckline_angle": angle,
                "neckline_slope_pct_per_bar": neck.slope_pct_per_bar,
                "time_symmetry_ratio": max(l_to_h, r_to_h) / min(l_to_h, r_to_h),
                # D21 rule values (computed whether or not the rule is on)
                "atr_at_head": atr_h if atr_ok else None,
                "head_atr_multiple": head_gap / atr_h if atr_ok else None,
                "neckline_total_slope": total_slope,
                "shoulder_diff_of_head": sd_of_head,
                "shoulder_depth_ratio": depth_ratio,
                "pattern_start_bars_before_ls": pat_start_gap,
                # D22 template values (fractions: 0.244 = 24.4%) and display-only targets
                "drop_left_pct": drop_left,
                "drop_right_pct": drop_right,
                "shoulder_drop_diff": drop_diff,
                "head_rise_pct": head_rise,
                "head_prominence": prominence,
                "neckline_tilt_of_head": tilt_of_head,
                "neckline_max_violation": violation,
                "neckline_change": neck_change,
                "neckline_wick_of_head": wick_of_head,
                "head_height": head_height,
                "breakout_level": breakout_level,
                "tp1": breakout_level + head_height,
                "tp2": breakout_level * (1.0 + head_rise),
                "invalidation_price": h_low,
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
                "signal_time": times[signal_i],
                "as_of_time": times[last],
                "pattern_id": f"{symbol}|{timeframe}|{ls_t}|{h_t}|{rs_t}",
            })
    return out


def detect_inverse_head_shoulders(df: pd.DataFrame, timeframe: str,
                                  config: Mapping[str, Any] | None = None,
                                  symbol: str | None = None) -> list[dict[str, Any]]:
    """Detect IHS patterns as of the last bar of df.

    config: partial overrides are fine, merged onto the profile's config (D22: {"profile": "classic"}
            selects the D19/D21 rule set; default "template").
    mode: enable_breakout_filter=False -> "candidate" (forming + watchlist + confirmed),
          True -> "breakout" (only patterns with a detected breakout).
    status: "forming" (template only: RS = lowest low since N2, not yet a confirmed pivot; changes with
            every new bar), "watchlist", "confirmed", "stale" (only with include_stale).
    Returns de-duplicated (D11) results sorted by spec step 11 order.
    """
    candidates = find_pattern_candidates(df, timeframe, config, symbol)
    return sorted(deduplicate_patterns(candidates), key=result_sort_key)
