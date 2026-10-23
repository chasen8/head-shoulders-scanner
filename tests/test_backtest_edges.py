"""D44 edges (backtest/edges.py): regime switches, volatility, relative strength, the extended equity account and
Sharpe statistics. Truncation tests: every feature at day d only uses data up to d. No network: synthetic only."""
import math

import numpy as np
import pandas as pd
import pytest

from backtest import edges as G
from backtest import exits as X

DAY = 86_400 * 10 ** 9
NO_COST = {"buy": 0.0, "sell": 0.0}
BC = {"buy": 0.001425, "sell": 0.002425}
TC = {"buy": 0.001425, "sell": 0.004425}


def walk(rows, n, seed=1, vol=0.02, drift=0.0003):
    rng = np.random.default_rng(seed)
    return 10 * np.exp(np.cumsum(rng.normal(drift, vol, (rows, n)), axis=1))


def days(n, start="2018-01-01"):
    return pd.date_range(start, periods=n, freq="D", tz="UTC").as_unit("ns").asi8


def random_trades(px, k=60, seed=4):
    rng = np.random.default_rng(seed)
    out = []
    n = px.shape[1]
    for j in range(k):
        s = int(rng.integers(px.shape[0]))
        d0 = int(rng.integers(5, n - 30))
        d1 = d0 + int(rng.integers(1, 25))
        legs = [(1.0, d1, float(px[s, d1]))] if j % 3 else [(0.5, d0 + 1, float(px[s, d0 + 1])),
                                                           (0.5, d1, float(px[s, d1]))]
        if j == 7:
            legs = [(1.0, -1, 0.0)]  # still open at the end
        out.append(X.SimTrade(sym=s, d0=d0, entry=float(px[s, d0]), risk_pct=float(rng.uniform(0.02, 0.2)),
                              legs=legs, key=(j,)))
    return out


# ------------------------------------------------------------------ equity_ext == D43 equity_curve

@pytest.mark.parametrize("use_bench", [True, False])
@pytest.mark.parametrize("seed", [None, 1])
def test_equity_ext_defaults_reproduce_d43(use_bench, seed):
    n = 260
    px = walk(8, n)
    bench = walk(1, n, seed=9)[0]
    trades = random_trades(px)
    order = None if seed is None else np.random.default_rng(seed).permutation(len(trades))
    a = X.equity_curve(px, trades, bench=bench if use_bench else None, risk=0.01, max_pos=4, costs=TC,
                       bench_costs=BC, start=3, order=order)
    b = G.equity_ext(px, trades, bench=bench if use_bench else None, risk=0.01, max_pos=4, costs=TC,
                     bench_costs=BC, start=3, order=order)
    np.testing.assert_allclose(b["equity"][3:], a["equity"][3:], rtol=1e-12)
    np.testing.assert_allclose(b["exposure"], a["exposure"], rtol=1e-10, atol=1e-14)
    assert (b["taken"], b["scaled"], b["skipped_full"]) == (a["taken"], a["scaled"], a["skipped_full"])
    # an all-True switch, idle weight 1 and multiplier 1 change nothing either
    c = G.equity_ext(px, trades, bench=bench if use_bench else None, risk=0.01, max_pos=4, costs=TC, bench_costs=BC,
                     start=3, order=order, allow=np.ones(n, bool), exit_on_off=True, idle_w=np.ones(n),
                     risk_mult=np.ones(n))
    np.testing.assert_allclose(c["equity"][3:], a["equity"][3:], rtol=1e-12)


def test_regime_off_blocks_entries_and_exit_mode_closes_at_close():
    n = 12
    px = np.array([[10, 10, 11, 12, 13, 13, 9, 9, 9, 9, 9, 9]], dtype=float)
    tr = [X.SimTrade(sym=0, d0=1, entry=10.0, risk_pct=0.05, legs=[(1.0, 10, 9.0)], key=(0,)),
          X.SimTrade(sym=0, d0=5, entry=13.0, risk_pct=0.05, legs=[(1.0, 8, 9.0)], key=(1,))]
    allow = np.ones(n, bool)
    allow[4:] = False
    r = G.equity_ext(px, tr, bench=None, risk=0.01, costs=NO_COST, allow=allow)
    assert r["taken"] == 1 and r["skipped_regime"] == 1 and r["forced_exits"] == 0
    assert r["equity"][-1] == pytest.approx(0.8 + 0.2 * 0.9)  # held to its own exit at 9
    r = G.equity_ext(px, tr, bench=None, risk=0.01, costs=NO_COST, allow=allow, exit_on_off=True)
    assert r["forced_exits"] == 1 and r["exposure"][4] == 0.0
    assert r["equity"][-1] == pytest.approx(0.8 + 0.2 * 1.3)  # closed at day 4's close (13)


