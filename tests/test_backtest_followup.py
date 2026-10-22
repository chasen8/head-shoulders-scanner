"""D42 follow-up analyses (backtest/followup.py): statistics, benchmark/excess math, random-entry control,
filter features use bars <= entry only, neckline-retest entry logic, same-bar conflicts, truncation.
No network: synthetic bars and tests/fixtures."""
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from backtest import engine as E
from backtest import followup as F
from backtest import followup_run as FR

FIXTURES = Path(__file__).parent / "fixtures"


def bars(rows):
    a = np.array(rows, dtype=float)
    return E.Bars(a[:, 0], a[:, 1], a[:, 2], a[:, 3])


def walk(n=400, seed=3, vol=0.03):
    rng = np.random.default_rng(seed)
    c = 100 * np.exp(np.cumsum(rng.normal(0, vol, n)))
    o = np.concatenate([[100.0], c[:-1]])
    h = np.maximum(o, c) * (1 + rng.uniform(0, 0.02, n))
    lo = np.minimum(o, c) * (1 - rng.uniform(0, 0.02, n))
    return E.Bars(o, h, lo, c), rng.uniform(1, 1e6, n)


def scramble(b: E.Bars, vol: np.ndarray, t: int, seed=9):
    """Every bar after t replaced by unrelated values (the future is rewritten)."""
    rng = np.random.default_rng(seed)
    k = b.n - t - 1
    c = b.close[t] * np.exp(np.cumsum(rng.normal(0, 0.08, k)))
    o = np.concatenate([[b.close[t]], c[:-1]])
    cat = lambda a, x: np.concatenate([a[:t + 1], x])
    return (E.Bars(cat(b.open, o), cat(b.high, np.maximum(o, c) * 1.05), cat(b.low, np.minimum(o, c) * 0.95),
                   cat(b.close, c)), cat(vol, rng.uniform(1, 1e7, k)))


def cut(b: E.Bars, m: int) -> E.Bars:
    return E.Bars(b.open[:m], b.high[:m], b.low[:m], b.close[:m])


# ------------------------------------------------------------------ statistics

def test_t_distribution_p_values():
    assert F.t_p_two_sided(2.0, 10) == pytest.approx(0.07339, abs=1e-4)
    assert F.t_p_two_sided(2.228, 10) == pytest.approx(0.05, abs=5e-4)
    assert F.t_p_two_sided(1.96, 1e6) == pytest.approx(0.05, abs=5e-4)
    assert F.t_p_two_sided(0.0, 5) == pytest.approx(1.0)
    m, t, p = F.one_sample_t([1.0, 2.0, 3.0, 4.0])
    assert m == pytest.approx(2.5) and t == pytest.approx(2.5 / (np.std([1, 2, 3, 4], ddof=1) / 2))
    assert 0 < p < 0.05
    assert F.one_sample_t([1.0]) == (1.0, None, None)


def test_profit_factor_and_welch():
    assert F.profit_factor([0.1, -0.05, 0.05]) == pytest.approx(3.0)
    assert F.profit_factor([0.1, 0.2]) is None
    assert F.welch_t([1, 2, 3, 4, 5], [1, 2, 3, 4, 5]) == pytest.approx(1.0)


# ------------------------------------------------------------------ benchmark / excess

