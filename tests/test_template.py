"""D23 template profile: config / profile switching, forming right shoulder (status + no repaint),
rules T1-T10, TP fields and the template score. No network (golden examples: test_golden_examples.py).
"""
import math
import time

import numpy as np
import pytest

from ihs_scanner.config import (CLASSIC_CONFIG, DEFAULT_CONFIG, TEMPLATE_CONFIG, TEMPLATE_OVERRIDES,
                                merge_config)
from ihs_scanner.detector import detect_inverse_head_shoulders, find_pattern_candidates
from ihs_scanner.scanner import OUTPUT_FIELDS
from ihs_scanner.scoring import calculate_template_score

from .synth import make_df, random_walk_df

N = 5  # template 4h pivot window
# wide symmetric pattern: LS 20, N1 40, H 60, N2 80, RS 100 (lows; highs = low + 1 -> neckline 111 flat)
WIDE = [(0, 125), (20, 100), (40, 110), (60, 90), (80, 110), (100, 100), (119, 106)]
# same, the RS still falling at the last bar (index 100): RS forming
WIDE_FORMING = WIDE[:-1]
D23_FIELDS = ("drop_left_pct", "drop_right_pct", "shoulder_drop_diff", "head_rise_pct", "head_prominence",
              "neckline_tilt_of_head", "neckline_max_violation", "neckline_change", "neckline_wick_of_head",
              "head_height", "breakout_level", "tp1", "tp2", "invalidation_price", "profile")


def _detect(df, tf="4h", **overrides):
    return detect_inverse_head_shoulders(df, tf, {"include_stale": True, **overrides}, symbol="T")


def _cands(df, tf="4h", **overrides):
    return find_pattern_candidates(df, tf, {"include_stale": True, **overrides}, symbol="T")


# ---------- config / profiles ----------
def test_default_profile_is_template():
    assert DEFAULT_CONFIG is TEMPLATE_CONFIG and DEFAULT_CONFIG["profile"] == "template"
    assert merge_config()["profile"] == "template"
    assert merge_config({"profile": "classic"}) == CLASSIC_CONFIG
    tpl = merge_config({"profile": "template"})
    for key, value in TEMPLATE_OVERRIDES.items():
        assert tpl[key] == value
    for key in CLASSIC_CONFIG:  # every other key is shared with classic
        if key not in TEMPLATE_OVERRIDES:
            assert tpl[key] == CLASSIC_CONFIG[key], key
    # re-merging a merged config is a no-op (detector re-merges what scanner merged)
    assert merge_config(merge_config({"profile": "classic", "min_head_atr": 2.0})) == merge_config(
        {"profile": "classic", "min_head_atr": 2.0})
    assert merge_config(tpl) == tpl
    with pytest.raises(ValueError):
        merge_config({"profile": "nope"})


def test_template_values():
    t = TEMPLATE_CONFIG
    assert t["shoulder_diff_limit_by_timeframe"] is None
    assert t["enable_balance_rule"] is False and t["max_time_symmetry_ratio"] is None
    assert t["enable_pattern_start_rule"] is False and t["max_neckline_total_slope"] is None
    assert t["max_pattern_bars_by_timeframe"] == {"4h": 1000, "1d": 600, "1w": 200}
    assert t["min_pattern_bars"] == {"4h": 60, "1d": 40, "1w": 16}
    assert t["enable_forming_rs"] is True and t["score_method"] == "template"
    assert CLASSIC_CONFIG["enable_forming_rs"] is False and CLASSIC_CONFIG["score_method"] == "spec"
    for f in D23_FIELDS:
        assert f in OUTPUT_FIELDS


# ---------- confirmed RS pattern (watchlist) + output values ----------
def test_wide_pattern_watchlist_values():
    df = make_df(WIDE)
    ts = df["timestamp"]
    res = _detect(df)
    assert len(res) == 1
    r = res[0]
    assert r["profile"] == "template" and r["status"] == "watchlist"
    assert (r["LS_time"], r["N1_time"], r["H_time"], r["N2_time"], r["RS_time"]) == (
        ts[20], ts[40], ts[60], ts[80], ts[100])
    assert r["RS_confirm_time"] == ts[105] and r["signal_time"] == ts[105]
    assert r["drop_left_pct"] == pytest.approx(11 / 111) and r["drop_right_pct"] == pytest.approx(11 / 111)
    assert r["shoulder_drop_diff"] == pytest.approx(0.0, abs=1e-12)
    assert r["head_rise_pct"] == pytest.approx(111 / 90 - 1)
    assert r["head_prominence"] == pytest.approx((111 / 90 - 1) / (111 / 100 - 1))
    assert r["head_height"] == pytest.approx(21.0)
    assert r["neckline_tilt_of_head"] == 0.0 and r["neckline_change"] == 0.0
    assert r["invalidation_price"] == pytest.approx(90.0)
    # not broken out: breakout level = neckline at the last bar (flat -> 111)
    assert r["breakout_level"] == pytest.approx(111.0)
    assert r["tp1"] == pytest.approx(111.0 + 21.0)
    assert r["tp2"] == pytest.approx(111.0 * (111 / 90))


