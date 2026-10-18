"""D19 extra shape rules: balance, time symmetry, optional min length / prior downtrend.

Balance and symmetry follow neurotrader888/TechnicalAnalysisAutomation
head_shoulders.py check_ihs_pattern (MIT); the prior-downtrend idea comes from
yanivil/SignalSync (MIT). All tests use the default pivot window (4h N=5).
"""
import pytest

from ihs_scanner.config import CLASSIC_CONFIG, RULE_OFF_OVERRIDES, rules_off_overrides
from ihs_scanner.detector import detect_inverse_head_shoulders

from .synth import IHS_WATCH, make_df

OFF = {"enable_balance_rule": False, "max_time_symmetry_ratio": None}

# Reproduces the rejected BTCUSDT 4h case: LS->H 100 bars, H->RS 12 bars, and the
# left shoulder (100) sits above the right midpoint 0.5*(RS.low 97 + N2.high 101) = 99:
# a falling channel, not an IHS. Passes every spec rule (shoulder diff 3.05%,
# head depth 8.6%, neckline angle -6.8 deg).
LOPSIDED = [(0, 115), (10, 100), (40, 110), (110, 90), (116, 100), (122, 97), (140, 98)]


# The D19 rules are tested in isolation: the D21 rules (tests/test_rules_r2.py) are switched off here,
# otherwise R11 / R3 would also reject the LOPSIDED and balance fixtures.
D21_OFF = rules_off_overrides(RULE_OFF_OVERRIDES)


def _detect(df, **overrides):
    cfg = {"profile": "classic", "include_stale": True, **D21_OFF}  # D23: classic rule set
    cfg.update(overrides)
    return detect_inverse_head_shoulders(df, "4h", cfg, symbol="T")


def test_new_defaults():  # classic profile values (D19); the template profile is in test_template.py
    assert CLASSIC_CONFIG["pivot_window_by_timeframe"] == {"4h": 5, "1d": 5, "1w": 3}
    assert CLASSIC_CONFIG["enable_balance_rule"] is True
    assert CLASSIC_CONFIG["max_time_symmetry_ratio"] == 2.5
    assert CLASSIC_CONFIG["min_pattern_bars"] is None
    assert CLASSIC_CONFIG["require_prior_downtrend"] is False
    assert CLASSIC_CONFIG["prior_trend_bars"] == {"4h": 60, "1d": 50, "1w": 20}


# ---------- lopsided falling channel (the BTC 4h case) ----------
def test_lopsided_channel_rejected_by_default():
    assert _detect(make_df(LOPSIDED)) == []


def test_lopsided_channel_accepted_when_both_rules_off():
    df = make_df(LOPSIDED)
    ts = df["timestamp"]
    res = _detect(df, **OFF)
    assert len(res) == 1
    r = res[0]
    assert (r["LS_time"], r["N1_time"], r["H_time"], r["N2_time"], r["RS_time"]) == (
        ts[10], ts[40], ts[110], ts[116], ts[122])
    assert r["time_symmetry_ratio"] == pytest.approx(100 / 12)
    assert r["LS_price"] > 0.5 * (r["RS_price"] + r["N2_price"])  # balance violated


@pytest.mark.parametrize("overrides", [
    {"enable_balance_rule": False},  # symmetry alone rejects (100 > 2.5 * 12)
    {"max_time_symmetry_ratio": None},  # balance alone rejects (100 > 99)
])
def test_lopsided_channel_each_rule_alone_rejects(overrides):
    assert _detect(make_df(LOPSIDED), **overrides) == []


# ---------- balance boundaries ----------
def _balance_df(ls, n1_high, n2_high, rs):
    return make_df([(0, 115), (20, ls), (35, n1_high - 1), (50, 90),
                    (65, n2_high - 1), (80, rs), (99, 106)])


