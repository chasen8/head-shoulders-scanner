"""D26 shape validation V1-V4 (formulas and sources: config.py, "D26" block).

Pure functions on numpy arrays; the detector calls them once per candidate that survived the D19-D23
rules. Every value depends only on bars <= the last bar the detector was given (no look-ahead):
V1/V2/V3a/V4 read bars LS.index .. RS.index (V2 up to the last bar for a forming RS), V3b/V3c read the
last bar. Notation: z = natural log of a price; head_height_log = log(neck(H.index)) - log(H.low).
"""
from __future__ import annotations

import math
from typing import Any, Mapping, Sequence

import numpy as np

from .templates import TEMPLATES

# shape_score reference values when the V2 / V4 thresholds are switched off (classic profile)
SHAPE_SCORE_REF_SWING = 0.65
SHAPE_SCORE_REF_DISTANCE = 0.25


def _finite(x: float | None) -> bool:
    return x is not None and math.isfinite(float(x))


def counter_swing(z_low: np.ndarray, z_high: np.ndarray, s: int, e: int, leg: str) -> float:
    """Largest move against the leg's direction strictly inside bars s..e (log units, >= 0).

    leg "down" (N1->H, N2->RS): max over s < i < j < e of z_high[j] - z_low[i]  (a rebound: a low, then a
    later higher high). leg "up" (LS->N1, H->N2): max over s < i < j < e of z_high[i] - z_low[j] (a pullback).
    The two end bars are excluded (they ARE the leg's extremes; their own wick ranges are not a swing) and
    i < j strictly (the order of high and low inside one bar is unknown). Fewer than 2 inner bars -> 0."""
    a, b = s + 1, e  # inner bars a .. b-1
    if b - a < 2:
        return 0.0
    lo = z_low[a:b]
    hi = z_high[a:b]
    if leg == "down":
        run_min = np.minimum.accumulate(lo)[:-1]  # lowest low strictly before j
        best = float(np.max(hi[1:] - run_min))
    elif leg == "up":
        run_max = np.maximum.accumulate(hi)[:-1]  # highest high strictly before j
        best = float(np.max(run_max - lo[1:]))
    else:
        raise ValueError(f"leg must be 'up' or 'down', got {leg!r}")
    return max(0.0, best)


def max_counter_swing(z_low: np.ndarray, z_high: np.ndarray, idx: Sequence[int]) -> tuple[float, str]:
    """V1: the largest counter swing over the four legs LS->N1 (up), N1->H (down), H->N2 (up),
    N2->RS (down). idx = (LS, N1, H, N2, RS) bar indices. Returns (value in log units, leg name)."""
    ls, n1, h, n2, rs = (int(i) for i in idx)
    legs = (("LS-N1", ls, n1, "up"), ("N1-H", n1, h, "down"), ("H-N2", h, n2, "up"), ("N2-RS", n2, rs, "down"))
    best, where = 0.0, legs[0][0]
    for name, s, e, kind in legs:
        v = counter_swing(z_low, z_high, s, e, kind)
        if v > best:
            best, where = v, name
    return best, where


def skeleton_fit(z_mid: np.ndarray, idx: Sequence[int], end: int, head_height_log: float) -> tuple[float, float]:
    """V2: fit of the five-point polyline to z_mid = log((high+low)/2) over bars idx[0] .. end.

    Model: straight lines through (i, z_mid[i]) for i in idx (+ (end, z_mid[end]) when end > idx[-1], i.e. a
    forming RS followed by newer bars). R^2 = 1 - SS_res / SS_tot (neurotrader888 head_shoulders.py
    compute_pattern_r2, MIT; there on log close). Second value: mean |residual| / head_height_log.
    SS_tot == 0 (flat series) -> R^2 = 0."""
    knots = [int(i) for i in idx]
    if end > knots[-1]:
        knots.append(int(end))
    x = np.arange(knots[0], knots[-1] + 1)
    y = z_mid[x]
    model = np.interp(x, knots, z_mid[knots])
    res = y - model
    ss_res = float(np.dot(res, res))
    dev = y - float(np.mean(y))
    ss_tot = float(np.dot(dev, dev))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0
    mad = float(np.mean(np.abs(res))) / head_height_log if head_height_log > 0 else math.inf
    return r2, mad


def close_violation(close: np.ndarray, neck_vals: np.ndarray) -> float | None:
    """V3a: max(close[i] / neck(i) - 1) over the given bars (N1 < i < RS, i != N2); None if no bars."""
    if close.size == 0:
        return None
    return float(np.max(close / neck_vals - 1.0))


def template_coords(prices: Mapping[str, float], index: Mapping[str, int], neck_at_head: float):
    """V4 normalisation (docs/academic-ihs-research.md section 9 (c)):
    y'_k = (z_k - z_H) / (z_neck(H) - z_H) for k in LS, N1, N2, RS (z = log price; LS/H/RS lows, N1/N2 highs)
    x'_k = (t_k - t_LS) / (t_RS - t_LS) for k in N1, H, N2 (t = bar index).
    Returns (y' tuple of 4, x' tuple of 3)."""
    zh = math.log(prices["H"])
    span = math.log(neck_at_head) - zh
    y = tuple((math.log(prices[k]) - zh) / span for k in ("LS", "N1", "N2", "RS"))
    width = index["RS"] - index["LS"]
    x = tuple((index[k] - index["LS"]) / width for k in ("N1", "H", "N2"))
    return y, x


