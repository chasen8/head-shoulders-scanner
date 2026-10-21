"""D36 rules on synthetic data: wick breakout (A), neckline touches + V1 split (C), de-duplication priority (C),
cross-timeframe superseding (C), T8 body lead-in. Real-data cases: tests/test_golden_d36.py."""
import numpy as np
import pandas as pd
import pytest

from ihs_scanner.config import CLASSIC_CONFIG, TEMPLATE_CONFIG
from ihs_scanner.detector import _count_touch_runs, deduplicate_patterns, detect_inverse_head_shoulders
from ihs_scanner.scanner import OUTPUT_FIELDS, supersede_by_higher_timeframe
from ihs_scanner.validation import counter_swing, leg_counter_swing, max_counter_swing, touch_peaks

from .synth import make_df

# LS 20, N1 40, H 60, N2 80, RS 100 (lows; highs = low + 1 -> flat neckline 111); confirmed RS, 106 bars
WIDE = [(0, 125), (20, 100), (40, 110), (60, 90), (80, 110), (100, 100), (105, 104)]


def _detect(df, tf="4h", **overrides):
    return detect_inverse_head_shoulders(df, tf, {"include_stale": True, **overrides}, symbol="T")


def test_profiles():
    assert TEMPLATE_CONFIG["breakout_price"] == "high" and CLASSIC_CONFIG["breakout_price"] == "close"
    assert TEMPLATE_CONFIG["reject_post_breakout_rs"] and not CLASSIC_CONFIG["reject_post_breakout_rs"]
    assert TEMPLATE_CONFIG["dedup_priority"] == "breakout_touches_slope" and CLASSIC_CONFIG["dedup_priority"] == "score"
    assert TEMPLATE_CONFIG["max_neckline_angle"] == {"4h": 30.0, "1d": 30.0, "1w": 45.0}
    assert CLASSIC_CONFIG["max_neckline_angle"] == 30.0
    for f in ("neckline_touches", "breakout_basis"):
        assert f in OUTPUT_FIELDS
    with pytest.raises(ValueError):
        _detect(make_df(WIDE), breakout_price="open")


# ---------- A. wick breakout ----------
def test_wick_crossing_is_a_breakout():
    df = make_df(WIDE[:-1] + [(105, 108.5)])  # after RS the price climbs back towards the neckline
    df.loc[104, "high"] = 113.0  # wick 1.8% above the 111 neckline; close ~108.2 stays below
    r_close = _detect(df, breakout_price="close")
    assert len(r_close) == 1 and not r_close[0]["breakout_detected"] and r_close[0]["status"] == "watchlist"
    r = _detect(df)
    assert len(r) == 1
    r = r[0]
    ts = df["timestamp"]
    assert r["breakout_detected"] and r["breakout_basis"] == "high" and r["status"] == "confirmed"
    assert r["breakout_time"] == ts[104] == r["first_cross_time"] and r["breakout_price"] == 113.0
    assert r["breakout_level"] == pytest.approx(111.0) and r["breakout_score"] == 20
    assert r["signal_time"] == ts[105]  # D13: the RS (100) is confirmed at 105, after the breakout bar
    # the buffer applies to the high too: 111 * 1.005 = 111.555
    df.loc[104, "high"] = 111.5
    assert not _detect(df)[0]["breakout_detected"]


def test_wick_above_neckline_before_rs_is_still_t4():
    # T4 (pre-pattern) is unchanged: a high 2% above the neckline between N1 and N2 rejects the pattern
    df = make_df(WIDE)
    df.loc[70, "high"] = 111 * 1.02
    assert _detect(df) == []


# ---------- C. neckline touches ----------
def test_count_touch_runs():
    # bars 0..20, N1 = 2, N2 = 12, RS = 20; ls = 0
    t = np.zeros(21, dtype=bool)
    t[[3, 4]] = True  # run starting at N1 + 1 -> part of N1
    t[[7, 8]] = True  # mid touch
    t[[11]] = True  # adjacent to N2
    t[[13]] = True  # adjacent to N2 (after)
    t[[16]] = True  # touch between N2 and RS
    assert _count_touch_runs(t, 0, 2, 12, 20) == 2


def test_leg_split_at_touch():
    # down leg 0 -> 10: falls to 3, rallies to the neckline at 6 (touch), falls to 10
    hi = np.log(np.array([110, 104, 99, 95, 101, 106, 110.5, 104, 98, 94, 90.0]))
    lo = hi - 0.01
    plain = counter_swing(lo, hi, 0, 10, "down")
    assert plain == pytest.approx(np.log(110.5) - (np.log(95) - 0.01))
    touch = np.zeros(11, dtype=bool)
    touch[6] = True
    assert touch_peaks(touch, hi, 0, 10) == [6]
    split = leg_counter_swing(lo, hi, 0, 10, "down", [6])
    assert split < 0.02  # every sub-leg is monotone (only the 0.01 bar ranges are left)
    assert leg_counter_swing(lo, hi, 0, 10, "down", []) == plain
    # up leg mirror: rises to the touch at 4, pulls back to 7, rises to 10
    up_hi = np.log(np.array([92, 96, 101, 106, 110.5, 104, 100, 97, 102, 106, 111.0]))
    up_lo = up_hi - 0.01
    assert counter_swing(up_lo, up_hi, 0, 10, "up") > 0.1
    assert leg_counter_swing(up_lo, up_hi, 0, 10, "up", [4]) < 0.02