@pytest.mark.parametrize("n2_high,expected", [
    (102.0, 1),  # r_midpoint = 0.5*(98+102) = 100 == LS.low -> pass
    (101.9, 0),  # r_midpoint = 99.95 < LS.low 100 -> reject
])
def test_balance_left_shoulder_boundary(n2_high, expected):
    df = _balance_df(100.0, 111.0, n2_high, 98.0)
    assert len(_detect(df, max_time_symmetry_ratio=None)) == expected
    assert len(_detect(df, **OFF)) == 1  # only balance decides


@pytest.mark.parametrize("rs,expected", [
    (102.0, 1),  # l_midpoint = 0.5*(100+104) = 102 == RS.low -> pass
    (102.1, 0),  # RS.low above l_midpoint -> reject
])
def test_balance_right_shoulder_boundary(rs, expected):
    df = _balance_df(100.0, 104.0, 111.0, rs)
    assert len(_detect(df, max_time_symmetry_ratio=None)) == expected
    assert len(_detect(df, **OFF)) == 1


# ---------- time symmetry boundaries ----------
def _sym_df(l_to_h, r_to_h):
    ls = 20
    h = ls + l_to_h
    rs = h + r_to_h
    wp = [(0, 115), (ls, 100), ((ls + h) // 2, 110), (h, 90),
          ((h + rs) // 2, 110), (rs, 100), (rs + 19, 106)]
    return make_df(wp)


@pytest.mark.parametrize("l_to_h,r_to_h,expected", [
    (30, 12, 1), (31, 12, 0),  # left side long: 30 == 2.5*12 passes, 31 rejects
    (12, 30, 1), (12, 31, 0),  # right side long
])
def test_symmetry_boundary(l_to_h, r_to_h, expected):
    df = _sym_df(l_to_h, r_to_h)
    res = _detect(df, enable_balance_rule=False)
    assert len(res) == expected
    if expected:
        assert res[0]["time_symmetry_ratio"] == pytest.approx(2.5)
    off = _detect(df, **OFF)
    assert len(off) == 1
    assert off[0]["time_symmetry_ratio"] == pytest.approx(max(l_to_h, r_to_h) / min(l_to_h, r_to_h))


def test_symmetry_ratio_configurable():
    df = _sym_df(31, 12)
    assert len(_detect(df, max_time_symmetry_ratio=2.6)) == 1
    assert len(_detect(df, max_time_symmetry_ratio=2.5)) == 0


def test_time_symmetry_ratio_value_symmetric_pattern():
    res = _detect(make_df(IHS_WATCH, noise=0.05, seed=1))
    assert len(res) == 1
    assert res[0]["time_symmetry_ratio"] == pytest.approx(1.0)  # 30 bars each side


# ---------- optional: min_pattern_bars ----------
@pytest.mark.parametrize("min_bars,expected", [(None, 1), (60, 1), (61, 0)])
def test_min_pattern_bars(min_bars, expected):
    df = make_df(IHS_WATCH)  # LS->RS = 60 bars
    assert len(_detect(df, min_pattern_bars=min_bars)) == expected


# ---------- optional: prior downtrend ----------
# IHS_WATCH: flat neckline at 111, H.low 90 -> head height 21; LS.low 100 at index 20.
def _prior_df(start_low):
    return make_df([(0, start_low)] + IHS_WATCH[1:])


def test_prior_downtrend_positive():
    df = _prior_df(125)  # max close before LS = 125.5 -> 25.5 >= 21
    assert len(_detect(df, require_prior_downtrend=True)) == 1


def test_prior_downtrend_negative():
    df = _prior_df(115)  # max close before LS = 115.5 -> 15.5 < 21
    assert len(_detect(df, require_prior_downtrend=True)) == 0
    assert len(_detect(df)) == 1  # off by default


def test_prior_downtrend_window_length():
    df = _prior_df(125)
    # only bars [10, 20) count: max close = 113 -> 13 < 21
    assert len(_detect(df, require_prior_downtrend=True, prior_trend_bars={"4h": 10})) == 0