def test_tp_uses_neckline_at_breakout_bar():
    # rising neckline 111 -> 113.2 (N2 +2%) and a breakout after RS
    wp = [(0, 125), (20, 100), (40, 110), (60, 90), (80, 112.2), (100, 101.8), (120, 118)]
    df = make_df(wp)
    res = _detect(df)
    assert len(res) == 1 and res[0]["breakout_detected"]
    r = res[0]
    ts = df["timestamp"]
    b = int(np.flatnonzero(ts == r["breakout_time"])[0])
    neck_b = 111 + (113.2 - 111) * (b - 40) / 40
    neck_h = 111 + (113.2 - 111) * (60 - 40) / 40
    assert r["breakout_level"] == pytest.approx(neck_b)
    assert r["head_height"] == pytest.approx(neck_h - 90)
    assert r["tp1"] == pytest.approx(neck_b + neck_h - 90)
    assert r["tp2"] == pytest.approx(neck_b * (neck_h / 90))


# ---------- T6 forming right shoulder ----------
def test_forming_rs_reported_with_forming_status():
    df = make_df(WIDE_FORMING)  # last bar = 100 = the lowest low since N2
    ts = df["timestamp"]
    res = _detect(df)
    assert len(res) == 1
    r = res[0]
    assert r["status"] == "forming" and r["RS_time"] == ts[100] and r["RS_confirm_time"] is None
    assert r["signal_time"] == ts[100]  # max(RS bar, N2 confirmation 85)
    assert r["bars_since_rs"] == 0
    # classic has no forming state: nothing until the RS pivot is confirmed
    assert _detect(df, profile="classic") == []
    assert _detect(df, enable_forming_rs=False) == []


def test_forming_becomes_watchlist_and_never_repaints():
    """At every t: only bars <= t are referenced; a forming RS lies inside the unconfirmed window
    (index > t - N); from t = RS + N the same pattern (same id) is a watchlist pattern."""
    df = make_df(WIDE, noise=0.05, seed=4)
    ts = df["timestamp"]
    seen_forming_at_100 = []
    for t in range(85, len(df)):
        res = _detect(df.iloc[:t + 1])
        now = ts[t]
        for r in res:
            for k in ("LS_time", "N1_time", "H_time", "N2_time", "RS_time", "signal_time"):
                assert r[k] <= now, (t, k)
            rs_i = int(np.flatnonzero(ts == r["RS_time"])[0])
            if r["status"] == "forming":
                assert rs_i > t - N and r["RS_confirm_time"] is None
            else:
                assert rs_i + N <= t and r["RS_confirm_time"] == ts[rs_i + N]
        target = [r for r in res if r["LS_time"] == ts[20] and r["H_time"] == ts[60]]
        if 100 <= t < 100 + N:
            assert len(target) == 1 and target[0]["status"] == "forming", t
            assert target[0]["RS_time"] == ts[100]
            seen_forming_at_100.append(target[0]["pattern_id"])
        elif t >= 100 + N:
            assert len(target) == 1 and target[0]["status"] == "watchlist", t
            assert target[0]["pattern_id"] == seen_forming_at_100[0]  # same pattern, now confirmed
        elif target:  # before bar 100 the provisional RS is the current (falling) bar
            assert target[0]["status"] == "forming" and target[0]["RS_time"] == now
    assert len(set(seen_forming_at_100)) == 1 and len(seen_forming_at_100) == N


