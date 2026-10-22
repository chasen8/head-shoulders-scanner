"""D43 exit / stop management (backtest/exits.py): trigger rules, same-bar conflicts, gaps, the trailing stop only
moves up, truncation (no look-ahead), the one-position loop and basic properties of the equity simulation.
No network: synthetic bars only."""
import math

import numpy as np
import pandas as pd
import pytest

from backtest import engine as E
from backtest import exits as X
from ihs_scanner.detector import wilder_atr


def bars(rows):
    a = np.array(rows, dtype=float)
    return E.Bars(a[:, 0], a[:, 1], a[:, 2], a[:, 3])


def walk(n=400, seed=3, vol=0.03, drift=0.0):
    rng = np.random.default_rng(seed)
    c = 100 * np.exp(np.cumsum(rng.normal(drift, vol, n)))
    o = np.concatenate([[100.0], c[:-1]]) * (1 + rng.normal(0, 0.005, n))
    h = np.maximum(o, c) * (1 + rng.uniform(0, 0.02, n))
    lo = np.minimum(o, c) * (1 - rng.uniform(0, 0.02, n))
    return E.Bars(o, h, lo, c)


def frame(b: E.Bars) -> pd.DataFrame:
    return pd.DataFrame({"open": b.open, "high": b.high, "low": b.low, "close": b.close})


def cut(b: E.Bars, m: int) -> E.Bars:
    return E.Bars(b.open[:m], b.high[:m], b.low[:m], b.close[:m])


def scramble(b: E.Bars, after: int, seed=9) -> E.Bars:
    rng = np.random.default_rng(seed)
    k = b.n - after - 1
    c = b.close[after] * np.exp(np.cumsum(rng.normal(0, 0.08, k)))
    o = np.concatenate([[b.close[after]], c[:-1]])
    cat = lambda a, x: np.concatenate([a[:after + 1], x])
    return E.Bars(cat(b.open, o), cat(b.high, np.maximum(o, c) * 1.05), cat(b.low, np.minimum(o, c) * 0.95),
                  cat(b.close, c))


TRAIL_RULES = [r for r in X.RULES if r.engine_exit is None]


# ------------------------------------------------------------------ rule list

def test_rule_keys_unique_and_cover_the_spec():
    keys = [r.key for r in X.RULES]
    assert len(keys) == len(set(keys))
    for k in ("TP1", "TP2", "HALF", "ATR2_C", "ATR3_C", "ATR4_C", "ATR3_L", "BE_ATR3_C", "LOWN_L",
              "HALF_near_ATR3_C", "HALF_R2_ATR3_C", "TIME_ATR3_C", "TIME_BE_ATR3_C", "ATR3_C_LH"):
        assert k in X.RULE_BY_KEY
    assert X.RULE_BY_KEY["ATR3_C_LH"].max_hold("1d") == 250 and X.RULE_BY_KEY["ATR3_C"].max_hold("1w") == 52


# ------------------------------------------------------------------ generic simulator vs the D40 engine

def test_fixed_target_equals_engine_simulate_single():
    b = walk(600, seed=5)
    rng = np.random.default_rng(1)
    for _ in range(300):
        t = int(rng.integers(0, b.n - 2))
        e = float(b.close[t])
        stop, tp, mh = e * (1 - rng.uniform(0.01, 0.2)), e * (1 + rng.uniform(0.01, 0.4)), int(rng.integers(1, 130))
        (f, i, px, r), = X.simulate(b, t, e, stop, mh, legs=((1.0, tp),))
        assert (i, px, r) == E.simulate_single(b, t, stop, tp, mh, 1) and f == 1.0


