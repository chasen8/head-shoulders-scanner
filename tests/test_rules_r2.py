"""D21 rules R1 / R11 / R3 / R2 / R21 (formulas and sources in ihs_scanner/config.py).

Each rule: a synthetic pattern that only that rule rejects (default -> rejected, rule off -> accepted),
plus boundary values. R1's ATR must not look ahead. No network.
"""
import numpy as np
import pytest

from ihs_scanner.config import CLASSIC_CONFIG, RULE_OFF_OVERRIDES, merge_config, rules_off_overrides
from ihs_scanner.detector import detect_inverse_head_shoulders, wilder_atr
from ihs_scanner.scanner import OUTPUT_FIELDS

from .synth import IHS_WATCH, make_df, random_walk_df

D21_FIELDS = ("atr_at_head", "head_atr_multiple", "neckline_total_slope", "shoulder_diff_of_head",
              "shoulder_depth_ratio", "pattern_start_bars_before_ls")


def _detect(df, **overrides):
    cfg = {"profile": "classic", "include_stale": True}  # D22: classic rule set
    cfg.update(overrides)
    return detect_inverse_head_shoulders(df, "4h", cfg, symbol="T")


def _off(rule):
    return dict(RULE_OFF_OVERRIDES[rule])


def test_defaults_and_off_switches():
    assert CLASSIC_CONFIG["min_head_atr"] == 1.0
    assert CLASSIC_CONFIG["atr_length"] == 14
    assert CLASSIC_CONFIG["enable_pattern_start_rule"] is True
    assert CLASSIC_CONFIG["pattern_start_lookback"] is None
    assert CLASSIC_CONFIG["pattern_start_price"] == "high"
    assert CLASSIC_CONFIG["max_neckline_total_slope"] == 0.15
    assert CLASSIC_CONFIG["max_shoulder_diff_of_head"] == 0.5
    assert CLASSIC_CONFIG["min_shoulder_depth_ratio"] == 0.25
    assert CLASSIC_CONFIG["max_shoulder_depth_ratio"] is None
    off = rules_off_overrides(["r1", "R11", " R3", "R2", "R21", ""])
    cfg = merge_config({"profile": "classic", **off})  # every override key exists
    assert cfg["min_head_atr"] is None and cfg["enable_pattern_start_rule"] is False
    assert cfg["max_neckline_total_slope"] is None and cfg["max_shoulder_diff_of_head"] is None
    assert cfg["min_shoulder_depth_ratio"] is None and cfg["max_shoulder_depth_ratio"] is None
    with pytest.raises(KeyError):
        rules_off_overrides(["R9"])
    for f in D21_FIELDS:
        assert f in OUTPUT_FIELDS


def test_clean_pattern_passes_all_rules_and_reports_values():
    # IHS_WATCH: flat neckline at 111 (highs = lows + 1), H.low 90 -> head height 21, shoulders 100
    res = _detect(make_df(IHS_WATCH))
    assert len(res) == 1
    r = res[0]
    assert r["neckline_total_slope"] == 0.0
    assert r["shoulder_diff_of_head"] == 0.0
    assert r["shoulder_depth_ratio"] == pytest.approx(11 / 21)
    atr_h = wilder_atr(make_df(IHS_WATCH), 14)[50]
    assert r["atr_at_head"] == atr_h
    assert 1.5 < atr_h < 11 / 6  # TR = |slope| + spread/2: 11/6 just before H, smaller further left
    assert r["head_atr_multiple"] == pytest.approx(10 / atr_h)
    # lows fall 0.75/bar from 115: bar 5 (low 111.25, close 111.75) is the first close above 111
    assert r["pattern_start_bars_before_ls"] == 14  # default high (D21) crosses 1 bar closer than close (15)
    assert _detect(make_df(IHS_WATCH), pattern_start_price="close")[0]["pattern_start_bars_before_ls"] == 15


# ---------- R1: head depth in ATR ----------
# spread 4 -> TR = 4 everywhere -> ATR 4; head only 3.5 below the shoulders (head depth 3.5% passes)
R1_WP = [(0, 115), (20, 100), (35, 106), (50, 96.5), (65, 106), (80, 100), (99, 103)]


def test_r1_rejects_shallow_head_in_atr_units():
    df = make_df(R1_WP, spread=4.0)
    assert _detect(df) == []
    res = _detect(df, **_off("R1"))
    assert len(res) == 1
    assert res[0]["atr_at_head"] == pytest.approx(4.0)
    assert res[0]["head_atr_multiple"] == pytest.approx(3.5 / 4.0)


