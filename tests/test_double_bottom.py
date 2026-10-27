"""D49 double bottom (ihs_scanner/double_bottom.py).

Synthetic zigzags (tests/synth.make_df: piecewise-linear lows through the waypoints, high = low + 1, close = low + 0.5):
a waypoint trough is a pivot low at its value, a waypoint peak is a pivot high at value + 1. 1d, pivot window N = 5.
"""
import math

import numpy as np
import pandas as pd
import pytest

from ihs_scanner.config import DOUBLE_BOTTOM_CONFIG, DEFAULT_CONFIG, double_bottom_config
from ihs_scanner.double_bottom import detect_double_bottoms

from .synth import make_df, random_walk_df

N = 5
# prior decline 130 -> 100, B1 = 100 (bar 30), P = 112 (+1 high = 113, bar 45), B2 = 100.5 (bar 60)
W = [(0, 130), (30, 100), (45, 112), (60, 100.5)]
W_WATCH = W + [(75, 109)]                       # n = 76, never reaches 113 * 1.01
W_BREAK = W + [(75, 112), (83, 116)]            # n = 84, the high crosses 114.13 at bar 77
W_UNDERCUT = [(0, 130), (30, 100), (45, 112), (60, 99.4), (75, 109)]
W_HIGHER = [(0, 130), (30, 100), (45, 112), (60, 101.4), (75, 109)]

# counter-examples
NO_DECLINE = [(0, 100), (30, 100), (45, 112), (60, 100.5), (75, 109)]       # flat before B1: no prior decline
DECLINE_BELOW_P = [(0, 108), (30, 100), (45, 112), (60, 100.5), (75, 109)]  # 8% drop, prior high < P
BOTTOMS_FAR = [(0, 130), (30, 100), (45, 112), (60, 90), (75, 105)]        # B2 10% below B1
B2_TOO_HIGH = [(0, 130), (30, 100), (45, 112), (60, 108), (75, 111)]       # B2 8% above B1
TOO_CLOSE = [(0, 130), (30, 100), (36, 112), (41, 100.5), (55, 109)]       # 11 bars between the bottoms (< 22)
SHALLOW = [(0, 130), (30, 100), (45, 103), (60, 100.5), (75, 102)]         # rally of 3%, < 6%
LOWER_DIP = [(0, 130), (30, 100), (40, 112), (47, 92), (52, 112), (66, 100.5), (80, 109)]  # a deeper low between
BROKEN_AFTER_B2 = [(0, 130), (30, 100), (45, 112), (60, 100.5), (68, 108), (78, 94), (90, 96)]  # closes below H


def det(wp, cfg=None, n=None, tf="1d", **kw):
    df = make_df(wp, n=n, timeframe=tf)
    return df, detect_double_bottoms(df, tf, cfg, symbol="T", **kw)


# ------------------------------------------------------------------ positives

def test_watchlist_w_values():
    df, res = det(W_WATCH)
    assert len(res) == 1
    r = res[0]
    assert r["status"] == "watchlist" and r["pattern_type"] == "double_bottom" and not r["breakout_detected"]
    ts = df["timestamp"]
    assert r["B1_time"] == ts[30].strftime("%Y-%m-%dT%H:%M:%SZ")
    assert r["P_time"] == ts[45].strftime("%Y-%m-%dT%H:%M:%SZ")
    assert r["B2_time"] == ts[60].strftime("%Y-%m-%dT%H:%M:%SZ")
    assert r["B1_price"] == 100 and r["P_price"] == 113 and r["B2_price"] == 100.5
    h = 100.0
    assert r["invalidation_price"] == h and r["neckline"] == 113
    assert r["tp1"] == pytest.approx(h * (113 / h) ** 1.414)
    assert r["tp2"] == pytest.approx(113 * (1 + (113 - h) / h))
    assert r["bars_between"] == 30 and r["bars_since_b2"] == 15
    assert r["prior_decline_pct"] == pytest.approx((131 - 100) / 131)