def test_baseline_rules_call_the_engine():
    b = walk(400, seed=8)
    t, e = 50, float(b.close[50])
    stop, tp1, tp2 = e * 0.9, e * 1.1, e * 1.25
    legs = X.run_rule(X.RULE_BY_KEY["TP1"], b, t, e, stop, tp1, tp2, None, "1d")
    assert [(i, p, r) for _f, i, p, r in legs] == [E.simulate_single(b, t, stop, tp1, 120, 1)]
    legs = X.run_rule(X.RULE_BY_KEY["HALF"], b, t, e, stop, tp1, tp2, None, "1d")
    assert [(i, p, r) for _f, i, p, r in legs] == E.simulate_half(b, t, e, stop, tp1, tp2, 120, 1)
    assert X.run_rule(X.RULE_BY_KEY["TP1"], b, t, e, stop, e * 0.99, tp2, None, "1d") == "target_reached_at_entry"


# ------------------------------------------------------------------ triggers, conflicts, gaps

def test_same_bar_stop_and_target_counts_as_stop():
    b = bars([(10, 10, 10, 10), (10, 13, 8, 12)])  # bar 1 reaches the target 12 and the stop 9
    (_f, i, px, r), = X.simulate(b, 0, 10.0, 9.0, 5, legs=((1.0, 12.0),))
    assert (i, px, r) == (1, 9.0, "stop")
    legs = X.simulate(b, 0, 10.0, 9.0, 5, legs=((0.5, 12.0), (0.5, None)), atr=np.ones(2), atr_k=3.0)
    assert [(i, px, r) for _f, i, px, r in legs] == [(1, 9.0, "stop"), (1, 9.0, "stop")]


def test_gap_through_stop_fills_at_open():
    b = bars([(10, 10, 10, 10), (10, 11, 9.8, 10.5), (8.5, 9, 8, 8.8)])
    (_f, i, px, r), = X.simulate(b, 0, 10.0, 9.0, 5)
    assert (i, px, r) == (2, 8.5, "stop")
    # intrabar trailing stop gapped through: fills at the open as well
    b2 = bars([(10, 10, 10, 10), (10, 15, 10, 15), (12, 12.5, 11.5, 12)])
    (_f, i, px, r), = X.simulate(b2, 0, 10.0, 9.0, 5, atr=np.full(3, 1.0), atr_k=2.0, trigger="low")
    assert (i, px, r) == (2, 12.0, "trail")  # trail = 15 - 2*1 = 13 > open 12


def test_close_trigger_vs_intrabar_trigger():
    # bar 1 closes at 15 (trail 15 - 2*1 = 13 from bar 2 on); bar 2 dips to 12.5 but closes 13.5; bar 3 closes 12.8
    rows = [(10, 10, 10, 10), (10, 15, 10, 15), (14, 14.5, 12.5, 13.5), (13.5, 13.6, 12.6, 12.8), (13, 13, 13, 13)]
    b, atr = bars(rows), np.full(5, 1.0)
    (_f, i, px, r), = X.simulate(b, 0, 10.0, 9.0, 10, atr=atr, atr_k=2.0, trigger="close")
    assert (i, px, r) == (3, 12.8, "trail")
    (_f, i, px, r), = X.simulate(b, 0, 10.0, 9.0, 10, atr=atr, atr_k=2.0, trigger="low")
    assert (i, px, r) == (2, 13.0, "trail")
    # the initial stop stays intrabar in the close version
    b2 = bars([(10, 10, 10, 10), (10, 10.2, 8.9, 9.5)])
    (_f, i, px, r), = X.simulate(b2, 0, 10.0, 9.0, 10, atr=np.full(2, 5.0), atr_k=2.0, trigger="close")
    assert (i, px, r) == (1, 9.0, "stop")


def test_trailing_stop_only_moves_up_and_uses_previous_bar():
    b = walk(500, seed=21, vol=0.04, drift=0.002)
    atr = wilder_atr(frame(b), 14)
    for trig in ("close", "low"):
        for k in (2.0, 3.0):
            trace = []
            t = 30
            e = float(b.close[t])
            X.simulate(b, t, e, e * 0.8, 400, atr=atr, atr_k=k, trigger=trig, trace=trace)
            levels = [tr for _i, tr, _s in trace]
            assert all(b2 >= a for a, b2 in zip(levels, levels[1:]))  # never moves down
            for i, tr, s_low in trace:
                cand = max(b.close[t:i]) - k * atr[i - 1]
                assert tr >= cand - 1e-12 and s_low >= e * 0.8
    # an ATR spike after the high must not lower the stop
    rows = [(10, 10, 10, 10), (10, 20, 10, 20)] + [(19, 19.5, 18.5, 19)] * 3
    atr = np.array([1.0, 1.0, 3.0, 5.0, 5.0])
    trace = []
    X.simulate(bars(rows), 0, 10.0, 9.0, 10, atr=atr, atr_k=2.0, trigger="close", trace=trace)
    assert [round(tr, 6) for _i, tr, _s in trace] == [8.0, 18.0, 18.0, 18.0]


