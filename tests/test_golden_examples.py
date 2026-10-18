"""D22 golden test: the user's five hand-marked inverse head-and-shoulders must be found by the
default (template) profile, using real K lines cut at the screenshot time.

Fixtures: tests/fixtures/<name>.csv (scripts/fetch_golden_fixtures.py). Points and annotations:
tests/golden_examples.py. Run with -s to see the comparison table.

Tolerances: LS/N1/H/N2 within +-6 bars (4h) / +-3 bars (1d) of the user's point and price within 1%.
RS must be "forming" or a confirmed pivot (its exact bar is not asserted: on the closed bars four of
the five right shoulders are still the newest low, and two user RS prices are not the bar's low).
"""
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from ihs_scanner.detector import detect_inverse_head_shoulders

from .golden_examples import GOLDEN_EXAMPLES

FIXTURES = Path(__file__).parent / "fixtures"
BAR = {"4h": pd.Timedelta(hours=4), "1d": pd.Timedelta(days=1), "1w": pd.Timedelta(days=7)}
TOL_BARS = {"4h": 6, "1d": 3, "1w": 2}
TOL_PRICE = 0.01


def load_as_of_screenshot(name: str) -> tuple[pd.DataFrame, dict]:
    spec = GOLDEN_EXAMPLES[name]
    df = pd.read_csv(FIXTURES / f"{name}.csv", encoding="utf-8")
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    shot = pd.Timestamp(spec["screenshot_utc"])
    closed = df[df["timestamp"] + BAR[spec["timeframe"]] <= shot].reset_index(drop=True)
    assert len(closed) < len(df), "fixture should extend past the screenshot so the cut is exercised"
    return closed, spec


def _matches(r: dict, spec: dict, ts: pd.Series) -> bool:
    tol = TOL_BARS[spec["timeframe"]]
    for key in ("LS", "N1", "H", "N2"):
        t_user, p_user = spec["points"][key]
        i_user = int(np.flatnonzero(ts == pd.Timestamp(t_user))[0])
        i_found = int(np.flatnonzero(ts == r[f"{key}_time"])[0])
        if abs(i_found - i_user) > tol or abs(r[f"{key}_price"] / p_user - 1) > TOL_PRICE:
            return False
    return r["status"] in ("forming", "watchlist", "confirmed")


@pytest.mark.parametrize("name", list(GOLDEN_EXAMPLES))
def test_golden_example_found(name):
    df, spec = load_as_of_screenshot(name)
    res = detect_inverse_head_shoulders(df, spec["timeframe"], None, symbol=spec["symbol"])
    ts = df["timestamp"]
    hits = [r for r in res if _matches(r, spec, ts)]
    found = "\n".join(f"  {r['status']} LS={r['LS_time']} N1={r['N1_time']} H={r['H_time']} N2={r['N2_time']}"
                      f" RS={r['RS_time']}" for r in res)
    assert hits, f"{name}: user pattern not found; results:\n{found}"
    r = hits[0]
    # the head is the user's head and is the lowest point since LS (invalidation not hit)
    assert r["invalidation_price"] == r["H_price"]
    assert df["low"].iloc[int(np.flatnonzero(ts == r["LS_time"])[0]):].min() == r["H_price"]
    if r["status"] == "forming":
        assert r["RS_confirm_time"] is None
        assert r["RS_time"] > ts.iloc[-1 - 5]  # still inside the pivot confirmation window
    else:
        assert r["RS_confirm_time"] is not None
    u = spec
    print(f"\n{name} [{r['status']}, score {r['pattern_score']:.1f} = {r['shoulder_score']:.1f}+"
          f"{r['head_depth_score']:.1f}+{r['neckline_score']:.1f}+{r['breakout_score']:.1f}]")
    for key in ("LS", "N1", "H", "N2", "RS"):
        t_user, p_user = spec["points"][key]
        print(f"  {key:<2} found {r[f'{key}_time']:%Y-%m-%d %H:%M} {r[f'{key}_price']:<10.6g}"
              f" user {t_user or 'forming':<20} {p_user}")
    print(f"  drop_L {r['drop_left_pct']:.2%} (user {u['user_drop_left']:.2%})  "
          f"drop_R {r['drop_right_pct']:.2%} (user {u['user_drop_right']:.2%})  "
          f"head_rise {r['head_rise_pct']:.2%} (user {u['user_head_rise']:.2%})")
    print(f"  breakout_level {r['breakout_level']:.6g}  tp1 {r['tp1']:.6g}  tp2 {r['tp2']:.6g}"
          f" (user target {u['user_target']})  invalidation {r['invalidation_price']:.6g}")


@pytest.mark.parametrize("name", ["HMSTRUSDT_binance_spot_4h", "PENDLEUSDT_binance_spot_1d"])
def test_golden_truncation(name):
    """As of each of the last 25 closed bars, every result references only bars <= t, a forming RS is
    still unconfirmed, and the result equals what a fresh run on df[:t+1] with the future mutated gives."""
    df, spec = load_as_of_screenshot(name)
    ts = df["timestamp"]
    n = len(df)
    for t in range(n - 25, n):
        cut = df.iloc[:t + 1]
        res = detect_inverse_head_shoulders(cut, spec["timeframe"], None, symbol=spec["symbol"])
        for r in res:
            assert r["RS_time"] <= ts[t] and r["signal_time"] <= ts[t]
            if r["status"] == "forming":
                assert r["RS_time"] > ts[t - 5]
        mutated = df.copy()
        mutated.loc[t + 1:, ["open", "high", "low", "close"]] *= 0.3
        again = detect_inverse_head_shoulders(mutated.iloc[:t + 1], spec["timeframe"], None,
                                              symbol=spec["symbol"])
        assert again == res