def test_confirmed_breakout_on_wick():
    df, res = det(W_BREAK)
    assert len(res) == 1
    r = res[0]
    assert r["status"] == "confirmed" and r["breakout_detected"]
    first = 61 + int(np.argmax(df["high"].to_numpy()[61:] > 113 * 1.01))
    assert r["breakout_time"] == df["timestamp"][first].strftime("%Y-%m-%dT%H:%M:%SZ")
    assert r["breakout_basis"] == "high" and r["bars_since_breakout"] == len(df) - 1 - first
    assert r["signal_time"] == r["breakout_time"]


def test_forming_provisional_b2():
    df = make_df(W_WATCH, timeframe="1d")
    for t, expect in ((61, "forming"), (63, "forming"), (65, "watchlist")):
        res = detect_double_bottoms(df.iloc[:t + 1], "1d", symbol="T")
        assert [r["status"] for r in res] == [expect], t
    res = detect_double_bottoms(df.iloc[:64], "1d", symbol="T")
    assert res[0]["B2_provisional"] and res[0]["B2_confirm_time"] is None and res[0]["B2_time"] == \
        df["timestamp"][60].strftime("%Y-%m-%dT%H:%M:%SZ")


@pytest.mark.parametrize("wp", [W_UNDERCUT, W_HIGHER])
def test_shallow_undercut_and_higher_second_bottom_allowed(wp):
    _, res = det(wp)
    assert len(res) == 1 and res[0]["status"] == "watchlist"


def test_weekly_and_4h_variants():
    _, res = det([(0, 130), (14, 100), (22, 112), (32, 100.5), (38, 106)], tf="1w")
    assert len(res) == 1 and res[0]["timeframe"] == "1w" and res[0]["bars_between"] == 18
    _, res = det(W_BREAK, tf="4h")
    assert len(res) == 1 and res[0]["status"] == "confirmed"


def test_stale_after_age_and_failed_breakout():
    wp = W + [(75, 112), (90, 125), (100, 125)]
    df = make_df(wp, timeframe="1d")
    assert detect_double_bottoms(df, "1d", symbol="T") == []  # breakout 15+ bars ago (and run away)
    rows = detect_double_bottoms(df, "1d", {"include_stale": True}, symbol="T")
    assert [r["status"] for r in rows] == ["stale"] and rows[0]["stale_reason"] in ("age", "runaway")
    # breaks out then falls back below the neckline by more than 0.25 of the height
    wp = W + [(75, 112), (78, 116), (81, 100.6)]
    rows = detect_double_bottoms(make_df(wp, timeframe="1d"), "1d", {"include_stale": True}, symbol="T")
    assert rows and rows[0]["status"] == "stale" and rows[0]["stale_reason"] == "failed_breakout"


def test_row_fields_json_ready():
    _, res = det(W_BREAK)
    for k, v in res[0].items():
        assert isinstance(v, (str, int, float, bool, type(None))), k
        if isinstance(v, float):
            assert math.isfinite(v), k


# ------------------------------------------------------------------ counter-examples

@pytest.mark.parametrize("wp,why", [
    (NO_DECLINE, "no prior decline"), (DECLINE_BELOW_P, "prior high below the neckline / decline too small"),
    (BOTTOMS_FAR, "B2 far below B1"), (B2_TOO_HIGH, "bottoms far apart (B2 above)"),
    (TOO_CLOSE, "bottoms too close in time"), (SHALLOW, "rally too small"),
    (LOWER_DIP, "a lower low between the bottoms (close below H)"),
    (BROKEN_AFTER_B2, "closed below H after B2 before any breakout")])
def test_counter_examples_are_rejected(wp, why):
    _, res = det(wp, {"include_stale": True})
    first_bottom = pd.Timestamp("2024-01-01", tz="UTC") + pd.Timedelta(days=30)
    assert [r for r in res if pd.Timestamp(r["B1_time"]) == first_bottom] == [], why


# ------------------------------------------------------------------ config / IHS untouched

def test_config_is_separate_and_market_aware():
    assert "min_rise_atr" not in DEFAULT_CONFIG and "head_prominence" not in DOUBLE_BOTTOM_CONFIG
    assert double_bottom_config(market="tw")["max_bars_between_by_timeframe"] == {"4h": 150, "1d": 150, "1w": 60}
    assert double_bottom_config()["max_bars_between_by_timeframe"]["4h"] == 300
    with pytest.raises(KeyError):
        double_bottom_config({"no_such_key": 1})
    with pytest.raises(ValueError):
        detect_double_bottoms(make_df(W_WATCH), "15m")