def test_breakeven_activates_on_close_from_the_next_bar():
    # risk 1 -> +1R = 11. bar 1 closes 11.2 (activation); bar 1 itself dipped to 9.5 (> stop 9, BE not active yet)
    rows = [(10, 10, 10, 10), (10, 11.5, 9.5, 11.2), (11, 11.1, 9.8, 10.5)]
    (_f, i, px, r), = X.simulate(bars(rows), 0, 10.0, 9.0, 10, atr=np.full(3, 1.0), atr_k=3.0, be_R=1.0,
                                 trail_after_be=True, trigger="close")
    assert (i, px, r) == (2, 10.0, "breakeven")
    # never reaching +1R by close -> no breakeven and no trail (trail_after_be)
    rows = [(10, 10, 10, 10), (10, 11.6, 9.6, 10.9), (10.9, 10.95, 9.7, 9.8), (9.8, 9.9, 9.5, 9.6)]
    trace = []
    legs = X.simulate(bars(rows), 0, 10.0, 9.0, 3, atr=np.full(4, 0.1), atr_k=3.0, be_R=1.0, trail_after_be=True,
                      trigger="low", trace=trace)
    assert legs[0][3] == "timeout" and all(tr == -math.inf for _i, tr, _s in trace)


def test_chandelier_lowest_low_of_previous_n_bars():
    rows = [(10, 10.5, 9.5, 10), (10, 11, 9.8, 10.8), (10.8, 12, 10.6, 11.8), (11.8, 12.5, 11.4, 12.2),
            (12.2, 12.3, 10.5, 10.7)]
    b = bars(rows)
    trace = []
    (_f, i, px, r), = X.simulate(b, 2, 11.8, 9.0, 10, low_n=2, trigger="low", trace=trace)
    # bar 3: min(low[1], low[2]) = 9.8; bar 4: min(low[2], low[3]) = 10.6 -> low 10.5 hits it
    assert [round(tr, 6) for _i, tr, _s in trace] == [9.8, 10.6]
    assert (i, px, r) == (4, 10.6, "trail")


def test_time_stop():
    flat = [(10, 10.2, 9.8, 10.1)] * 25
    b = bars([(10, 10, 10, 10)] + flat)
    (_f, i, px, r), = X.simulate(b, 0, 10.0, 9.0, 120, time_n=20)
    assert (i, px, r) == (20, 10.1, "time")  # +0.5R = 10.5 never reached by a close
    b2 = bars([(10, 10, 10, 10), (10, 10.7, 10, 10.6)] + flat)  # a close >= 10.5 inside the window
    (_f, i, _px, r), = X.simulate(b2, 0, 10.0, 9.0, 24, time_n=20)
    assert r == "timeout" and i == 24


def test_partial_target_then_trail():
    rows = [(10, 10, 10, 10), (10, 12.5, 10, 12.4), (12.4, 13, 12.3, 12.9), (12.9, 13.0, 11.0, 11.2)]
    legs = X.simulate(bars(rows), 0, 10.0, 9.0, 10, atr=np.full(4, 0.5), legs=((0.5, 12.0), (0.5, None)),
                      atr_k=3.0, trigger="close")
    assert legs[0] == (0.5, 1, 12.0, "tp")
    assert legs[1] == (0.5, 3, 11.2, "trail")  # trail = 12.9 - 1.5 = 11.4 from bar 3 on
    ret = X.legs_return(legs, 10.0, "tw")
    assert ret == pytest.approx(0.5 * E.net_return(10, 12, "tw") + 0.5 * E.net_return(10, 11.2, "tw"))
    assert X.final_reason(legs) == "trail"