@pytest.mark.parametrize("k,expected", [(0.875, 1), (0.8751, 0), (0.5, 1)])
def test_r1_boundary(k, expected):
    df = make_df(R1_WP, spread=4.0)
    assert len(_detect(df, min_head_atr=k)) == expected  # 3.5 >= 0.875 * 4 passes (<= rule)


def test_r1_skipped_when_atr_not_available():
    df = make_df(R1_WP, spread=4.0)
    res = _detect(df, atr_length=60)  # H.index 50 < 59 -> ATR[H] is NaN -> rule skipped
    assert len(res) == 1
    assert res[0]["atr_at_head"] is None and res[0]["head_atr_multiple"] is None


def test_wilder_atr_formula():
    df = make_df([(0, 100), (3, 103), (6, 100)], spread=1.0)  # slope +-1 -> TR = 1.5 except TR[0] = 1
    atr = wilder_atr(df, 3)
    assert np.isnan(atr[:2]).all()
    assert atr[2] == pytest.approx((1 + 1.5 + 1.5) / 3)
    assert atr[3] == pytest.approx((atr[2] * 2 + 1.5) / 3)


def test_wilder_atr_no_lookahead():
    df = random_walk_df(600, seed=3)
    full = wilder_atr(df, 14)
    for k in (1, 13, 14, 15, 100, 333, 600):
        part = wilder_atr(df.iloc[:k], 14)
        np.testing.assert_array_equal(part, full[:k])


def test_atr_at_head_same_on_truncated_and_full_df():
    df = make_df(IHS_WATCH, noise=0.3, seed=5)
    full = {r["pattern_id"]: r for r in _detect(df)}
    assert full
    h_index = 50
    for t in range(h_index + 5 + 30, len(df)):  # from RS confirmation (80 + 5) to the end
        for r in _detect(df.iloc[:t + 1]):
            if r["pattern_id"] in full:
                assert r["atr_at_head"] == full[r["pattern_id"]]["atr_at_head"]
                assert r["head_atr_multiple"] == full[r["pattern_id"]]["head_atr_multiple"]


# ---------- R11: pattern start (price above the extended neckline before LS) ----------
def _r11_df(gap, cross_low=112.0):
    """IHS_WATCH shifted right by 20 (LS 40, N1 55, H 70, N2 85, RS 100; flat neckline 111).
    Before LS the lows sit far below the neckline except bar LS-gap (low = cross_low)."""
    ls = 40
    pre = [(0, 104.5), (ls - gap - 1, 104.4), (ls - gap, cross_low), (ls - gap + 1, 104.2)]
    body = [(ls, 100), (55, 110), (70, 90), (85, 110), (100, 100), (119, 106)]
    return make_df(pre + body)


def test_r11_rejects_when_price_never_above_neckline_before_ls():
    df = make_df([(0, 104), (20, 100), (35, 110), (50, 90), (65, 110), (80, 100), (99, 106)])
    assert _detect(df) == []
    res = _detect(df, **_off("R11"))
    assert len(res) == 1 and res[0]["pattern_start_bars_before_ls"] is None


@pytest.mark.parametrize("gap,expected", [(29, 1), (30, 0), (10, 1)])
def test_r11_default_lookback_is_neckline_width_minus_one(gap, expected):
    # neckline width N2 - N1 = 30 -> neurotrader888 range(1, 30): j = 1..29
    df = _r11_df(gap)
    res = _detect(df)
    assert len(res) == expected
    if expected:
        assert res[0]["pattern_start_bars_before_ls"] == gap
    off = _detect(df, **_off("R11"))
    assert len(off) == 1


@pytest.mark.parametrize("lookback,expected", [(10, 1), (9, 0)])
def test_r11_lookback_configurable(lookback, expected):
    assert len(_detect(_r11_df(10), pattern_start_lookback=lookback)) == expected


def test_r11_price_source_close_vs_high():
    df = _r11_df(10, cross_low=110.6)  # close 111.1 > 111 passes; now low 110.4: close 110.9, high 111.4
    assert len(_detect(df, pattern_start_price="close")) == 1
    df = _r11_df(10, cross_low=110.4)
    assert _detect(df, pattern_start_price="close") == []  # close below the neckline
    assert len(_detect(df, pattern_start_price="high")) == 1