def template_distance(y: Sequence[float], x: Sequence[float], amplitude_weight: float = 0.8,
                      exclude: Sequence[str] = (),
                      templates: Mapping[str, Mapping[str, Sequence[float]]] = TEMPLATES) -> tuple[float, str | None]:
    """V4 (Fu, Chung, Luk & Ng 2007 amplitude / temporal distance, as restated in docs/academic-ihs-research.md
    5.3; weights per section 9 (c)): for each template j
        AD_j = sqrt(mean_k (y'_k - q_k)^2)  (4 amplitudes),  TD_j = sqrt(mean_k (x'_k - q^t_k)^2)  (3 times)
        D_j  = w * AD_j + (1 - w) * TD_j
    Returns (min_j D_j, name of that template); templates named in `exclude` are skipped (leave-one-out)."""
    best, name = math.inf, None
    yv, xv = np.asarray(y, dtype=float), np.asarray(x, dtype=float)
    for key, q in templates.items():
        if key in exclude:
            continue
        ad = math.sqrt(float(np.mean((yv - np.asarray(q["y"])) ** 2)))
        td = math.sqrt(float(np.mean((xv - np.asarray(q["x"])) ** 2)))
        d = amplitude_weight * ad + (1.0 - amplitude_weight) * td
        if d < best:
            best, name = d, key
    return best, name


def shape_score(swing: float | None, distance: float | None, max_swing: float | None,
                max_distance: float | None) -> float:
    """shape_score (0-100) = 100 * (0.5 * clip(1 - v1 / gamma, 0, 1) + 0.5 * clip(1 - D / theta, 0, 1)).

    v1 = v1_max_counter_swing (clean legs), D = v4_template_distance (close to an example); gamma / theta = the
    V1 / V4 thresholds (SHAPE_SCORE_REF_* when a threshold is off). 0 = at both limits; 100 = no counter swing
    at all and identical to an example. V2 (MAD) is not part of it: straighter-than-the-examples legs are not
    "more like the examples" (the examples' MAD is 0.14-0.18 of the 0.20 limit)."""
    gamma = SHAPE_SCORE_REF_SWING if max_swing is None else float(max_swing)
    theta = SHAPE_SCORE_REF_DISTANCE if max_distance is None else float(max_distance)

    def clip01(v: float) -> float:
        return min(1.0, max(0.0, v))

    clean = clip01(1.0 - swing / gamma) if _finite(swing) and gamma > 0 else 0.0
    near = clip01(1.0 - distance / theta) if _finite(distance) and theta > 0 else 0.0
    return 100.0 * (0.5 * clean + 0.5 * near)


# ---------- display: one row per check, pass/fail decided here (front end only draws it, D17) ----------
CHECKS = (
    # id, value field, config key, kind ("max" = value <= limit), plain-language description
    ("V1", "v1_max_counter_swing", "max_counter_swing_of_head", "max",
     "五點之間沒有夾著更大的轉折：每一段（左肩→頸線→頭→頸線→右肩）中途的反向波動，都小於頭高的這個比例"),
    ("V2", "v2_mad", "max_skeleton_mad_of_head", "max",
     "整段走勢貼著五點連成的骨架：價格離骨架的平均距離，小於頭高的這個比例"),
    ("V2r", "v2_r2", "min_skeleton_r2", "min",
     "骨架貼合度 R²（1＝完全貼合；參考值，你的範例只有 −0.20～0.37，所以不拿來過濾）"),
    ("V3a", "v3_close_violation", "max_neckline_close_violation", "max",
     "左頸線到右肩之間，沒有收盤站上頸線（頸線是乾淨的壓力）"),
    ("V3b", "v3_runaway", "max_runaway_of_head", "max",
     "還沒漲過頭：目前收盤高出頸線的幅度，小於頭高的這個比例（超過就標為過期）"),
    ("V3c", "v3_fallback", "max_fallback_of_head", "max",
     "突破後沒有跌回頸線下太多：跌回頸線下的幅度，小於頭高的這個比例（只看已站上過頸線的）"),
    ("V4", "v4_template_distance", "max_template_distance", "max",
     "和你的 5 個範例之一夠像（振幅 80%、時間 20% 的距離，越小越像）"),
)
SHAPE_CHECK_IDS = ("V1", "V2", "V2r", "V3a", "V4")  # shape_valid = all enabled ones pass; V3b/V3c set the status


def check_passes(value: float | None, limit: float | None, kind: str) -> bool | None:
    """None = check off (limit None); a missing / non-finite value fails an enabled check."""
    if limit is None:
        return None
    if not _finite(value):
        return False
    return bool(value <= limit) if kind == "max" else bool(value >= limit)


def shape_checks(row: Mapping[str, Any], config: Mapping[str, Any]) -> list[dict[str, Any]]:
    """[{id, field, value, limit, kind, pass, enabled, text}] for one result, decided here so the front end only
    draws it (D17). pass None = off, or (V3c) not applicable because the price never closed above the neckline."""
    out = []
    for cid, field, key, kind, text in CHECKS:
        value, limit = row.get(field), config.get(key)
        ok = check_passes(value, limit, kind)
        if cid == "V3c" and value is None:
            ok = None
        out.append({"id": cid, "field": field, "value": float(value) if _finite(value) else None,
                    "limit": None if limit is None else float(limit), "kind": kind, "pass": ok,
                    "enabled": limit is not None, "text": text})
    return out