def test_v1_ignores_rebound_to_neckline():
    # N1 -> H leg with a dip to 93 and a rebound whose high (110.9) touches the 111 neckline
    wp = [(0, 125), (20, 100), (40, 110), (46, 93), (50, 109.9), (60, 90), (80, 110), (100, 100), (105, 104)]
    df = make_df(wp)
    off = dict(max_counter_swing_of_head=None, max_skeleton_mad_of_head=None, max_template_distance=None,
               min_head_prominence=None)
    with_touch = _detect(df, **off)
    without = _detect(df, v1_ignore_neckline_touches=False, **off)
    assert len(with_touch) == len(without) == 1
    assert with_touch[0]["neckline_touches"] == 1
    assert without[0]["v1_max_counter_swing"] > 0.65 > with_touch[0]["v1_max_counter_swing"]
    # the touch count is computed in both cases (it is an output value)
    assert without[0]["neckline_touches"] == 1


# ---------- C. de-duplication priority ----------
def _row(**kw):
    t = pd.Timestamp
    base = {"symbol": "X", "timeframe": "1d", "LS_time": t("2024-01-01", tz="UTC"),
            "RS_time": t("2024-03-01", tz="UTC"), "pattern_score": 50.0, "breakout_detected": False,
            "neckline_touches": 0, "neckline_change": 0.02}
    base.update(kw)
    return base


def test_dedup_priority_order():
    hi_score_rising = _row(pattern_score=90.0, neckline_change=0.05)
    falling = _row(pattern_score=60.0, neckline_change=-0.05)
    touched = _row(pattern_score=55.0, neckline_change=0.04, neckline_touches=2)
    broke = _row(pattern_score=40.0, breakout_detected=True)
    assert deduplicate_patterns([hi_score_rising, falling])[0] is hi_score_rising  # D11 default: score
    assert deduplicate_patterns([hi_score_rising, falling], "breakout_touches_slope")[0] is falling
    assert deduplicate_patterns([hi_score_rising, falling, touched], "breakout_touches_slope")[0] is touched
    assert deduplicate_patterns([hi_score_rising, falling, touched, broke], "breakout_touches_slope")[0] is broke
    with pytest.raises(ValueError):
        deduplicate_patterns([falling], "nope")


# ---------- C. cross-timeframe ----------
def test_supersede_by_higher_timeframe():
    t = pd.Timestamp
    w = _row(timeframe="1w", LS_time=t("2023-10-02", tz="UTC"), RS_time=t("2024-06-03", tz="UTC"),
             neckline_change=-0.2, pattern_id="W")
    d_up = _row(H_time=t("2024-02-01", tz="UTC"), neckline_change=0.05, pattern_id="D_UP")
    d_down = _row(H_time=t("2024-02-01", tz="UTC"), neckline_change=-0.05, pattern_id="D_DOWN")
    d_out = _row(H_time=t("2024-07-01", tz="UTC"), neckline_change=0.05, pattern_id="D_OUT")  # H after 1w RS
    other = _row(symbol="Y", H_time=t("2024-02-01", tz="UTC"), neckline_change=0.05, pattern_id="Y_UP")
    h4 = _row(timeframe="4h", H_time=t("2024-02-01", tz="UTC"), neckline_change=0.05, pattern_id="H4")
    kept, gone = supersede_by_higher_timeframe([w, d_up, d_down, d_out, other, h4])
    assert [g["pattern_id"] for g in gone] == ["D_UP"] and gone[0]["superseded_by"] == "W"
    assert [k["pattern_id"] for k in kept] == ["W", "D_DOWN", "D_OUT", "Y_UP", "H4"]
    # a rising weekly neckline supersedes nothing; another exchange is another series
    assert supersede_by_higher_timeframe([{**w, "neckline_change": 0.01}, d_up])[1] == []
    assert supersede_by_higher_timeframe([{**w, "exchange": "okx"}, {**d_up, "exchange": "binance"}])[1] == []


# ---------- T8 body lead-in ----------
def test_t8_lead_in_ignores_a_lone_wick():
    wp = [(0, 120)] + WIDE[1:]
    df = make_df(wp)
    df.loc[8, "low"] = 85.0  # flash-crash wick below H (90); open / close stay ~116
    assert len(_detect(df)) == 1
    assert _detect(df, head_lead_in_price="low") == []
    df.loc[8, "close"] = 86.0  # a CLOSE below the head is still a rejection
    assert _detect(df) == []
