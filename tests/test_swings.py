import numpy as np
import pandas as pd

from ihs_scanner.swings import find_swing_points, pivot_mask


def _df_from_lows(lows, spread=1.0):
    lows = np.asarray(lows, dtype=float)
    n = len(lows)
    return pd.DataFrame({
        "timestamp": pd.date_range("2024-01-01", periods=n, freq="4h", tz="UTC"),
        "open": lows + spread / 2, "high": lows + spread, "low": lows,
        "close": lows + spread / 2, "volume": np.full(n, 1.0),
    })


def test_simple_swing_low_and_confirm_index():
    df = _df_from_lows([5, 4, 3, 2, 3, 4, 5])
    sw = find_swing_points(df, 3)
    assert [p.index for p in sw["lows"]] == [3]
    p = sw["lows"][0]
    assert p.confirm_index == 6 and p.price == 2.0 and p.time == df["timestamp"].iloc[3]
    assert p.kind == "low"


def test_flat_bottom_is_not_a_pivot():
    df = _df_from_lows([5, 4, 2, 2, 4, 5, 6, 7])
    assert find_swing_points(df, 2)["lows"] == []
    # equal value on only one side also rejects (strict on both sides)
    assert not pivot_mask([3, 2, 1, 1, 2, 3], 1, "low").any()


def test_last_n_bars_never_pivot():
    lows = [5, 4, 3, 4, 5, 6, 7, 6, 5, 1, 2]
    sw = find_swing_points(_df_from_lows(lows), 2)
    idx = [p.index for p in sw["lows"]]
    assert idx == [2]  # index 9 (value 1) has only one right neighbour
    assert all(p.index <= len(lows) - 1 - 2 for p in sw["lows"] + sw["highs"])
    assert all(p.confirm_index == p.index + 2 for p in sw["lows"] + sw["highs"])


def test_swing_high_strict():
    highs = np.array([1, 2, 3, 9, 3, 2, 1, 5, 5, 1, 0, 0], dtype=float)
    df = pd.DataFrame({"timestamp": pd.date_range("2024", periods=len(highs), freq="1D", tz="UTC"),
                       "open": highs - 0.5, "high": highs, "low": highs - 1,
                       "close": highs - 0.5, "volume": 1.0})
    sw = find_swing_points(df, 2)
    assert [p.index for p in sw["highs"]] == [3]  # the 5,5 plateau is not a pivot
    assert sw["highs"][0].price == 9.0


def test_pivot_needs_n_left_bars():
    # global min at index 1 cannot be a pivot with N=2 (only 1 bar on the left)
    assert not pivot_mask([3, 1, 2, 3, 4, 5], 2, "low").any()