def test_r11_rejects_when_data_runs_out_before_a_cross():
    # LS at index 6 (pivot window 5): only 6 bars before it, all below the neckline, lookback 29
    # (the original returns None once LS - j < 0)
    df = make_df([(0, 104), (6, 100), (35, 110), (50, 90), (65, 110), (80, 100), (99, 106)])
    assert _detect(df, max_time_symmetry_ratio=None, max_shoulder_diff_of_head=None) == []
    assert len(_detect(df, max_time_symmetry_ratio=None, **_off("R11"))) >= 1


# ---------- R3: total neckline tilt ----------
# N2.high = 111 * 1.10 = 122.1 over 30 bars: angle 18.4 deg passes the 30 deg rule, but the
# total tilt |0.37 * 60| / close[H] 90.5 = 0.2453 > 0.15
R3_WP = [(0, 115), (20, 100), (35, 110), (50, 90), (65, 111 * 1.10 - 1), (80, 100), (99, 106)]
R3_VALUE = abs((111 * 1.10 - 111) / 30 * 60) / 90.5


def test_r3_rejects_steep_total_tilt():
    df = make_df(R3_WP)
    assert _detect(df) == []
    res = _detect(df, **_off("R3"))
    assert len(res) == 1
    assert res[0]["neckline_total_slope"] == pytest.approx(R3_VALUE)
    assert abs(res[0]["neckline_angle"]) < 30


def test_r3_boundary():
    df = make_df(R3_WP)
    value = _detect(df, **_off("R3"))[0]["neckline_total_slope"]
    assert len(_detect(df, max_neckline_total_slope=value)) == 1  # equal passes
    assert len(_detect(df, max_neckline_total_slope=value * (1 - 1e-9))) == 0
    assert len(_detect(df, max_neckline_total_slope=0.25)) == 1


# ---------- R2: shoulder difference relative to head height ----------
# spread 0.5: LS 100, RS 96 (4.08% diff passes 5%), H 95 (depth 3.06% passes 3%),
# N1.high 101, N2.high 104.5 -> neck(H) = 102.75, head height 7.75; 4 / 7.75 = 0.516 > 0.5
R2_WP = [(0, 115), (20, 100), (35, 100.5), (50, 95), (65, 104), (80, 96), (99, 100)]


def test_r2_rejects_large_shoulder_diff_vs_head_height():
    df = make_df(R2_WP, spread=0.5)
    assert _detect(df) == []
    res = _detect(df, **_off("R2"))
    assert len(res) == 1
    assert res[0]["shoulder_diff_of_head"] == pytest.approx(4 / 7.75)
    assert res[0]["shoulder_diff"] <= 0.05


@pytest.mark.parametrize("r,expected", [(4 / 7.75 + 1e-9, 1), (4 / 7.75 - 1e-6, 0), (0.30, 0), (0.6, 1)])
def test_r2_boundary(r, expected):
    assert len(_detect(make_df(R2_WP, spread=0.5), max_shoulder_diff_of_head=r)) == expected


# ---------- R21: shoulder depth / head height ----------
# shoulders 100, neckline highs 103, H 88: mean shoulder depth 3 / head height 15 = 0.2 < 0.25
R21_WP = [(0, 115), (20, 100), (35, 102), (50, 88), (65, 102), (80, 100), (99, 101)]


def test_r21_rejects_shallow_shoulders():
    df = make_df(R21_WP)
    assert _detect(df) == []
    res = _detect(df, **_off("R21"))
    assert len(res) == 1
    assert res[0]["shoulder_depth_ratio"] == pytest.approx(0.2)


def test_r21_min_boundary():
    df = make_df(R21_WP)
    value = _detect(df, **_off("R21"))[0]["shoulder_depth_ratio"]
    assert len(_detect(df, min_shoulder_depth_ratio=value)) == 1
    assert len(_detect(df, min_shoulder_depth_ratio=value + 1e-9)) == 0


def test_r21_optional_max_bound():
    df = make_df(IHS_WATCH)  # ratio 11/21 = 0.524
    assert len(_detect(df, max_shoulder_depth_ratio=0.7)) == 1
    assert len(_detect(df, max_shoulder_depth_ratio=11 / 21)) == 1
    assert len(_detect(df, max_shoulder_depth_ratio=0.5)) == 0
