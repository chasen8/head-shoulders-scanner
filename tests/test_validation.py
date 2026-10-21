"""D26 shape validation V1-V4: pure functions (by hand), synthetic pass / fail per check, threshold
boundaries, status changes for V3b / V3c, no look-ahead for the new fields, config and templates."""
import math

import numpy as np
import pytest

from ihs_scanner.config import (CLASSIC_CONFIG, RULE_OFF_OVERRIDES, TEMPLATE_CONFIG, merge_config,
                                rules_off_overrides)
from ihs_scanner.detector import detect_inverse_head_shoulders
from ihs_scanner.scanner import OUTPUT_FIELDS
from ihs_scanner.templates import TEMPLATES
from ihs_scanner.validation import (CHECKS, check_passes, counter_swing, max_counter_swing, shape_checks,
                                    shape_score, skeleton_fit, template_coords, template_distance)

from .synth import make_df

# LS 20, N1 40, H 60, N2 80, RS 100 (lows; highs = low + 1 -> flat neckline 111): passes every check
WIDE = [(0, 125), (20, 100), (40, 110), (60, 90), (80, 110), (100, 100), (119, 106)]
HH_LOG = math.log(111 / 90)  # head height in log units for WIDE
D26_FIELDS = ("v1_max_counter_swing", "v1_leg", "v2_mad", "v2_r2", "v3_close_violation", "v3_runaway",
              "v3_fallback", "v4_template_distance", "v4_nearest_template", "shape_valid", "shape_score",
              "base_score", "stale_reason")
V_OFF = rules_off_overrides(["V1", "V2", "V3", "V4"])


def _detect(df, tf="4h", **overrides):
    return detect_inverse_head_shoulders(df, tf, {"include_stale": True, **overrides}, symbol="T")


# ---------- pure functions ----------
def test_counter_swing_by_hand():
    zl = np.log(np.array([10, 9, 8, 8.5, 7, 6, 5.0]))
    zh = zl + 0.01
    # down leg 0..6: inner bars 1..5; rebound = low 8 (bar 2) -> high 8.5+ (bar 3)
    assert counter_swing(zl, zh, 0, 6, "down") == pytest.approx(math.log(8.5) + 0.01 - math.log(8))
    # end bars are excluded: a rebound that needs bar 0's low or bar 6's high does not count
    zl2 = np.log(np.array([5, 9, 8, 7, 6, 5.5, 5.0]))
    assert counter_swing(zl2, zl2 + 0.01, 0, 6, "down") == pytest.approx(0.0, abs=1e-12)
    # up leg: pullback = earlier high -> later low
    up = np.log(np.array([5, 6, 7, 6.2, 8, 9, 10.0]))
    assert counter_swing(up, up + 0.01, 0, 6, "up") == pytest.approx(math.log(7) + 0.01 - math.log(6.2))
    assert counter_swing(up, up, 0, 2, "up") == 0.0  # fewer than two inner bars
    with pytest.raises(ValueError):
        counter_swing(up, up, 0, 6, "sideways")


def test_max_counter_swing_picks_the_leg():
    z = np.log(np.array([10, 12, 14, 13, 15, 12, 9, 10.5, 8, 7, 9, 11, 13, 12, 10.0]))
    v, leg = max_counter_swing(z, z, (0, 4, 9, 12, 14))
    assert leg == "N1-H" and v == pytest.approx(math.log(10.5 / 9))


def test_skeleton_fit_perfect_and_flat():
    idx = (0, 10, 20, 30, 40)
    z = np.interp(np.arange(41), idx, [0.0, 1.0, -1.0, 1.0, 0.0])
    r2, mad = skeleton_fit(z, idx, 40, 2.0)
    assert r2 == pytest.approx(1.0) and mad == pytest.approx(0.0, abs=1e-12)
    # a forming RS followed by newer bars: the model extends to the last bar
    r2b, _ = skeleton_fit(np.concatenate([z, [0.35, 0.7]]), idx, 42, 2.0)
    assert r2b == pytest.approx(1.0)
    r2c, mad_c = skeleton_fit(np.concatenate([z, [0.5, 0.7]]), idx, 42, 2.0)  # bar 41 off the line by 0.15
    assert r2c < 1.0 and mad_c == pytest.approx(0.15 / 43 / 2.0)
    flat = np.zeros(41)
    assert skeleton_fit(flat, idx, 40, 1.0) == (0.0, 0.0)  # SS_tot = 0 -> R^2 0


