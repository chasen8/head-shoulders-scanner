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


def calculate_template_score(shoulder_drop_diff: float,
                             max_shoulder_drop_diff: float | None,
                             head_prominence: float,
                             head_rise: float,
                             neckline_tilt_of_head: float,
                             max_neckline_tilt_of_head: float | None,
                             breakout_detected: bool,
                             distance_to_neckline: float | None,
                             ideal_head_prominence: float = 1.5,
                             ideal_head_rise: float = 0.40,
                             distance_range: float = 0.30) -> dict[str, float]:
    """D23 template score (0-100), same four component names as the spec score:

    shoulder_score   (30) = 30 * max(0, 1 - |drop_L - drop_R| / max_shoulder_drop_diff)
                            (max None -> 0.05)
    head_depth_score (25) = 12.5 * clip((head_prominence - 1) / (ideal_head_prominence - 1), 0, 1)
                          + 12.5 * clip(head_rise / ideal_head_rise, 0, 1)
    neckline_score   (25) = 25 * max(0, 1 - neckline_tilt_of_head / max_neckline_tilt_of_head)
                            (max None -> 1.0)
    breakout_score   (20) = 20 if broken out, else 20 * max(0, 1 - distance_to_neckline / distance_range)
    """
    def clip01(x: float) -> float:
        return min(1.0, max(0.0, x))

    sd_max = 0.05 if max_shoulder_drop_diff is None else float(max_shoulder_drop_diff)
    tilt_max = 1.0 if max_neckline_tilt_of_head is None else float(max_neckline_tilt_of_head)
    shoulder_score = 30.0 * max(0.0, 1.0 - shoulder_drop_diff / sd_max) if sd_max > 0 else 0.0
    prom_span = ideal_head_prominence - 1.0
    head_depth_score = (12.5 * (clip01((head_prominence - 1.0) / prom_span) if prom_span > 0 else 1.0)
                        + 12.5 * clip01(head_rise / ideal_head_rise if ideal_head_rise > 0 else 1.0))
    neckline_score = 25.0 * max(0.0, 1.0 - neckline_tilt_of_head / tilt_max) if tilt_max > 0 else 0.0
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
