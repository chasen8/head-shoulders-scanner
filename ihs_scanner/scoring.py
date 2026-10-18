"""Pattern score (spec step 9, charter D10) and result ordering (spec step 11)."""
from __future__ import annotations

import math
from typing import Any, Mapping

TIMEFRAME_RANK = {"1w": 0, "1d": 1, "4h": 2}  # 1w > 1d > 4h


def calculate_pattern_score(shoulder_diff: float,
                            max_allowed_shoulder_diff: float,
                            head_depth: float,
                            neckline_angle: float,
                            max_neckline_angle: float,
                            breakout_detected: bool,
                            distance_to_neckline: float | None,
                            ideal_head_depth: float = 0.10,
                            distance_range: float = 0.10) -> dict[str, float]:
    """Return the four component scores and their sum (unrounded).

    breakout_score: 20 if a breakout has been detected (breakout mode, and per D10
    also candidate mode); otherwise max(0, 1 - distance / 0.10) * 20.
    """
    shoulder_score = max(0.0, 1.0 - shoulder_diff / max_allowed_shoulder_diff) * 30.0
    head_depth_score = min(head_depth / ideal_head_depth, 1.0) * 25.0
    head_depth_score = max(0.0, head_depth_score)
    neckline_score = max(0.0, 1.0 - abs(neckline_angle) / max_neckline_angle) * 25.0
    if breakout_detected:
        breakout_score = 20.0
    elif distance_to_neckline is None or not math.isfinite(distance_to_neckline):
        breakout_score = 0.0
    else:
        breakout_score = max(0.0, 1.0 - distance_to_neckline / distance_range) * 20.0
    total = shoulder_score + head_depth_score + neckline_score + breakout_score
    return {
        "shoulder_score": shoulder_score,
        "head_depth_score": head_depth_score,
        "neckline_score": neckline_score,
        "breakout_score": breakout_score,
        "pattern_score": total,
    }


def result_sort_key(r: Mapping[str, Any]):
    """score desc -> breakout first -> distance asc -> volume_ratio desc -> 1w>1d>4h.

    Trailing symbol / RS_time keys only make the order fully deterministic.
    None / NaN distance sorts last; None volume_ratio sorts after any number.
    """
    dist = r.get("distance_to_neckline")
    if dist is None or not math.isfinite(float(dist)):
        dist = math.inf
    vr = r.get("volume_ratio")
    vr_key = -float(vr) if vr is not None and math.isfinite(float(vr)) else math.inf
    return (
        -float(r.get("pattern_score") or 0.0),
        0 if r.get("breakout_detected") else 1,
        float(dist),
        vr_key,
        TIMEFRAME_RANK.get(r.get("timeframe"), 99),
        str(r.get("symbol") or ""),
        str(r.get("RS_time") or ""),
    )
