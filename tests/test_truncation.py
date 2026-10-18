"""8. Truncation / no-repaint tests.

For every t, detect(df[:t+1]) is the "live" answer at the close of bar t.
- every key time in a result is <= time[t]; RS_confirm_time = RS_time + N bars <= time[t]
- a pattern must NOT appear before its right shoulder is confirmed (t < RS.index + N)
- once it appears, its key points (and its breakout once found) never change
- results at t do not depend on data after t
"""
import numpy as np
import pandas as pd
import pytest

from ihs_scanner.detector import detect_inverse_head_shoulders

from .synth import IHS_BREAK, IHS_WATCH, make_df, random_walk_df

KEY_TIMES = ("LS_time", "N1_time", "H_time", "N2_time", "RS_time", "RS_confirm_time")
STABLE_FIELDS = ("LS_time", "LS_price", "N1_time", "N1_price", "H_time", "H_price",
                 "N2_time", "N2_price", "RS_time", "RS_price", "RS_confirm_time",
                 "shoulder_diff", "head_depth", "neckline_angle", "pattern_id")
CFG = {"include_stale": True}
N = 3  # 4h pivot window


def _check_times(res, df, t):
    now = df["timestamp"].iloc[t]
    bar = df["timestamp"].iloc[1] - df["timestamp"].iloc[0]
    for r in res:
        for k in KEY_TIMES:
            assert r[k] <= now, (t, k)
        assert r["RS_confirm_time"] == r["RS_time"] + N * bar
        if r["breakout_time"] is not None:
            assert r["RS_time"] < r["breakout_time"] <= now
            assert r["signal_time"] == max(r["RS_confirm_time"], r["breakout_time"])
        else:
            assert r["signal_time"] == r["RS_confirm_time"]
        assert r["signal_time"] <= now
        assert r["as_of_time"] == now


@pytest.mark.parametrize("waypoints,vol_spike", [(IHS_WATCH, None), (IHS_BREAK, 92)])
def test_pattern_appears_only_after_rs_confirmation(waypoints, vol_spike):
    df = make_df(waypoints, noise=0.05, seed=3)
    if vol_spike is not None:
        df.loc[vol_spike, "volume"] = 300.0
    ts = df["timestamp"]
    rs_index = 80
    first_seen = None
    reference = None
    breakout_seen = None
    for t in range(30, len(df)):
        res = detect_inverse_head_shoulders(df.iloc[:t + 1], "4h", CFG, symbol="T")
        _check_times(res, df, t)
        target = [r for r in res if r["RS_time"] == ts[rs_index]]
        if t < rs_index + N:
            assert target == [], f"pattern visible at t={t} before RS confirmation"
            continue
        assert len(target) == 1, f"pattern missing at t={t}"
        r = target[0]
        if first_seen is None:
            first_seen, reference = t, r
        for f in STABLE_FIELDS:
            assert r[f] == reference[f], (t, f)
        if r["breakout_time"] is not None:
            if breakout_seen is None:
                breakout_seen = r["breakout_time"]
            assert r["breakout_time"] == breakout_seen  # first breakout never moves
        else:
            assert breakout_seen is None  # a breakout cannot "un-happen"
    assert first_seen == rs_index + N
    if vol_spike is not None:
        assert breakout_seen is not None


def test_results_independent_of_future_data():
    df = make_df(IHS_BREAK, noise=0.05, seed=5)
    for t in (60, 83, 88, 92, 95):
        before = detect_inverse_head_shoulders(df.iloc[:t + 1], "4h", CFG, symbol="T")
        mutated = df.copy()
        for col in ("open", "high", "low", "close"):
            mutated.loc[t + 1:, col] = mutated.loc[t + 1:, col] * 0.5
        mutated.loc[t + 1:, "volume"] = 1e9
        after = detect_inverse_head_shoulders(mutated.iloc[:t + 1], "4h", CFG, symbol="T")
        assert before == after


def test_random_walk_truncation_invariants():
    """On noisy data: at every t, results only reference bars <= t, and no pattern
    shows up whose RS is not yet confirmed. Also every pattern reported live at t
    must be reproducible from the full history up to t (same key points)."""
    df = random_walk_df(600, seed=11)
    ts = df["timestamp"]
    for t in range(100, len(df), 7):
        res = detect_inverse_head_shoulders(df.iloc[:t + 1], "4h", CFG, symbol="RW")
        _check_times(res, df, t)
        for r in res:
            rs_i = int(np.flatnonzero(ts == r["RS_time"])[0])
            assert rs_i + N <= t