def test_bench_return_is_asof_and_after_costs():
    day = 86_400 * 10 ** 9
    times = np.array([0, day, 2 * day, 3 * day], dtype=np.int64)
    vals = np.array([100.0, 110.0, 121.0, 90.0])
    # entry between bar 0 and bar 1 -> uses bar 0 (never the later bar 1); exit exactly at bar 2
    r = F.bench_return(times, vals, np.array([day // 2]), np.array([2 * day]))
    assert r[0] == pytest.approx(0.21)
    c = F.BENCH_COSTS["tw"]
    r2 = F.bench_return(times, vals, np.array([day // 2]), np.array([2 * day]), "tw")
    assert r2[0] == pytest.approx(121 * (1 - c["sell"]) / (100 * (1 + c["buy"])) - 1)
    assert np.isnan(F.bench_return(times, vals, np.array([-1]), np.array([day]))[0])  # before the first bar
    # excess = strategy net return - benchmark return over the same period
    net = np.array([0.05])
    assert (net - r)[0] == pytest.approx(0.05 - 0.21)


def test_sma_filter_uses_only_past_closes():
    v = np.linspace(100, 200, 300)
    r = F.sma_ratio(v, 200)
    assert np.isnan(r[198]) and np.isfinite(r[199])
    assert r[250] == pytest.approx(v[250] / v[51:251].mean())
    v2 = v.copy()
    v2[251:] = 1.0  # rewrite the future
    np.testing.assert_allclose(F.sma_ratio(v2, 200)[:251], r[:251])
    times = np.arange(300, dtype=np.int64) * 10
    up = F.above_sma_asof(times, v, np.array([5, 1985, 2505]), 200)
    assert np.isnan(up[0]) and np.isnan(up[1]) and up[2] == 1.0
    assert F.above_sma_asof(times, v[::-1].copy(), np.array([2505]), 200)[0] == 0.0


# ------------------------------------------------------------------ random-entry control

def test_bracket_sim_equals_engine_simulate_single():
    b, _ = walk(600, seed=5)
    rng = np.random.default_rng(1)
    for _ in range(300):
        t = int(rng.integers(0, b.n - 2))
        e = b.close[t]
        stop, tp, mh = e * (1 - rng.uniform(0.01, 0.2)), e * (1 + rng.uniform(0.01, 0.4)), int(rng.integers(1, 130))
        assert F.bracket_sim(b, t, stop, tp, mh) == E.simulate_single(b, t, stop, tp, mh, 1)


def test_fixed_hold_and_random_bracket():
    b = bars([(10, 10, 10, 10), (10, 12, 9, 11), (11, 13, 10, 12), (12, 12, 8, 9)])
    r = F.fixed_hold_returns(b.close, np.array([0, 1]), 2, "crypto")
    np.testing.assert_allclose(r, [E.net_return(10, 12, "crypto"), E.net_return(11, 9, "crypto")])
    rets, wins = F.random_bracket(b, [0], 0.15, 0.15, 5, "crypto")  # stop 8.5, tp 11.5 -> bar 2 high 13 -> tp
    assert wins == [True] and rets[0] == pytest.approx(E.net_return(10, 11.5, "crypto"))
    rets, wins = F.random_bracket(b, [2], 0.01, 0.5, 5, "crypto")  # stop 11.88 hit on bar 3 (open 12 -> fill at stop)
    assert wins == [False] and rets[0] == pytest.approx(E.net_return(12, 12 * 0.99, "crypto"))
    assert F.random_bracket(b, [2], 0.5, 0.5, 5, "crypto") == ([], [])  # data ends -> 'open' dropped
    assert F.draw_starts(np.random.default_rng(0), 5, 4, 3).size == 0


# ------------------------------------------------------------------ filter features: bars <= t only

def test_features_ignore_bars_after_entry():
    b, vol = walk(500, seed=11)
    ls_i, h_i, rs_i, brk_i, t = 300, 330, 360, 364, 366

    def feats(bb, vv):
        return (F.prior_decline(bb.high, bb.low, ls_i, 250), F.volume_contraction(vv, ls_i, h_i, rs_i, t, 3),
                F.volume_surge(vv, t, 20), F.volume_surge(vv, brk_i, 20))
    base = feats(b, vol)
    for s in range(3):
        assert feats(*scramble(b, vol, t, seed=s)) == base
    # an RS window that would reach past t is clipped at t
    assert F.mean_volume(vol, [t], 3, t) == pytest.approx(vol[t - 3:t + 1].mean())
    vol2 = vol.copy()
    vol2[t + 1:] *= 1000
    assert F.volume_contraction(vol2, ls_i, h_i, t - 1, t, 3) == F.volume_contraction(vol, ls_i, h_i, t - 1, t, 3)


def test_prior_decline_values():
    high = np.array([10, 20, 15, 12, 11, 9, 8], dtype=float)
    low = high - 1
    d = F.prior_decline(high, low, 6, 10)
    assert d["decline_bars"] == 5 and d["decline_depth"] == pytest.approx(7 / 20 - 1)
    assert d["decline_at_edge"] is False
    d = F.prior_decline(high, low, 6, 4)  # window [2, 6): peak 15 at bar 2 = window edge
    assert d["decline_bars"] == 4 and d["decline_at_edge"] is True


def test_bins_and_consistency():
    edges, labels = [0, 1.0, 2.0, 1e9], ["<1", "1–2", "≥2"]
    assert [F.bin_label(v, edges, labels) for v in (0.5, 1.0, 5.0, None, float("nan"))] == \
        ["<1", "1–2", "≥2", "未知", "未知"]
    assert FR.consistency([(2.0, 0.5), (1.0, 0.1)]) == "一致較好"
    assert FR.consistency([(-2.0, -0.5), (-1.0, -0.1)]) == "一致較差"
    assert FR.consistency([(2.0, 0.5), (-1.0, 0.1)]) == "不一致"
    assert FR.consistency([(None, None), (1.0, 0.1)]) == "樣本不足"


# ------------------------------------------------------------------ neckline retest entry

def test_retest_limit_fill_rules():
    # t=0 signal close 110, neckline 100, RS 90
    b = bars([(105, 111, 104, 110), (110, 112, 104, 111), (108, 109, 100.5, 102), (99, 101, 97, 100)])
    assert F.retest_fill(b, 0, 100, 90, 0.01, 5) == (2, 101.0, "filled")  # low 100.5 <= 101 -> limit fill
    assert F.retest_fill(b, 0, 100, 90, 0.0, 5) == (3, 99.0, "filled")  # opens below the limit -> open price
    assert F.retest_fill(b, 0, 100, 90, 0.0, 2) == (None, None, "no_retest")
    assert F.retest_fill(b, 0, 100, 90, 0.0, 10)[2] == "filled"
    assert F.retest_fill(b, 0, 100, 99, -0.02, 10)[2] == "bad_level"  # limit 98 <= RS 99
    b2 = bars([(105, 111, 104, 110), (110, 112, 104, 111)])
    assert F.retest_fill(b2, 0, 100, 90, 0.0, 5) == (None, None, "pending")  # data ended before the wait


def test_retest_close_confirm_cancel_and_entry():
    b = bars([(105, 111, 104, 110), (110, 111, 100.5, 99), (99, 104, 98, 103)])
    # bar 1 touches but closes below the neckline -> not confirmed; bar 2 touches and closes above -> enter at close
    assert F.retest_fill(b, 0, 100, 90, 0.01, 5, "close_confirm") == (2, 103.0, "filled")
    b2 = bars([(105, 111, 104, 110), (110, 111, 85, 88), (88, 104, 87, 103)])
    assert F.retest_fill(b2, 0, 100, 90, 0.01, 5, "close_confirm") == (None, None, "cancel_rs")


def test_fill_bar_same_bar_conflicts():
    # limit fill (open above the limit): low also hits the stop -> stop at the stop price
    b = bars([(105, 111, 104, 110), (106, 130, 85, 100)])
    r = F.retest_trade(b, 0, 100, 90, 120, 0.0, 5, 50, "crypto")
    assert r["fill_price"] == 100 and r["exit_reason"] == "stop" and r["exit_price"] == 90 and r["exit_bar"] == 1
    # limit fill with a high above the target on the same bar: the high may have come first -> no target on bar 1
    b = bars([(105, 111, 104, 110), (106, 125, 99, 104), (104, 105, 95, 100)])
    r = F.retest_trade(b, 0, 100, 90, 120, 0.0, 5, 1, "crypto")
    assert r["exit_reason"] == "timeout" and r["exit_bar"] == 2
    # filled at the open (gap down to the limit): the target later in that bar counts
    b = bars([(105, 111, 104, 110), (99, 125, 98, 104)])
    r = F.retest_trade(b, 0, 100, 90, 120, 0.0, 5, 50, "crypto")
    assert r["fill_price"] == 99 and r["exit_reason"] == "tp" and r["exit_price"] == 120
    # after the fill bar: a bar touching both stop and target -> stop (D40)
    b = bars([(105, 111, 104, 110), (106, 107, 99, 104), (104, 125, 85, 100)])
    r = F.retest_trade(b, 0, 100, 90, 120, 0.0, 5, 50, "crypto")
    assert r["exit_reason"] == "stop" and r["exit_bar"] == 2
    assert r["net_ret"] == pytest.approx(E.net_return(100, 90, "crypto"))
    assert r["R"] == pytest.approx(r["net_ret"] / 0.1)
    # gap below RS at the fill bar: filled at the open and stopped at that same price (cost-only loss)
    b = bars([(105, 111, 104, 110), (88, 95, 87, 94)])
    r = F.retest_trade(b, 0, 100, 90, 120, 0.0, 5, 50, "tw")
    assert r["fill_price"] == 88 and r["exit_price"] == 88 and r["exit_reason"] == "stop"


def test_retest_entry_does_not_look_ahead():
    """The fill decision up to bar m only depends on bars <= m: truncating or rewriting the future never changes
    whether (and where / at what price) the order filled by bar m."""
    b, vol = walk(300, seed=21, vol=0.02)
    t = 120
    checked = 0
    for x in (0.0, 0.01, 0.03):
        for mode in ("limit", "close_confirm"):
            level = b.close[t] * 0.97
            rs = level * 0.9
            full = F.retest_fill(b, t, level, rs, x, 20, mode)
            for m in range(t + 1, t + 22):
                part = F.retest_fill(cut(b, m + 1), t, level, rs, x, 20, mode)
                if full[0] is not None and full[0] <= m:
                    assert part == full
                else:
                    assert part[0] is None
                alt = F.retest_fill(scramble(b, vol, m, seed=m)[0], t, level, rs, x, 20, mode)
                if full[0] is not None and full[0] <= m:
                    assert alt == full
                checked += 1
    assert checked > 100


def test_retest_on_real_fixture_signal_is_causal():
    df = pd.read_csv(FIXTURES / "BTCUSDT_binance_spot_1d.csv", encoding="utf-8").iloc[:700].reset_index(drop=True)
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    sigs = [s for s in E.ihs_signals(df, "1d", "crypto", symbol="BTC") if s["mode"] == "A"]
    assert sigs
    b = E.Bars.of(df)
    vol = df["volume"].to_numpy(float)
    for s in sigs[:3]:
        t = s["t"]
        full = F.retest_trade(b, t, s["breakout_level"], s["RS_price"], s["tp1"], 0.01, 20, 120, "crypto")
        if full["fill_bar"] is None:
            continue
        f = full["fill_bar"]
        alt = F.retest_fill(scramble(b, vol, f)[0], t, s["breakout_level"], s["RS_price"], 0.01, 20)
        assert alt == (f, full["fill_price"], "filled")
        assert F.retest_fill(cut(b, f + 1), t, s["breakout_level"], s["RS_price"], 0.01, 20) == alt
        assert full["exit_bar"] >= f


def test_breakout_trade_matches_engine():
    b, _ = walk(400, seed=8)
    t = 100
    rs, tp = b.close[t] * 0.9, b.close[t] * 1.2
    bo = F.breakout_trade(b, t, rs, tp, 120, "tw")
    ei, px, reason = E.simulate_single(b, t, rs, tp, 120, 1)
    assert bo["exit_bar"] == ei and bo["net_ret"] == pytest.approx(E.net_return(b.close[t], px, "tw"))
    assert F.breakout_trade(b, t, b.close[t] * 1.01, tp, 120, "tw")["status"] == "skip"