def test_template_coords_and_distance_by_hand():
    prices = {"LS": 100.0, "N1": 111.0, "H": 90.0, "N2": 111.0, "RS": 100.0}
    index = {"LS": 20, "N1": 40, "H": 60, "N2": 80, "RS": 100}
    y, x = template_coords(prices, index, 111.0)
    assert y == pytest.approx((math.log(100 / 90) / HH_LOG, 1.0, 1.0, math.log(100 / 90) / HH_LOG))
    assert x == pytest.approx((0.25, 0.5, 0.75))
    one = {"A": {"y": (0.5, 1.0, 1.0, 0.5), "x": (0.25, 0.5, 0.75)},
           "B": {"y": (0.5, 1.0, 1.0, 0.1), "x": (0.0, 0.5, 1.0)}}
    d, name = template_distance(y, x, 0.8, templates=one)
    assert name == "A" and d == pytest.approx(0.8 * math.sqrt(2 * (y[0] - 0.5) ** 2 / 4))
    d_b, name_b = template_distance(y, x, 0.8, exclude=("A",), templates=one)
    ad = math.sqrt(((y[0] - 0.5) ** 2 + (y[3] - 0.1) ** 2) / 4)
    td = math.sqrt((0.25 ** 2 * 2) / 3)
    assert name_b == "B" and d_b == pytest.approx(0.8 * ad + 0.2 * td)
    # every template is at distance 0 from itself
    for key, q in TEMPLATES.items():
        assert template_distance(q["y"], q["x"])[0] == pytest.approx(0.0, abs=1e-12)
        assert template_distance(q["y"], q["x"])[1] == key


def test_shape_score_by_hand():
    assert shape_score(0.0, 0.0, 0.65, 0.25) == pytest.approx(100.0)
    assert shape_score(0.65, 0.25, 0.65, 0.25) == pytest.approx(0.0)
    assert shape_score(0.325, 0.05, 0.65, 0.25) == pytest.approx(100 * (0.5 * 0.5 + 0.5 * 0.8))
    assert shape_score(2.0, 1.0, 0.65, 0.25) == 0.0  # clipped
    assert shape_score(0.325, 0.125, None, None) == pytest.approx(50.0)  # off -> reference limits 0.65 / 0.25
    assert shape_score(math.nan, None, 0.65, 0.25) == 0.0


def test_check_passes_and_shape_checks():
    assert check_passes(0.5, None, "max") is None
    assert check_passes(0.5, 0.5, "max") is True and check_passes(0.51, 0.5, "max") is False
    assert check_passes(0.5, 0.5, "min") is True and check_passes(0.49, 0.5, "min") is False
    assert check_passes(None, 0.5, "max") is False and check_passes(math.nan, 0.5, "max") is False
    row = {"v1_max_counter_swing": 0.7, "v2_mad": 0.1, "v2_r2": 0.2, "v3_close_violation": -0.01,
           "v3_runaway": -0.3, "v3_fallback": None, "v4_template_distance": 0.1}
    checks = {c["id"]: c for c in shape_checks(row, TEMPLATE_CONFIG)}
    assert [c[0] for c in CHECKS] == list(checks)
    assert checks["V1"]["pass"] is False and checks["V1"]["limit"] == 0.65
    assert checks["V2"]["pass"] is True and checks["V4"]["pass"] is True
    assert checks["V2r"]["enabled"] is False and checks["V2r"]["pass"] is None  # R^2 shown, not a filter
    assert checks["V3c"]["pass"] is None and checks["V3c"]["enabled"] is True  # never crossed: n/a
    assert all(c["pass"] is None and not c["enabled"] for c in shape_checks(row, CLASSIC_CONFIG))


# ---------- config / templates ----------
def test_config_profiles_and_rule_switches():
    t, c = TEMPLATE_CONFIG, CLASSIC_CONFIG
    assert (t["max_counter_swing_of_head"], t["max_skeleton_mad_of_head"], t["max_neckline_close_violation"],
            t["max_runaway_of_head"], t["max_fallback_of_head"], t["max_template_distance"]) == (
        0.65, 0.20, 0.005, 0.5, 0.25, 0.25)
    assert t["min_skeleton_r2"] is None and t["shape_score_weight"] == 0.0
    for key in ("max_counter_swing_of_head", "max_skeleton_mad_of_head", "min_skeleton_r2",
                "max_neckline_close_violation", "max_runaway_of_head", "max_fallback_of_head",
                "max_template_distance"):
        assert c[key] is None, key  # classic: all off
    for rid in ("V1", "V2", "V3", "V4"):
        assert rid in RULE_OFF_OVERRIDES
    off = merge_config(V_OFF)
    assert all(off[k] is None for k in RULE_OFF_OVERRIDES["V3"])
    for f in D26_FIELDS:
        assert f in OUTPUT_FIELDS
    with pytest.raises(KeyError):
        _detect(make_df(WIDE), template_exclude=["NOPE"])


def test_templates_file_is_current():
    from scripts.build_templates import OUT, build, render
    assert OUT.read_text(encoding="utf-8") == render(build()), "run scripts/build_templates.py"
    assert len(TEMPLATES) == 5