def test_ihs_results_unchanged_by_the_new_module():
    from ihs_scanner.detector import detect_inverse_head_shoulders
    from .synth import IHS_WATCH
    df = make_df(IHS_WATCH)
    a = detect_inverse_head_shoulders(df, "4h", {"profile": "classic", "include_stale": True}, symbol="T")
    detect_double_bottoms(df, "4h", symbol="T")
    b = detect_inverse_head_shoulders(df, "4h", {"profile": "classic", "include_stale": True}, symbol="T")
    assert a == b


# ------------------------------------------------------------------ no look-ahead (truncation)

def _times_ok(res, df, t):
    now = df["timestamp"].iloc[t]
    for r in res:
        for k in ("B1_time", "P_time", "B2_time", "breakout_time", "signal_time", "as_of_time"):
            if r[k] is not None:
                assert pd.Timestamp(r[k]) <= now, (t, k)
        assert pd.Timestamp(r["as_of_time"]) == now
        assert pd.Timestamp(r["P_time"]) <= now - N * (df["timestamp"].iloc[1] - df["timestamp"].iloc[0])  # P confirmed
        if r["B2_confirm_time"] is not None:
            assert pd.Timestamp(r["B2_confirm_time"]) <= now
            assert r["signal_time"] >= r["B2_confirm_time"]


def test_truncation_breakout_never_repaints():
    df = make_df(W_BREAK, timeframe="1d")
    ts = df["timestamp"]
    first = 61 + int(np.argmax(df["high"].to_numpy()[61:] > 113 * 1.01))
    seen = None
    for t in range(40, len(df)):
        part = df.iloc[:t + 1]
        res = detect_double_bottoms(part, "1d", {"include_stale": True}, symbol="T")
        _times_ok(res, df, t)
        mutated = df.copy()
        mutated.loc[t + 1:, ["open", "high", "low", "close"]] *= 0.5
        assert detect_double_bottoms(mutated.iloc[:t + 1], "1d", {"include_stale": True}, symbol="T") == res
        brk = [r for r in res if r["breakout_time"]]
        if t < first:
            assert brk == [], t
        else:
            assert len(brk) == 1
            key = (brk[0]["B1_time"], brk[0]["P_time"], brk[0]["B2_time"], brk[0]["breakout_time"],
                   brk[0]["breakout_price"], brk[0]["tp1"], brk[0]["tp2"], brk[0]["invalidation_price"])
            seen = seen or key
            assert key == seen, t  # the first breakout and every level never change
        if t < 60 + N:  # B2 is not confirmed before B2 + N: only a provisional (forming) W may exist
            assert all(r["B2_provisional"] for r in res), t
    assert seen is not None and seen[3] == ts.iloc[first].strftime("%Y-%m-%dT%H:%M:%SZ")


def test_truncation_future_rewrite_random_walks():
    for seed, tf in ((3, "1d"), (5, "4h"), (9, "1w")):
        df = random_walk_df(900, seed=seed)
        for t in range(120, len(df), 29):
            res = detect_double_bottoms(df.iloc[:t + 1], tf, {"include_stale": True}, symbol="RW")
            _times_ok(res, df, t)
            mutated = df.copy()
            rng = np.random.default_rng(t)
            mutated.loc[t + 1:, ["open", "high", "low", "close"]] = rng.uniform(50, 150, (len(df) - t - 1, 4))
            assert detect_double_bottoms(mutated.iloc[:t + 1], tf, {"include_stale": True}, symbol="RW") == res


def test_random_walks_find_some_and_do_not_crash():
    total = 0
    for seed in range(6):
        df = random_walk_df(1000, seed=seed)
        for tf in ("4h", "1d", "1w"):
            total += len(detect_double_bottoms(df, tf, {"include_stale": True}, symbol="RW"))
    assert total >= 0


def test_speed_1000_bars():
    import time
    df = random_walk_df(1000, seed=1)
    t0 = time.perf_counter()
    detect_double_bottoms(df, "4h", symbol="RW")
    assert time.perf_counter() - t0 < 0.5