# ------------------------------------------------------------------ no look-ahead

@pytest.mark.parametrize("seed", [1, 2, 3])
def test_truncation_does_not_change_exits(seed):
    b = walk(450, seed=seed, vol=0.035, drift=0.001)
    rng = np.random.default_rng(seed)
    for _ in range(25):
        t = int(rng.integers(20, 300))
        e = float(b.close[t])
        stop0 = e * (1 - rng.uniform(0.03, 0.15))
        tp1, tp2 = e * (1 + rng.uniform(0.05, 0.2)), e * (1 + rng.uniform(0.2, 0.5))
        for rule in X.RULES:
            full = X.run_rule(rule, b, t, e, stop0, tp1, tp2, wilder_atr(frame(b), 14), "1d")
            last = max(i for _f, i, _p, _r in full)
            for m in (t + 1, t + 5, last, last + 1, b.n - 1):
                if m <= t or m > b.n - 1:
                    continue
                bc = cut(b, m + 1)
                part = X.run_rule(rule, bc, t, e, stop0, tp1, tp2, wilder_atr(frame(bc), 14), "1d")
                if X.final_reason(full) != "open" and last <= m:
                    assert part == full, (rule.key, t, m)
                else:  # not decided yet by bar m -> still open there, with legs already closed unchanged
                    assert X.final_reason(part) == "open"
                    for pl, fl in zip(part, full):
                        if pl[3] != "open":
                            assert pl == fl
            if X.final_reason(full) != "open" and last < b.n - 1:
                bs = scramble(b, last, seed=seed)
                assert X.run_rule(rule, bs, t, e, stop0, tp1, tp2, wilder_atr(frame(bs), 14), "1d") == full


def test_series_trades_one_position_at_a_time():
    b = walk(400, seed=4)
    atr = wilder_atr(frame(b), 14)
    sigs = []
    for t in (30, 31, 60, 200):
        e = float(b.close[t])
        sigs.append({"t": t, "pattern_id": f"p{t}", "RS_price": e * 0.9, "tp1": e * 1.1, "tp2": e * 1.3})
    sigs.append({"t": 250, "pattern_id": "bad", "RS_price": float("nan"), "tp1": 1.0, "tp2": 2.0})
    sigs.append({"t": 260, "pattern_id": "above", "RS_price": float(b.close[260]) * 1.01, "tp1": 1e9, "tp2": 1e9})
    for rule in (X.RULE_BY_KEY["TP1"], X.RULE_BY_KEY["ATR3_C"], X.RULE_BY_KEY["HALF_near_ATR3_L"]):
        trades, skips = X.series_trades(b, sigs, rule, "1d", "tw", atr)
        for a, c in zip(trades, trades[1:]):
            assert c["t"] > a["exit_bar"]
        assert skips.get("missing_level") == 1 and skips.get("stop_beyond_entry") == 1
        assert sum(skips.values()) + len(trades) == len(sigs)
        for tr in trades:
            assert tr["R"] == pytest.approx(tr["net_ret"] / tr["risk_pct"])


def test_rr_stats_and_losing_streak():
    rr = X.rr_stats([2.0, 4.0, -1.0, -0.5, float("nan")])
    assert rr["avg_win_R"] == 3.0 and rr["avg_loss_R"] == 0.75 and rr["rr"] == 4.0
    assert X.rr_stats([1.0])["rr"] is None
    assert X.max_losing_streak([0.1, -1, -1, 0.0, -1, -1, -1, 0.2]) == 3


# ------------------------------------------------------------------ equity simulation

DAY = 86_400 * 10 ** 9
NO_COST = {"buy": 0.0, "sell": 0.0}