# ---------- detector: pass / fail per check on synthetic shapes ----------
def test_clean_pattern_passes_every_check():
    res = _detect(make_df(WIDE))
    assert len(res) == 1
    r = res[0]
    assert r["shape_valid"] is True and r["status"] == "watchlist" and r["stale_reason"] is None
    assert r["v1_max_counter_swing"] < 0.1 and r["v2_mad"] < 0.02 and r["v2_r2"] > 0.99
    assert r["v3_close_violation"] < 0 and r["v3_fallback"] is None
    assert r["v3_runaway"] == pytest.approx((106.5 - 111) / 21)  # last close 106.5, flat neckline 111, head 21
    assert r["v4_template_distance"] <= 0.25 and r["v4_nearest_template"] in TEMPLATES
    assert r["base_score"] == pytest.approx(r["pattern_score"])  # weight 0: pattern_score unchanged
    assert r["shape_score"] == pytest.approx(shape_score(r["v1_max_counter_swing"], r["v4_template_distance"],
                                                         0.65, 0.25), abs=0.01)
    # classic computes the values but has no shape check on
    cl = _detect(make_df(WIDE), profile="classic", max_pattern_bars_by_timeframe={"4h": 150})
    assert cl and cl[0]["shape_valid"] is None and cl[0]["v4_template_distance"] == pytest.approx(
        r["v4_template_distance"])


def test_v1_rebound_inside_n1_h_rejected():
    # N1 -> H falls to 94, rebounds to 108(+1 high), then falls to the head: rebound log(109/94) = 0.71 head heights
    wp = [(0, 125), (20, 100), (40, 110), (46, 94), (52, 108), (60, 90), (80, 110), (100, 100), (119, 106)]
    df = make_df(wp)
    assert _detect(df) == []
    r = _detect(df, max_counter_swing_of_head=None)[0]
    assert r["v1_leg"] == "N1-H" and r["v1_max_counter_swing"] == pytest.approx(math.log(109 / 94) / HH_LOG)
    # a small rebound (98 -> 104) passes
    ok = _detect(make_df([(0, 125), (20, 100), (40, 110), (46, 98), (52, 103), (60, 90), (80, 110), (100, 100),
                          (119, 106)]))
    assert len(ok) == 1 and ok[0]["v1_max_counter_swing"] == pytest.approx(math.log(104 / 98) / HH_LOG)


def test_v2_box_shaped_bottom_rejected():
    # price drops straight to just above the head and stays flat: legs far from the five-point skeleton
    wp = [(0, 125), (30, 100), (40, 110), (41, 91.5), (59, 91), (60, 90), (61, 91), (79, 91.5), (80, 110),
          (90, 100), (109, 106)]
    df = make_df(wp)
    assert _detect(df) == []
    r = _detect(df, max_skeleton_mad_of_head=None)[0]
    assert r["v2_mad"] > 0.20 and r["v1_max_counter_swing"] < 0.1 and r["shape_valid"] is True
    # R^2 is shown, and can be switched on as a filter
    assert r["v2_r2"] < 0
    assert _detect(make_df(WIDE), min_skeleton_r2=0.99)  # WIDE R^2 0.997
    assert _detect(make_df(WIDE), min_skeleton_r2=0.999) == []


def test_v3a_close_above_neckline_rejected_while_t4_passes():
    df = make_df(WIDE)
    df.loc[70:71, "high"] = 111 * 1.009  # equal highs: not a pivot, N2 stays at 80; T4 (1%) passes
    df.loc[70:71, "close"] = 111 * 1.008  # ... but the close is 0.8% above the neckline
    assert _detect(df) == []
    r = _detect(df, max_neckline_close_violation=None)[0]
    assert r["v3_close_violation"] == pytest.approx(0.008) and r["neckline_max_violation"] == pytest.approx(0.009)


def _detect_close(df, **overrides):
    return _detect(df, **{"breakout_price": "close", **overrides})


def test_v3b_runaway_turns_stale():
    wp = [(0, 125), (20, 100), (40, 110), (60, 90), (80, 110), (100, 100), (110, 112), (116, 124)]
    df = make_df(wp)
    # D36: pinned to the close breakout (a wick cross one bar earlier would make the age 7 > 6 -> "age")
    r = _detect_close(df)[0]
    assert r["status"] == "stale" and r["stale_reason"] == "runaway" and r["shape_valid"] is True
    assert r["v3_runaway"] == pytest.approx((124.5 - 111) / 21)
    assert detect_inverse_head_shoulders(df, "4h", None, symbol="T") == []  # stale is not output by default
    assert _detect_close(df, max_runaway_of_head=None)[0]["status"] == "confirmed"
    v = r["v3_runaway"]
    assert _detect_close(df, max_runaway_of_head=v)[0]["status"] == "confirmed"  # boundary: <= passes
    assert _detect_close(df, max_runaway_of_head=v - 1e-9)[0]["status"] == "stale"


