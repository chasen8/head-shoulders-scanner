"""Tests for backtest/score_study.py (D50): statistics helpers and the causality of the extra features."""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from backtest import score_study as S

DAY = S.DAY_NS


def test_profit_factor():
    assert math.isclose(S.profit_factor([10.0, -5.0, -5.0, 20.0]), 3.0)
    assert S.profit_factor([1.0, 2.0]) == math.inf
    assert math.isnan(S.profit_factor([]))
    assert math.isnan(S.profit_factor([0.0, 0.0]))
    assert S.profit_factor([-1.0, -2.0]) == 0.0
    assert math.isclose(S.profit_factor([5.0, float("nan"), -2.5]), 2.0)  # NaN ignored


def test_spearman_monotone_and_constant():
    x = np.arange(50.0)
    assert math.isclose(S.spearman(x, x ** 3), 1.0)
    assert math.isclose(S.spearman(x, -x), -1.0)
    assert math.isnan(S.spearman(x, np.ones(50)))


def test_bootstrap_spearman_interval_contains_rho():
    rng = np.random.default_rng(0)
    x = rng.normal(size=400)
    y = 0.5 * x + rng.normal(size=400)
    rho, lo, hi = S.bootstrap_spearman(x, y, n_boot=200, seed=1)
    assert lo < rho < hi and lo > 0.2


def test_quintile_edges_and_bins_use_training_values_only():
    train = np.arange(100.0)
    edges = S.quintile_edges(train)
    assert len(edges) == 4
    test = np.array([-5.0, 10.0, 50.0, 99.0, 500.0, np.nan])
    b = S.assign_bins(test, edges)
    assert list(b) == [0, 0, 2, 4, 4, -1]
    # changing the test values must not change the edges
    assert np.array_equal(edges, S.quintile_edges(train))


def test_group_stats():
    st = S.group_stats([10.0, -5.0, 20.0, -5.0], [True, False, True, False])
    assert st["n"] == 4 and st["win"] == 50.0 and math.isclose(st["mean"], 5.0) and math.isclose(st["pf"], 3.0)


def test_rank_sum_score_is_independent_of_other_test_rows():
    rng = np.random.default_rng(3)
    df = pd.DataFrame({"a": rng.normal(size=60), "b": rng.normal(size=60)})
    train = np.zeros(60, dtype=bool)
    train[:40] = True
    s1 = S.rank_sum_score(df, ["a", "b"], train, [1, -1])
    df2 = df.copy()
    df2.loc[50:, ["a", "b"]] = 99.0  # other TEST rows change
    s2 = S.rank_sum_score(df2, ["a", "b"], train, [1, -1])
    assert np.allclose(s1[:50], s2[:50])
    # high a and low b is the best score (training ECDF from rows 0..39)
    probe = pd.concat([df, pd.DataFrame({"a": [1e9, -1e9], "b": [-1e9, 1e9]})], ignore_index=True)
    tm = np.zeros(len(probe), dtype=bool)
    tm[:40] = True
    sp = S.rank_sum_score(probe, ["a", "b"], tm, [1, -1])
    assert sp[-2] == 1.0 and sp[-1] == 0.0


def _bars(n=800, seed=0):
    rng = np.random.default_rng(seed)
    close = 100 + np.cumsum(rng.normal(0, 1, n))
    high, low = close + 1.0, close - 1.0
    times = (np.arange(n, dtype=np.int64) * DAY) + 1_500_000_000 * 10 ** 9
    return high, low, close, times


def test_range_position_ignores_future_bars():
    high, low, close, times = _bars()
    t = 600
    a = S.range_position(high[: t + 1], low[: t + 1], close[: t + 1], times[: t + 1], t)
    h2, l2, c2 = high.copy(), low.copy(), close.copy()
    h2[t + 1:] = 1e6
    l2[t + 1:] = -1e6
    b = S.range_position(h2[: t + 1], l2[: t + 1], c2[: t + 1], times[: t + 1], t)
    assert a == b and 0.0 <= a["pos52"] <= 1.0 and 0.0 < a["near52"] <= 1.0
    # full-length arrays are also safe because the slice is explicit in the caller; short history -> NaN
    short = S.range_position(high[:100], low[:100], close[:100], times[:100], 99)
    assert math.isnan(short["pos52"])


def test_range_position_values():
    n = 400
    times = np.arange(n, dtype=np.int64) * DAY
    high = np.full(n, 110.0)
    low = np.full(n, 90.0)
    close = np.full(n, 100.0)
    r = S.range_position(high, low, close, times, n - 1)
    assert math.isclose(r["pos52"], 0.5) and math.isclose(r["near52"], 100.0 / 110.0)


def test_months_since_prior_high_uses_bars_before_ls_only():
    n = 1200
    times = np.arange(n, dtype=np.int64) * DAY
    high = np.full(n, 50.0)
    high[400] = 80.0  # the peak
    ls_i = 900
    m = S.months_since_prior_high(high, times, ls_i)
    assert math.isclose(m, (900 - 400) / 30.4375, rel_tol=1e-9)
    high2 = high.copy()
    high2[ls_i:] = 1e9  # LS bar and later bars must not matter
    assert math.isclose(S.months_since_prior_high(high2, times, ls_i), m)
    assert math.isnan(S.months_since_prior_high(high, times, 500))  # < 730 days of history


def test_prior_decline_depth():
    high = np.array([10.0, 20.0, 15.0, 12.0, 11.0])
    low = np.array([9.0, 18.0, 14.0, 10.0, 8.0])
    assert math.isclose(S.prior_decline_depth(high, low, 4, 10), 8.0 / 20.0 - 1.0)