def test_equity_without_trades_tracks_the_benchmark_or_cash():
    n = 300
    bench = 100 * np.exp(np.cumsum(np.random.default_rng(0).normal(0.0005, 0.01, n)))
    px = np.ones((1, n))
    r = X.equity_curve(px, [], bench=bench, costs=NO_COST, bench_costs={"buy": 0.001, "sell": 0.001})
    np.testing.assert_allclose(r["equity"], bench / bench[0] * 0.999)
    r = X.equity_curve(px, [], bench=None, costs=NO_COST)
    np.testing.assert_allclose(r["equity"], np.ones(n))
    p = X.perf(np.array([1.0, 1.2, 0.9, 1.5]), np.array([0, 1, 2, 3]) * 365.25 / 3 * DAY)
    assert p["mdd"] == pytest.approx(0.9 / 1.2 - 1) and p["cagr"] == pytest.approx(0.5) and p["total"] == 0.5


def test_equity_single_trade_risk_sizing_and_marking():
    n = 10
    px = np.array([[10, 10, 11, 12, 12, 12, 12, 12, 12, 12]], dtype=float)
    tr = X.SimTrade(sym=0, d0=1, entry=10.0, risk_pct=0.05, legs=[(1.0, 4, 12.0)])
    r = X.equity_curve(px, [tr], bench=None, risk=0.01, costs=NO_COST)
    # 1% risk / 5% stop distance -> 20% of equity; +20% on the position -> +4% on the account
    assert r["equity"][1] == pytest.approx(1.0) and r["exposure"][1] == pytest.approx(0.2)
    assert r["equity"][2] == pytest.approx(1.02)
    assert r["equity"][-1] == pytest.approx(1.04) and r["exposure"][-1] == 0.0
    c = {"buy": 0.001, "sell": 0.002}
    r2 = X.equity_curve(px, [tr], bench=None, risk=0.01, costs=c)
    assert r2["equity"][-1] == pytest.approx(0.8 + 0.2 / 1.001 * 1.2 * 0.998)
    # half legs exit on different days
    tr2 = X.SimTrade(sym=0, d0=1, entry=10.0, risk_pct=0.05, legs=[(0.5, 2, 11.0), (0.5, 4, 12.0)])
    r3 = X.equity_curve(px, [tr2], bench=None, risk=0.01, costs=NO_COST)
    assert r3["equity"][-1] == pytest.approx(0.8 + 0.1 * 1.1 + 0.1 * 1.2)


def test_equity_max_positions_no_leverage_and_open_trades():
    n = 50
    rng = np.random.default_rng(3)
    px = 10 * np.exp(np.cumsum(rng.normal(0, 0.02, (30, n)), axis=1))
    trades = [X.SimTrade(sym=k, d0=5, entry=float(px[k, 5]), risk_pct=0.02, legs=[(1.0, 20 + k % 5, float(px[k, 20 + k % 5]))],
                         key=(k,)) for k in range(30)]
    trades.append(X.SimTrade(sym=0, d0=30, entry=float(px[0, 30]), risk_pct=0.01, legs=[(1.0, -1, 0.0)], key=(99,)))
    r = X.equity_curve(px, trades, bench=None, risk=0.01, max_pos=10, costs=NO_COST)
    # 1% / 2% -> 50% of equity each: only 2 fit in the cash on day 5 (the 2nd is scaled down), the rest are skipped
    assert r["taken"] == 3 and r["skipped_full"] == 28 and r["scaled"] >= 1
    assert np.nanmax(r["exposure"]) <= 1.0 + 1e-12
    assert np.isfinite(r["equity"]).all() and r["exposure"][-1] > 0  # the open trade is still marked at the end
    r = X.equity_curve(px, trades, bench=None, risk=0.001, max_pos=4, costs=NO_COST)
    assert r["taken"] == 5 and r["skipped_full"] == 26  # 4 on day 5 (max_pos), the late one on day 30
    order = np.arange(len(trades))[::-1]
    r2 = X.equity_curve(px, trades, bench=None, risk=0.001, max_pos=4, costs=NO_COST, order=order)
    assert r2["taken"] == 5 and not np.allclose(r2["equity"], r["equity"])  # a different same-day order
