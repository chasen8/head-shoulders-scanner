"""52-week position fields (local trial): hand-computed value, no look-ahead, null when history is short."""
import pandas as pd

import build_site


def _daily(closes, start="2024-01-01"):
    ts = pd.date_range(start, periods=len(closes), freq="1D", tz="UTC")
    return pd.DataFrame({"timestamp": ts, "close": [float(c) for c in closes]})


def _fourh(closes, start):
    ts = pd.date_range(start, periods=len(closes), freq="4h", tz="UTC")
    return pd.DataFrame({"timestamp": ts, "close": [float(c) for c in closes]})


def test_hand_computed_1d():
    # 10-day window of closes 10..19 then 14 -> window = last 10 closes: 11..19,14; lo 11, hi 19, price 14
    d = _daily(list(range(10, 20)) + [14])
    out = build_site.week52_fields(d, d, "1d", 10)
    assert out == {"pos_52w": round((14 - 11) / (19 - 11), 4), "near_high_52w": round(14 / 19, 4)}


def test_4h_uses_daily_closed_by_asof_only():
    d = _daily([10, 20, 30, 40, 50])             # 2024-01-01..05, daily closes
    # 4h series: last bar opens 2024-01-03 20:00 -> closes 2024-01-04 00:00; price 25
    h = _fourh([25], "2024-01-03 20:00")
    out = build_site.week52_fields(h, d, "4h", 3)
    # daily bars closed by then: Jan1,2,3 (closes 10,20,30) -> lo 10, hi 30
    assert out["pos_52w"] == round((25 - 10) / (30 - 10), 4)
    assert out["near_high_52w"] == round(25 / 30, 4)


def test_truncation_does_not_peek_future():
    d = _daily([10, 20, 30, 40, 50])
    h = _fourh([25], "2024-01-03 20:00")
    a = build_site.week52_fields(h, d, "4h", 3)
    b = build_site.week52_fields(h, d.iloc[:3], "4h", 3)   # future daily bars removed
    assert a == b


def test_null_when_short_history_or_no_daily_or_flat():
    d = _daily([10, 20, 30])
    none = {"pos_52w": None, "near_high_52w": None}
    assert build_site.week52_fields(d, d, "1d", 10) == none
    assert build_site.week52_fields(d, None, "1w", 3) == none
    flat = _daily([5, 5, 5])
    assert build_site.week52_fields(flat, flat, "1d", 3) == none


def test_price_above_window_high_is_clipped_to_one():
    d = _daily([10, 20, 30])
    h = _fourh([35], "2024-01-03 20:00")
    out = build_site.week52_fields(h, d, "4h", 3)
    assert out == {"pos_52w": 1.0, "near_high_52w": 1.0}
