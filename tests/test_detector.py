import math
import time

import pytest

from ihs_scanner.detector import detect_inverse_head_shoulders, find_pattern_candidates
from ihs_scanner.neckline import calculate_neckline

from .synth import IHS_BREAK, IHS_WATCH, make_df, random_walk_df, rs_low_for_diff


CLASSIC = {"profile": "classic"}  # D22: these tests pin the classic (D19/D21) rule set


def _detect(df, tf="4h", **overrides):
    return detect_inverse_head_shoulders(df, tf, {**CLASSIC, **overrides}, symbol="TEST")


# ---------- 2. clean synthetic pattern ----------
def test_detects_clean_ihs_in_candidate_mode():
    df = make_df(IHS_WATCH, noise=0.05, seed=1)
    ts = df["timestamp"]
    res = _detect(df, pivot_window_by_timeframe={"4h": 3})  # explicit N=3 (RS confirm = RS+3)
    assert len(res) == 1
    r = res[0]
    assert r["mode"] == "candidate" and r["status"] == "watchlist"
    assert r["pattern_type"] == "inverse_head_and_shoulders"
    assert (r["LS_time"], r["N1_time"], r["H_time"], r["N2_time"], r["RS_time"]) == (
        ts[20], ts[35], ts[50], ts[65], ts[80])
    assert r["LS_price"] == pytest.approx(100) and r["H_price"] == pytest.approx(90)
    assert r["RS_price"] == pytest.approx(100)
    assert r["N1_price"] == pytest.approx(111) and r["N2_price"] == pytest.approx(111)
    assert r["RS_confirm_time"] == ts[83]
    assert r["breakout_detected"] is False
    assert r["breakout_time"] is None and r["breakout_price"] is None and r["volume_ratio"] is None
    assert r["current_close"] == pytest.approx(df["close"].iloc[-1])
    assert r["pattern_id"].startswith("TEST|4h|2024-01-04T08:00:00Z|")
    for key in ("shoulder_score", "head_depth_score", "neckline_score", "breakout_score"):
        assert key in r


# ---------- 3. shoulder difference ----------
@pytest.mark.parametrize("tf,diff,head,expected", [
    ("4h", 0.06, 90, 0), ("4h", 0.04, 90, 1),
    ("1d", 0.08, 80, 1), ("1d", 0.12, 80, 0),
])
def test_shoulder_diff_limits(tf, diff, head, expected):
    rs = rs_low_for_diff(100.0, diff)
    wp = [(0, 115), (20, 100), (35, 110), (50, head), (65, 110), (80, rs), (99, 106)]
    res = _detect(make_df(wp, timeframe=tf), tf)
    assert len(res) == expected
    if expected:
        assert res[0]["shoulder_diff"] == pytest.approx(diff)


# ---------- 4. neckline angle ----------
@pytest.mark.parametrize("rise,expected", [(0.25, 0), (0.10, 1)])
def test_neckline_angle_limit(rise, expected):
    n2_low = 111 * (1 + rise) - 1  # N2.high = N1.high * (1 + rise)
    wp = [(0, 115), (20, 100), (35, 110), (50, 90), (65, n2_low), (80, 100), (99, 106)]
    # D21 R3 (total neckline tilt 0.245 > 0.15) would also reject rise=0.10; off so the angle decides
    res = _detect(make_df(wp), max_neckline_total_slope=None)
    assert len(res) == expected
    if expected:
        exp_angle = math.degrees(math.atan(rise / 30 * 100))
        assert res[0]["neckline_angle"] == pytest.approx(exp_angle)  # ~18.43 deg
        assert res[0]["neckline_slope_pct_per_bar"] == pytest.approx(rise / 30)


def test_neckline_formula():
    neck = calculate_neckline((10, 100.0), (20, 110.0))
    assert neck.price_at(15) == pytest.approx(105.0)
    assert neck.neckline_price_at(30) == pytest.approx(120.0)  # extrapolation
    assert neck.slope_pct_per_bar == pytest.approx(0.01)
    assert neck.angle == pytest.approx(45.0)  # atan(0.01 * 100)
    assert calculate_neckline((0, 100.0), (10, 100.0)).angle == 0.0


# ---------- 5. breakout mode / volume filter ----------
def _break_df(breakout_volume):
    vol = [100.0] * 97
    vol[92] = breakout_volume
    return make_df(IHS_BREAK, volume=vol)


def test_breakout_mode_skips_patterns_without_breakout():
    df = make_df(IHS_WATCH)
    assert len(_detect(df)) == 1
    assert _detect(df, enable_breakout_filter=True) == []