def test_v3c_failed_breakout_turns_stale():
    wp = [(0, 125), (20, 100), (40, 110), (60, 90), (80, 110), (100, 100), (106, 113), (112, 104.5)]
    df = make_df(wp)
    # D36: close breakout, see V3b
    r = _detect_close(df)[0]
    assert r["breakout_detected"] and r["status"] == "stale" and r["stale_reason"] == "failed_breakout"
    assert r["v3_fallback"] == pytest.approx((111 - 105.0) / 21)
    v = r["v3_fallback"]
    assert _detect_close(df, max_fallback_of_head=v)[0]["status"] == "confirmed"
    assert _detect_close(df, max_fallback_of_head=v - 1e-9)[0]["status"] == "stale"
    # a throwback to just under the neckline stays confirmed
    small = make_df(wp[:-1] + [(112, 109.5)])
    s = _detect_close(small)[0]
    assert s["status"] == "confirmed" and 0 < s["v3_fallback"] < 0.25


def test_v4_unlike_every_example_rejected():
    # left shoulder almost as deep as the head, head far right next to N2: no example looks like this
    t_off = dict(min_shoulder_drop_by_timeframe=None, max_shoulder_drop_diff=None, max_shoulder_drop_rel_diff=None,
                 min_head_rise_by_timeframe=None, min_head_prominence=None, max_shoulder_diff_of_head=None,
                 min_shoulder_depth_ratio=None, max_neckline_tilt_of_head=None)
    df = make_df([(0, 125), (20, 91), (30, 110), (90, 90), (95, 110), (100, 107), (119, 108)])
    assert _detect(df, **t_off) == []
    r = _detect(df, **t_off, max_template_distance=None)[0]
    assert r["v4_template_distance"] > 0.25 and r["shape_valid"] is True


# ---------- boundaries: value == limit passes, one ulp beyond fails ----------
SLOPED = [(0, 125), (20, 100), (40, 112), (60, 90), (80, 108), (100, 98.5), (119, 104)]


@pytest.mark.parametrize("key,field,kind", [
    ("max_counter_swing_of_head", "v1_max_counter_swing", "max"),
    ("max_skeleton_mad_of_head", "v2_mad", "max"),
    ("min_skeleton_r2", "v2_r2", "min"),
    ("max_neckline_close_violation", "v3_close_violation", "max"),
    ("max_template_distance", "v4_template_distance", "max"),
])
def test_shape_check_boundaries(key, field, kind):
    df = make_df(SLOPED, noise=0.05, seed=2)
    res = _detect(df)
    assert len(res) == 1
    value = res[0][field]
    eps = 1e-9 * max(1.0, abs(value))
    beyond = value + eps if kind == "min" else value - eps
    assert len(_detect(df, **{key: value})) == 1
    assert _detect(df, **{key: beyond}) == []


def test_template_exclude_changes_nearest():
    r = _detect(make_df(WIDE))[0]
    nearest = r["v4_nearest_template"]
    r2 = _detect(make_df(WIDE), template_exclude=[nearest])[0]
    assert r2["v4_nearest_template"] != nearest and r2["v4_template_distance"] >= r["v4_template_distance"]


# ---------- no look-ahead for the new fields ----------
@pytest.mark.parametrize("t", [100, 103, 106, 109, 112])
def test_validation_fields_independent_of_future(t):
    wp = [(0, 125), (20, 100), (40, 110), (60, 90), (80, 110), (100, 100), (106, 113), (112, 104.5), (119, 110)]
    df = make_df(wp, noise=0.05, seed=9)
    before = _detect(df.iloc[:t + 1])
    mutated = df.copy()
    mutated.loc[t + 1:, ["open", "high", "low", "close"]] *= 0.4
    after = _detect(mutated.iloc[:t + 1])
    assert after == before and before
    for r in before:
        for f in D26_FIELDS:
            assert f in r


def test_confirmed_rs_shape_values_stable_over_time():
    """Once RS is confirmed, V1 / V2 / V3a / V4 read only LS..RS, so they never change with newer bars."""
    df = make_df(WIDE, noise=0.05, seed=4)
    ts = df["timestamp"]
    ref = None
    for t in range(105, len(df)):
        rs = [r for r in _detect(df.iloc[:t + 1]) if r["RS_time"] == ts[100]]
        assert len(rs) == 1
        vals = tuple(rs[0][f] for f in ("v1_max_counter_swing", "v2_mad", "v2_r2", "v3_close_violation",
                                        "v4_template_distance", "v4_nearest_template", "shape_score"))
        ref = ref or vals
        assert vals == ref, t
