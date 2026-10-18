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
    "pivot_window_by_timeframe": {"4h": 3, "1d": 3, "1w": 2},
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
}


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