def test_breakout_detected_time_and_volume():
    df = _break_df(300.0)
    res = _detect(df, enable_breakout_filter=True)
    assert len(res) == 1
    r = res[0]
    # close = low + 0.5 > 111 * 1.005 = 111.555 first at bar 92 (low 112)
    assert r["mode"] == "breakout" and r["status"] == "confirmed"
    assert r["breakout_detected"] is True
    assert r["breakout_time"] == df["timestamp"][92]
    assert r["breakout_price"] == pytest.approx(112.5)
    assert r["average_volume_20"] == pytest.approx(100.0)  # excludes the breakout bar itself
    assert r["volume_ratio"] == pytest.approx(3.0)  # would be 2.73 if self-inclusive
    assert r["breakout_score"] == 20


def test_volume_filter_rejects_low_volume_breakout():
    low = _break_df(110.0)  # ratio 1.1 < 1.2
    assert len(_detect(low, enable_breakout_filter=True)) == 1  # no volume filter
    assert _detect(low, enable_breakout_filter=True, enable_volume_filter=True) == []
    high = _break_df(130.0)  # ratio 1.3
    res = _detect(high, enable_breakout_filter=True, enable_volume_filter=True)
    assert len(res) == 1 and res[0]["volume_ratio"] == pytest.approx(1.3)
    # candidate mode with volume filter: low-volume cross is not a breakout -> watchlist
    cand = _detect(low, enable_volume_filter=True)
    assert len(cand) == 1 and cand[0]["status"] == "watchlist" and not cand[0]["breakout_detected"]


def test_breakout_age_counts_from_first_cross():  # D14: no reset on volume re-break
    # low-volume cross at 92, high-volume close above neckline at 96
    vol = [100.0] * 101
    vol[92] = 110.0
    vol[96] = 300.0
    df = make_df(IHS_BREAK + [(100, 116)], volume=vol)
    res = _detect(df, enable_volume_filter=True, include_stale=True)
    assert len(res) == 1
    r = res[0]
    assert r["first_cross_time"] == df["timestamp"][92]
    assert r["breakout_time"] == df["timestamp"][96]
    # last bar 100: 4 bars since breakout but 8 since first cross > max_breakout_age 4h (6)
    assert r["status"] == "stale"
    fresh = _detect(df, enable_volume_filter=True, max_breakout_age={"4h": 8})
    assert len(fresh) == 1 and fresh[0]["status"] == "confirmed"


def test_candidate_mode_breakout_gets_full_breakout_score():  # D10
    res = _detect(_break_df(300.0))
    assert len(res) == 1 and res[0]["mode"] == "candidate"
    assert res[0]["breakout_detected"] and res[0]["breakout_score"] == 20


def test_stale_breakout_filtered_unless_include_stale():  # D9
    df = make_df(IHS_BREAK + [(110, 125)])  # breakout at 92, last bar 110 -> age 18
    assert _detect(df) == []
    res = _detect(df, include_stale=True)
    assert len(res) == 1 and res[0]["status"] == "stale"


# ---------- D7 invalidation ----------
def test_right_shoulder_broken_before_breakout_invalidates():
    wp = [(0, 115), (20, 100), (35, 110), (50, 90), (65, 110), (80, 100),
          (86, 104), (90, 98), (99, 106)]
    df = make_df(wp)
    cands = find_pattern_candidates(df, "4h", CLASSIC, "TEST")
    rs_times = {c["RS_time"] for c in cands}
    assert df["timestamp"][80] not in rs_times  # broken RS
    assert df["timestamp"][90] in rs_times  # the new right shoulder forms a new pattern


# ---------- 9. de-duplication ----------
def test_dedup_keeps_single_best_pattern():
    wp = [(0, 115), (10, 101), (15, 104), (20, 100), (35, 110), (50, 90),
          (65, 110), (80, 100), (99, 106)]
    df = make_df(wp)
    raw = find_pattern_candidates(df, "4h", CLASSIC, "TEST")
    assert len(raw) == 2  # LS at 10 and LS at 20
    res = _detect(df)
    assert len(res) == 1
    assert res[0]["LS_time"] == df["timestamp"][20]  # shoulder_diff 0 -> highest score
    assert res[0]["pattern_score"] == max(r["pattern_score"] for r in raw)


# ---------- performance ----------
def test_detection_speed_2000_bars():
    df = random_walk_df(2000)
    t0 = time.perf_counter()
    res = detect_inverse_head_shoulders(df, "4h", {**CLASSIC, "include_stale": True}, symbol="RW")
    dt = time.perf_counter() - t0
    print(f"\n2000-bar random walk (classic): {len(res)} patterns (incl. stale) in {dt * 1000:.1f} ms")
    assert dt < 2.0
