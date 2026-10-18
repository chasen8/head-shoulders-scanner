"""Default configuration and helpers for the IHS scanner.

Keys follow docs/spec.md step 12, plus the charter additions (D6, D9, ...).
Per-timeframe values are dicts keyed by "4h" / "1d" / "1w".
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

TIMEFRAMES = ("4h", "1d", "1w")

DEFAULT_CONFIG: dict[str, Any] = {
    # --- spec step 12 ---
    # D19: upper end of the spec's suggested range (4h/1d 3-5, 1w 2-3) to cut noisy pivots
    "pivot_window_by_timeframe": {"4h": 5, "1d": 5, "1w": 3},
    "shoulder_diff_limit_by_timeframe": {"4h": 0.05, "1d": 0.10, "1w": 0.10},
    "min_head_depth_by_timeframe": {"4h": 0.03, "1d": 0.05, "1w": 0.05},
    "max_neckline_angle": 30.0,
    "angle_scale": 100.0,
    "breakout_buffer_by_timeframe": {"4h": 0.005, "1d": 0.01, "1w": 0.01},
    "enable_breakout_filter": False,  # False = candidate mode, True = breakout mode
    "enable_volume_filter": False,
    "min_volume_ratio": 1.2,
    "min_pattern_score": 0.0,
    # --- charter additions ---
    "max_pattern_bars_by_timeframe": {"4h": 150, "1d": 120, "1w": 80},  # D6: LS->RS length
    # D9/D15: freshness windows in bars (int also accepted). 4h 30 bars = 5 days, 1d 20 = 4 weeks,
    # 1w 8 = 2 months; breakout age counted from the FIRST price cross (D14, no reset).
    "max_bars_after_rs": {"4h": 30, "1d": 20, "1w": 8},
    "max_breakout_age": {"4h": 6, "1d": 5, "1w": 3},
    "include_stale": False,  # D9
    "ideal_head_depth": 0.10,  # spec step 9
    "volume_avg_window": 20,  # D8: average of the N bars BEFORE the breakout bar
    "breakout_distance_range": 0.10,  # spec step 9: distance / 0.10
    # --- D19: extra shape rules (neurotrader888/TechnicalAnalysisAutomation check_ihs_pattern, MIT) ---
    # balance: reject if LS.low > (RS.low + N2.high)/2 or RS.low > (LS.low + N1.high)/2
    "enable_balance_rule": True,
    # symmetry: reject if one side (LS->H or H->RS, in bars) is > ratio x the other; None = off
    "max_time_symmetry_ratio": 2.5,
    # --- D19 optional, off by default (for later backtest comparison) ---
    "min_pattern_bars": None,  # minimum LS->RS length in bars; None = off
    # prior downtrend (concept from yanivil/SignalSync, MIT): highest close in the
    # prior_trend_bars bars before LS must be >= LS.low + head height, where
    # head height = neckline_price_at(H.index) - H.low
    "require_prior_downtrend": False,
    "prior_trend_bars": {"4h": 60, "1d": 50, "1w": 20},
    # --- D21: five more shape rules (docs/pattern-rules-research.md section 2, priorities 1-5) ---
    # Notation: neck(i) = neckline_price_at(i) through N1.high / N2.high;
    # head_height = neck(H.index) - H.low.  Every rule is checked independently; None / False = off.
    # scan.py --rules-off R1,R11,R3,R2,R21 switches them off for comparisons.
    #
    # R1 head depth in ATR units (yanivil/SignalSync scan.py L151 IHS_MIN_HEAD_ATR, L1436, MIT):
    #   accept iff H.low <= min(LS.low, RS.low) - min_head_atr * ATR[H.index]
    #   (SignalSync uses strict "<" and a simple-mean ATR; D21 uses "<=" and Wilder ATR(atr_length):
    #   TR[0] = high-low, TR[i] = max(high-low, |high-prev close|, |low-prev close|),
    #   ATR[length-1] = mean(TR[0:length]), ATR[i] = (ATR[i-1]*(length-1) + TR[i]) / length.
    #   ATR[H.index] only uses bars <= H.index (no look-ahead). If ATR[H.index] is NaN
    #   (H.index < length-1) the rule is skipped, as the research report suggests.)
    "min_head_atr": 1.0,
    "atr_length": 14,
    # R11 pattern start (neurotrader888/TechnicalAnalysisAutomation head_shoulders.py check_ihs_pattern
    #   L227-244, MIT): walking left from LS, some bar j = LS.index-1 ... LS.index-L must have
    #   price[j] > neck(j) (the neckline extended to the left). L = pattern_start_lookback, or when None
    #   the original's range(1, head_width) with head_width = N2.index - N1.index, i.e. L = width - 1.
    #   As in the original, running out of data (j < 0) before a crossing is found rejects the pattern.
    #   price = close by default: the original runs on a single (log) close series; "high" is an option.
    #   Overlap: optional require_prior_downtrend (D19) asks "did price fall >= head_height into LS";
    #   R11 asks "was price above the extended neckline shortly before LS" -- related, kept both.
    "enable_pattern_start_rule": True,
    "pattern_start_lookback": None,
    "pattern_start_price": "high",  # D21: high, consistent with the high-based neckline (D5); original uses close
    # R3 total neckline tilt (SignalSync scan.py L156 IHS_MAX_NECK_SLOPE, L1450-1456, MIT):
    #   accept iff |slope * (RS.index - LS.index)| / close[H.index] <= max_neckline_total_slope,
    #   slope = (N2.high - N1.high) / (N2.index - N1.index) in price per bar (SignalSync width = rs - ls).
    #   Runs together with the +-30 deg max_neckline_angle rule.
    "max_neckline_total_slope": 0.15,
    # R2 shoulder difference relative to head height (SignalSync scan.py L153
    #   IHS_SHOULDER_SYM_OF_HEIGHT = 0.30, L1457-1459, MIT):
    #   accept iff |LS.low - RS.low| <= max_shoulder_diff_of_head * head_height.
    #   D21 default 0.5 (looser than SignalSync's US-daily 0.30). Runs together with the spec's
    #   percentage shoulder_diff_limit_by_timeframe.
    "max_shoulder_diff_of_head": 0.5,
    # R21 shoulder depth / head height band (Savin, Weller & Zvingelis, "The Predictive Power of
    #   Head-and-Shoulders Price Patterns in the U.S. Stock Market", sec. B rules R6/R7, mirrored for IHS;
    #   definition as written in docs/pattern-rules-research.md R21):
    #   shoulder_depth_ratio = mean(N1.high - LS.low, N2.high - RS.low) / head_height
    #   accept iff ratio >= min_shoulder_depth_ratio (R7, 0.25) and, when set, ratio <= max (R6, 0.7
    #   in the paper; the inequality direction was lost in the PDF text, so the upper bound is off).
    "min_shoulder_depth_ratio": 0.25,
    "max_shoulder_depth_ratio": None,
}

# D21: rule id -> config overrides that switch the rule off (scan.py --rules-off).
RULE_OFF_OVERRIDES: dict[str, dict[str, Any]] = {
    "R1": {"min_head_atr": None},
    "R11": {"enable_pattern_start_rule": False},
    "R3": {"max_neckline_total_slope": None},
    "R2": {"max_shoulder_diff_of_head": None},
    "R21": {"min_shoulder_depth_ratio": None, "max_shoulder_depth_ratio": None},
}


def rules_off_overrides(rule_ids) -> dict[str, Any]:
    """Merge RULE_OFF_OVERRIDES for the given ids ("R1", "r11", ...); unknown ids raise KeyError."""
    out: dict[str, Any] = {}
    for rid in rule_ids:
        key = str(rid).strip().upper()
        if not key:
            continue
        if key not in RULE_OFF_OVERRIDES:
            raise KeyError(f"unknown rule id {rid!r}; expected one of {sorted(RULE_OFF_OVERRIDES)}")
        out.update(RULE_OFF_OVERRIDES[key])
    return out


def merge_config(overrides: Mapping[str, Any] | None = None,
                 base: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Return a new config = base (default DEFAULT_CONFIG) updated with overrides.

    Dict-valued keys are merged one level deep, so {"pivot_window_by_timeframe": {"4h": 5}}
    only changes 4h. Unknown keys raise KeyError (catches typos).
    """
    cfg = deepcopy(dict(base) if base is not None else DEFAULT_CONFIG)
    if not overrides:
        return cfg
    for key, value in overrides.items():
        if key not in cfg:
            raise KeyError(f"unknown config key: {key!r}")
        if isinstance(cfg[key], dict) and isinstance(value, Mapping):
            merged = dict(cfg[key])
            merged.update(deepcopy(dict(value)))
            cfg[key] = merged
        else:
            cfg[key] = deepcopy(value)
    return cfg


def tf_value(config: Mapping[str, Any], key: str, timeframe: str) -> Any:
    """Read a value that may be a scalar or a per-timeframe dict."""
    value = config[key]
    if isinstance(value, Mapping):
        if timeframe not in value:
            raise ValueError(f"config[{key!r}] has no value for timeframe {timeframe!r}")
        return value[timeframe]
    return value