def test_idle_weight_switches_between_benchmark_and_cash_with_costs():
    n = 10
    bench = np.array([100, 110, 121, 100, 90, 80, 88, 96.8, 100, 110], dtype=float)
    w = np.array([1, 1, 1, 0, 0, 0, 1, 1, 1, 1], dtype=float)
    r = G.equity_ext(np.ones((1, n)), [], bench=bench, costs=NO_COST, bench_costs=NO_COST, idle_w=w)
    # in the benchmark days 0-2, sold at day 3's close (100), cash until day 5, back in at day 6's close (88)
    assert r["equity"][2] == pytest.approx(1.21)
    assert r["equity"][3] == pytest.approx(1.0) and r["equity"][5] == pytest.approx(1.0)
    assert r["equity"][9] == pytest.approx(110 / 88)
    rc = G.equity_ext(np.ones((1, n)), [], bench=bench, costs=NO_COST, bench_costs=BC, idle_w=w)
    assert rc["equity"][9] == pytest.approx((1 - BC["buy"]) * 1.0 * (1 - BC["sell"]) * (1 - BC["buy"]) * 110 / 88)
    # a partial weight inside the band is not rebalanced
    rb = G.equity_ext(np.ones((1, n)), [], bench=bench, costs=NO_COST, bench_costs=NO_COST,
                      idle_w=np.full(n, 0.95), band=0.1)
    assert rb["bench_frac"][0] == pytest.approx(0.95) and rb["bench_frac"][1] > 0.95


def test_weights_and_risk_multiplier_scale_positions():
    n = 6
    px = np.array([[10, 10, 11, 11, 11, 11]], dtype=float)
    tr = [X.SimTrade(sym=0, d0=1, entry=10.0, risk_pct=0.05, legs=[(1.0, 3, 11.0)])]
    r = G.equity_ext(px, tr, bench=None, costs=NO_COST, weights=np.array([0.3]))
    assert r["exposure"][1] == pytest.approx(0.3)
    r = G.equity_ext(px, tr, bench=None, costs=NO_COST, weights=np.array([0.9]), max_w=0.4)
    assert r["exposure"][1] == pytest.approx(0.4)
    r = G.equity_ext(px, tr, bench=None, costs=NO_COST, risk_mult=np.full(n, 0.5))
    assert r["exposure"][1] == pytest.approx(0.1)  # 1% / 5% = 20%, halved


# ------------------------------------------------------------------ truncation / no look-ahead

def test_equity_ext_does_not_look_ahead():
    n = 200
    px = walk(6, n, seed=5)
    bench = walk(1, n, seed=6)[0]
    trades = [t for t in random_trades(px, seed=7) if all(dx < 0 or dx < 120 for _f, dx, _p in t.legs)]
    allow = G.sma_regime(bench, 20) > 0.5
    kw = dict(bench=bench, costs=TC, bench_costs=BC, start=25, max_pos=3, exit_on_off=True,
              idle_w=allow.astype(float) * 0.7, band=0.05, risk_mult=np.full(n, 0.8))
    a = G.equity_ext(px, trades, allow=allow, **kw)
    d = 120
    px2, b2 = px.copy(), bench.copy()
    px2[:, d + 1:] *= np.random.default_rng(1).uniform(0.3, 3.0, px2[:, d + 1:].shape)
    b2[d + 1:] *= np.random.default_rng(2).uniform(0.3, 3.0, n - d - 1)
    allow2 = G.sma_regime(b2, 20) > 0.5
    np.testing.assert_array_equal(allow2[:d + 1], allow[:d + 1])
    kw.update(bench=b2, idle_w=allow2.astype(float) * 0.7)
    b = G.equity_ext(px2, trades, allow=allow2, **kw)
    np.testing.assert_allclose(b["equity"][:d + 1], a["equity"][:d + 1], rtol=1e-12)


@pytest.mark.parametrize("d", [40, 150, 299])
def test_features_use_only_data_up_to_d(d):
    n = 400
    px = walk(30, n, seed=11)
    px[3, :120] = np.nan                       # a later listing
    bench = walk(1, n, seed=12)[0]
    t = days(n)
    fns = {"vol": lambda p, b: G.realized_vol(p, 20, 252.0),
           "rs": lambda p, b: G.rel_strength(p, b, 126),
           "near": lambda p, b: G.near_high(p, 252),
           "p_rs": lambda p, b: G.xs_percentile(G.rel_strength(p, b, 126)),
           "p_near": lambda p, b: G.xs_percentile(G.near_high(p, 252))}
    px2, b2 = px.copy(), bench.copy()
    px2[:, d + 1:] = np.random.default_rng(3).uniform(1, 100, px2[:, d + 1:].shape)
    b2[d + 1:] = np.random.default_rng(4).uniform(1, 100, n - d - 1)
    for k, fn in fns.items():
        full, cut, scr = fn(px, bench), fn(px[:, :d + 1], bench[:d + 1]), fn(px2, b2)
        np.testing.assert_allclose(cut[:, d], full[:, d], equal_nan=True, err_msg=k)
        np.testing.assert_allclose(scr[:, :d + 1], full[:, :d + 1], equal_nan=True, err_msg=k)
    for k, fn in {"sma": lambda b, tt: G.sma_regime(b, 200), "faber": lambda b, tt: G.faber_regime(b, tt, 10)}.items():
        full, scr = fn(bench, t), fn(b2, t)
        np.testing.assert_array_equal(scr[:d + 1], full[:d + 1], err_msg=k)
        np.testing.assert_array_equal(fn(bench[:d + 1], t[:d + 1]), full[:d + 1], err_msg=k)