@pytest.mark.parametrize("t", [97, 100, 102, 104, 105, 110])
def test_template_results_independent_of_future_data(t):
    df = make_df(WIDE, noise=0.05, seed=6)
    before = _detect(df.iloc[:t + 1])
    mutated = df.copy()
    for col in ("open", "high", "low", "close"):
        mutated.loc[t + 1:, col] = mutated.loc[t + 1:, col] * 0.5
    mutated.loc[t + 1:, "volume"] = 1e9
    assert _detect(mutated.iloc[:t + 1]) == before
    assert before  # forming (t < 105) or watchlist


def test_forming_invalidated_by_low_below_head():
    df = make_df(WIDE_FORMING + [(103, 89)])  # falls through H.low (90) after the RS area
    assert [r for r in _detect(df) if r["H_time"] == df["timestamp"][60]] == []


def test_random_walk_template_invariants():
    df = random_walk_df(500, seed=13)
    ts = df["timestamp"]
    for t in range(150, len(df), 9):
        for r in _detect(df.iloc[:t + 1]):
            assert r["signal_time"] <= ts[t] and r["RS_time"] <= ts[t]
            rs_i = int(np.flatnonzero(ts == r["RS_time"])[0])
            assert (rs_i > t - N) == (r["status"] == "forming")


# ---------- rules: boundary of every D23 threshold ----------
# neckline 113 -> 109 (falling), H 90, shoulders 100 / 98.5
SLOPED = [(0, 125), (20, 100), (40, 112), (60, 90), (80, 108), (100, 98.5), (119, 104)]


@pytest.mark.parametrize("key,field,kind", [
    ("min_head_prominence", "head_prominence", "min"),
    ("max_shoulder_drop_diff", "shoulder_drop_diff", "max"),
    ("max_neckline_tilt_of_head", "neckline_tilt_of_head", "max"),
    ("max_neckline_violation", "neckline_max_violation", "max"),
    ("max_neckline_rise", "neckline_change", "max"),
    ("max_neckline_wick_of_head", "neckline_wick_of_head", "max"),
    ("min_head_rise_by_timeframe", "head_rise_pct", "min"),
    ("min_shoulder_drop_by_timeframe", None, "min"),
])
def test_rule_boundaries(key, field, kind):
    df = make_df(SLOPED, noise=0.05, seed=2)
    off = {key: None}
    res = _detect(df, **off)
    assert len(res) == 1
    r = res[0]
    value = min(r["drop_left_pct"], r["drop_right_pct"]) if field is None else r[field]
    eps = 1e-9 * max(1.0, abs(value))
    edge, beyond = (value, value + eps) if kind == "min" else (value, value - eps)
    wrap = (lambda v: {"4h": v, "1d": v, "1w": v}) if key.endswith("_by_timeframe") else (lambda v: v)
    assert len(_detect(df, **{key: wrap(edge)})) == 1
    assert _detect(df, **{key: wrap(beyond)}) == []


def test_drop_symmetry_relative_rule():
    # RS 95: drops 0.099 / 0.144 -> absolute diff 0.045 passes 0.05, relative 0.31 > 0.25 rejects
    wp = [(0, 125), (20, 100), (40, 110), (60, 90), (80, 110), (100, 95), (119, 106)]
    df = make_df(wp)
    assert _detect(df) == []
    res = _detect(df, max_shoulder_drop_rel_diff=None)
    assert len(res) == 1 and res[0]["shoulder_drop_diff"] == pytest.approx(16 / 111 - 11 / 111)


def test_t4_neckline_poke_rejected():
    df = make_df(WIDE)
    df.loc[70:71, "high"] = 114.0  # equal highs -> not a strict pivot, N2 stays at 80; 2.7% above the neckline
    assert _detect(df) == []
    res = _detect(df, max_neckline_violation=None)
    assert len(res) == 1 and res[0]["neckline_max_violation"] == pytest.approx(114 / 111 - 1)


def test_t7_ls_must_start_the_rally_to_n1():
    # LS 100 at 20, a lower swing high at 27, a higher dip at 32 before N1 at 40
    wp = [(0, 125), (20, 100), (27, 105), (32, 101.5), (40, 110), (60, 90), (80, 110), (100, 100.5),
          (119, 106)]
    df = make_df(wp)
    ts = df["timestamp"]
    on = _cands(df)
    assert on and all(r["LS_time"] == ts[32] for r in on)
    off = _cands(df, require_n1_first_high_after_ls=False)
    assert any(r["LS_time"] == ts[20] for r in off)


def test_t7_ls_must_be_lowest_before_n1():
    # swing lows at 20 (101) and 32 (100) with no swing high between: only 32 is the bottom of the leg
    wp = [(0, 125), (20, 101), (25, 102.5), (32, 100), (40, 110), (60, 90), (80, 110), (100, 100),
          (119, 106)]
    df = make_df(wp)
    ts = df["timestamp"]
    assert {r["LS_time"] for r in _cands(df)} == {ts[32]}
    assert ts[20] in {r["LS_time"] for r in _cands(df, require_n1_first_high_after_ls=False)}


@pytest.mark.parametrize("dip_at,expected", [(8, 0), (0, 1)])
def test_t8_head_lead_in(dip_at, expected):
    # a low of 88 (< H 90) dip_at bars into the series; lead-in = ceil(0.5 * (60 - 20)) = 20 bars before LS
    if dip_at:
        wp = [(0, 120), (dip_at, 88), (14, 104)] + WIDE[1:]
    else:
        wp = WIDE
    df = make_df(wp)
    assert len(_detect(df)) == expected
    assert len(_detect(df, head_lead_in=None)) == 1


def test_t9_rising_neckline():
    # N2 high 124.3 = N1 x 1.12; RS keeps the drop symmetric
    wp = [(0, 125), (20, 100), (40, 110), (60, 90), (80, 123.3), (100, 124.3 * (1 - 11 / 111)), (119, 120)]
    df = make_df(wp)
    assert _detect(df) == []
    res = _detect(df, max_neckline_rise=None)
    assert len(res) == 1 and res[0]["neckline_change"] == pytest.approx(124.3 / 111 - 1)


def test_t10_wick_spike_neckline_highs():
    df = make_df(WIDE)
    df.loc[[40, 80], "high"] = 121.0  # lone wicks: body tops 110.5 -> wick 10.5, head height 31
    assert _detect(df) == []
    res = _detect(df, max_neckline_wick_of_head=None)
    assert len(res) == 1 and res[0]["neckline_wick_of_head"] == pytest.approx(10.5 / 31)


def test_min_width_per_timeframe():
    df = make_df(WIDE)  # LS->RS = 80 bars
    assert len(_detect(df, min_pattern_bars={"4h": 80, "1d": 40, "1w": 16})) == 1
    assert _detect(df, min_pattern_bars={"4h": 81, "1d": 40, "1w": 16}) == []
    assert _detect(df, min_pattern_bars=81) == []  # a plain int still works


# ---------- score ----------
def test_template_score_by_hand():
    s = calculate_template_score(shoulder_drop_diff=0.02, max_shoulder_drop_diff=0.05, head_prominence=1.25,
                                 head_rise=0.30, neckline_tilt_of_head=0.30, max_neckline_tilt_of_head=0.75,
                                 breakout_detected=False, distance_to_neckline=0.15)
    assert s["shoulder_score"] == pytest.approx(30 * 0.6)
    assert s["head_depth_score"] == pytest.approx(12.5 * 0.5 + 12.5 * 0.75)
    assert s["neckline_score"] == pytest.approx(25 * 0.6)
    assert s["breakout_score"] == pytest.approx(20 * 0.5)
    assert s["pattern_score"] == pytest.approx(18 + 15.625 + 15 + 10)
    top = calculate_template_score(0.0, 0.05, 3.0, 1.0, 0.0, 0.75, True, 0.5)
    assert top["pattern_score"] == pytest.approx(100.0)
    low = calculate_template_score(0.08, 0.05, 0.9, -0.1, 1.0, 0.75, False, None)
    assert low["pattern_score"] == 0.0


def test_score_method_switch():
    df = make_df(WIDE)
    tpl = _detect(df)[0]
    spec = _detect(df, score_method="spec")[0]
    assert tpl["pattern_score"] != spec["pattern_score"]
    with pytest.raises(ValueError):
        _detect(df, score_method="other")


# ---------- performance ----------
def test_template_speed_1000_bars():
    df = random_walk_df(1000, seed=21)
    detect_inverse_head_shoulders(df, "4h", None, symbol="RW")  # warm-up
    t0 = time.perf_counter()
    res = detect_inverse_head_shoulders(df, "4h", {"include_stale": True}, symbol="RW")
    dt = time.perf_counter() - t0
    print(f"\n1000-bar random walk (template): {len(res)} patterns in {dt * 1000:.1f} ms")
    assert dt < 1.0  # target 0.2 s on the dev box; generous for CI
    assert all(math.isfinite(r["pattern_score"]) for r in res)