def test_faber_uses_only_completed_months():
    t = days(500, "2019-01-01")
    v = np.linspace(100, 200, 500)
    out = G.faber_regime(v, t, 10)
    mk = G.month_keys(t)
    first = np.flatnonzero(np.isfinite(out))[0]
    # the first value appears on the first day of the 11th month (10 completed months), not earlier
    assert mk[first] - mk[0] == 10 and mk[first - 1] != mk[first]
    assert (out[first:] == 1.0).all()  # rising series: always above its average
    # changing the current month's prices (incl. the would-be month end) cannot change that month's values
    d = first + 40
    m = mk[d]
    v2 = v.copy()
    v2[mk == m] = 1.0
    assert (G.faber_regime(v2, t, 10)[mk == m] == out[mk == m]).all()
    # a crash inside the month only shows up from the next month on
    nxt = np.flatnonzero(mk == m + 1)[0]
    assert G.faber_regime(v2, t, 10)[nxt] == 0.0


def test_sma_regime_and_vol_values():
    v = np.array([1, 2, 3, 4, 5, 1], dtype=float)
    out = G.sma_regime(v, 3)
    assert np.isnan(out[:2]).all() and list(out[2:]) == [1.0, 1.0, 1.0, 0.0]
    r = np.array([0.01, -0.02, 0.015, 0.0, 0.03])
    p = 100 * np.exp(np.concatenate([[0], np.cumsum(r)]))
    vol = G.realized_vol(p, 5, 252.0)
    assert np.isnan(vol[:5]).all() and vol[5] == pytest.approx(np.std(r, ddof=1) * math.sqrt(252))


def test_rs_near_high_percentile_and_order():
    px = np.array([[10, 10, 12], [10, 10, 9], [10, 10, 11], [np.nan, 10, 10]], dtype=float)
    bench = np.array([100, 100, 110], dtype=float)
    rs = G.rel_strength(px, bench, 2)
    assert rs[0, 2] == pytest.approx(1.2 / 1.1 - 1) and np.isnan(rs[3, 2]) and np.isnan(rs[:, :2]).all()
    pct = G.xs_percentile(rs)
    assert list(pct[:3, 2]) == pytest.approx([1.0, 1 / 3, 2 / 3]) and np.isnan(pct[3, 2])
    nh = G.near_high(np.array([[10, 12, 9, 12]], dtype=float), 3)
    assert np.isnan(nh[0, :2]).all() and nh[0, 2] == pytest.approx(0.75) and nh[0, 3] == pytest.approx(1.0)
    scores = np.array([0.1, np.nan, 0.5, 0.3, 0.9])
    d0 = np.array([2, 1, 1, 1, 2])
    assert list(G.score_order(scores, d0)) == [2, 3, 1, 4, 0]


def test_vol_weights_floor_cap_and_missing():
    w = G.vol_weights(np.array([0.2, 0.05, np.nan, 1.0]), c=0.02, floor=0.1, max_w=0.15)
    assert w[0] == pytest.approx(0.1) and w[1] == pytest.approx(0.15) and w[3] == pytest.approx(0.02)
    assert w[2] == pytest.approx(0.02 / 0.2)  # missing -> median sigma (0.2)


def test_psr_dsr_and_calmar():
    rng = np.random.default_rng(0)
    r = rng.normal(0.001, 0.01, 1000)
    m = G.moments(r)
    p = G.psr(m["sr"], m["n"], m["skew"], m["kurt"])
    assert 0.9 < p < 1.0
    assert G.psr(m["sr"], m["n"], m["skew"], m["kurt"], sr_star=m["sr"]) == pytest.approx(0.5)
    trials = rng.normal(0, 0.03, 50)
    d = G.dsr(m["sr"], m["n"], m["skew"], m["kurt"], trials)
    assert d["sr0"] > 0 and d["dsr"] < p
    assert G.expected_max_sr(0.001, 100) > G.expected_max_sr(0.001, 10) > 0
    assert G.calmar(0.2, -0.4) == pytest.approx(0.5) and G.calmar(0.2, 0.0) is None
