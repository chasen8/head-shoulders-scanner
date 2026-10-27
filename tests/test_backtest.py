"""D40 backtest engine (backtest/engine.py): no look-ahead, exit rules, costs, breakeven, holding cap, de-dup.
No network: real bars come from tests/fixtures."""
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from backtest import engine as E

FIXTURES = Path(__file__).parent / "fixtures"


def load(name: str, n: int | None = None) -> pd.DataFrame:
    df = pd.read_csv(FIXTURES / f"{name}.csv", encoding="utf-8")
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    return df.iloc[:n].reset_index(drop=True) if n else df


def scramble_after(df: pd.DataFrame, t: int, seed: int = 1) -> pd.DataFrame:
    """Same timestamps, every bar after t replaced by an unrelated random walk (the future is rewritten)."""
    out = df.copy()
    k = len(df) - t - 1
    rng = np.random.default_rng(seed)
    c = float(df["close"].iloc[t]) * np.exp(np.cumsum(rng.normal(0, 0.05, k)))
    o = np.concatenate([[float(df["close"].iloc[t])], c[:-1]])
    out.loc[t + 1:, "open"] = o
    out.loc[t + 1:, "close"] = c
    out.loc[t + 1:, "high"] = np.maximum(o, c) * 1.03
    out.loc[t + 1:, "low"] = np.minimum(o, c) * 0.97
    out.loc[t + 1:, "volume"] = rng.uniform(1, 1e6, k)
    return out


def bars(rows):
    """rows: [(open, high, low, close)]"""
    a = np.array(rows, dtype=float)
    return E.Bars(a[:, 0], a[:, 1], a[:, 2], a[:, 3])


def frame(rows, start="2024-01-01", freq="1D"):
    a = np.array(rows, dtype=float)
    return pd.DataFrame({"timestamp": pd.date_range(start, periods=len(a), freq=freq, tz="UTC"),
                         "open": a[:, 0], "high": a[:, 1], "low": a[:, 2], "close": a[:, 3],
                         "volume": np.full(len(a), 100.0)})


# ------------------------------------------------------------------ no look-ahead

def _until(sigs, t):
    return [s for s in sigs if s["t"] <= t]


def test_ihs_entries_do_not_change_when_the_future_is_rewritten():
    df = load("BTCUSDT_binance_spot_1d", 700)
    full = E.ihs_signals(df, "1d", "crypto", symbol="BTC")
    assert {s["mode"] for s in full} == {"A", "B"}  # the fixture has both entry kinds
    for cut in sorted({s["t"] for s in full})[:3]:
        alt = E.ihs_signals(scramble_after(df, cut), "1d", "crypto", symbol="BTC")
        assert _until(alt, cut) == _until(full, cut), cut  # same entry bar, price-level inputs, stop, TP1, TP2


def test_triangle_entries_do_not_change_when_the_future_is_rewritten():
    df = load("ATUSDT_binance_spot_4h", 500)
    full = E.triangle_signals(df, "4h", "crypto", symbol="AT")
    assert full
    for cut in sorted({s["t"] for s in full})[:3]:
        alt = E.triangle_signals(scramble_after(df, cut, seed=cut), "4h", "crypto", symbol="AT")
        assert _until(alt, cut) == _until(full, cut), cut


def test_detector_only_ever_sees_bars_up_to_t():
    df = load("ATUSDT_binance_spot_4h", 300)
    seen = []

    def spy(win, tf, cfg, symbol):
        seen.append((int(win.index[0]), int(win.index[-1]), len(win)))
        return E.ihs_view(win, tf, cfg, symbol)

    E.ihs_signals(df, "4h", "crypto", symbol="AT", view_fn=spy)
    ts = list(E.eval_points(len(df), "4h", 5))
    assert [s[1] for s in seen] == ts  # the window ends exactly at the eval bar
    assert all(n <= E.WINDOW["4h"] for _a, _b, n in seen)


def test_trade_entry_uses_close_t_and_exits_only_after_t():
    df = load("BTCUSDT_binance_spot_1d", 700)
    sigs = E.ihs_signals(df, "1d", "crypto", symbol="BTC")
    trades, _ = E.build_trades(df, sigs, "1d", "crypto")
    assert trades
    for tr in trades:
        assert tr["entry_price"] == df["close"].iloc[tr["t"]]
        assert tr["exit_bar"] > tr["t"]


def test_weekly_view_lookup_uses_only_closed_weeks():
    times = np.array([10, 20, 30], dtype=np.int64)
    views = [["w0"], ["w1"], ["w2"]]
    assert E._weekly_at((times, views), 9) == []
    assert E._weekly_at((times, views), 20) == ["w1"]
    assert E._weekly_at((times, views), 29) == ["w1"]


def test_ats_atr_is_causal():
    from ihs_scanner.detector import wilder_atr
    df = load("ATUSDT_binance_spot_4h", 400)
    a = wilder_atr(df, 14)
    b = wilder_atr(scramble_after(df, 200), 14)
    assert np.allclose(a[:201], b[:201], equal_nan=True)


# ------------------------------------------------------------------ exits

def test_same_bar_stop_and_target_counts_as_stop():
    b = bars([(100, 101, 99, 100), (100, 120, 80, 100)])
    assert E.simulate_single(b, 0, stop=90, tp=110, max_hold=10) == (1, 90.0, "stop")
    legs = E.simulate_half(b, 0, 100, 90, 110, 115, 10)
    assert [l[2] for l in legs] == ["stop", "stop"]


def test_target_and_gap_stop_fills():
    b = bars([(100, 101, 99, 100), (100, 111, 99, 110), (85, 86, 80, 82)])
    assert E.simulate_single(b, 0, 90, 110, 10) == (1, 110.0, "tp")
    assert E.simulate_single(b, 0, 90, 130, 10) == (2, 85.0, "stop")  # gap below the stop fills at the open


def test_short_mirrors_long():
    b = bars([(100, 101, 99, 100), (100, 101, 89, 90), (100, 112, 99, 111)])
    assert E.simulate_single(b, 0, stop=110, tp=90, max_hold=10, d=-1) == (1, 90.0, "tp")
    assert E.simulate_single(b, 0, stop=110, tp=80, max_hold=10, d=-1) == (2, 110.0, "stop")


def test_holding_cap_and_data_end():
    b = bars([(100, 101, 99, 100)] * 6)
    assert E.simulate_single(b, 0, 90, 120, max_hold=3) == (3, 100.0, "timeout")
    assert E.simulate_single(b, 2, 90, 120, max_hold=10) == (5, 100.0, "open")
    legs = E.simulate_half(b, 0, 100, 90, 110, 120, 3)
    assert legs == [(3, 100.0, "timeout"), (3, 100.0, "timeout")]


def test_half_moves_stop_to_breakeven_after_tp1():
    b = bars([(100, 101, 99, 100), (100, 111, 99, 108), (105, 106, 99, 100), (100, 130, 99, 125)])
    legs = E.simulate_half(b, 0, entry=100, stop=90, tp_a=110, tp_b=125, max_hold=10)
    assert legs == [(1, 110.0, "tp"), (2, 100.0, "breakeven")]
    # without the dip below the entry the second half reaches TP2
    b2 = bars([(100, 101, 99, 100), (100, 111, 99, 108), (105, 106, 101, 104), (104, 130, 101, 125)])
    assert E.simulate_half(b2, 0, 100, 90, 110, 125, 10) == [(1, 110.0, "tp"), (3, 125.0, "tp")]
    # the breakeven stop only starts on the bar AFTER TP1 (TP1 bar's low 95 is not a breakeven exit)
    b3 = bars([(100, 101, 99, 100), (100, 111, 95, 108), (108, 126, 105, 120)])
    assert E.simulate_half(b3, 0, 100, 90, 110, 125, 10) == [(1, 110.0, "tp"), (2, 125.0, "tp")]
    # one bar through both targets closes both halves
    b4 = bars([(100, 101, 99, 100), (100, 130, 99, 128)])
    assert E.simulate_half(b4, 0, 100, 90, 110, 125, 10) == [(1, 110.0, "tp"), (1, 125.0, "tp")]


def test_costs():
    assert E.net_return(100, 110, "crypto") == pytest.approx(110 * 0.999 / (100 * 1.001) - 1)
    assert E.net_return(100, 110, "tw") == pytest.approx(110 * (1 - 0.001425 - 0.003) / (100 * 1.001425) - 1)
    assert E.net_return(100, 100, "tw") == pytest.approx(-0.005840, abs=1e-5)  # round trip ~0.585%
    assert E.net_return(100, 90, "crypto", d=-1) == pytest.approx((100 * 0.999 - 90 * 1.001) / 100)


# ------------------------------------------------------------------ build_trades: levels, R, one position at a time

def _ihs_sig(t, pid, rs=95.0, tp1=110.0, tp2=120.0, mode="A"):
    return {"family": "ihs", "mode": mode, "t": t, "pattern_id": pid, "status": "confirmed", "RS_price": rs,
            "tp1": tp1, "tp2": tp2}


def test_one_position_per_series_and_strategy():
    rows = [(100, 101, 99, 100)] * 3 + [(100, 111, 99, 110)] + [(100, 101, 99, 100)] * 6
    df = frame(rows)
    sigs = [_ihs_sig(0, "p1"), _ihs_sig(2, "p2"), _ihs_sig(3, "p3"), _ihs_sig(5, "p4")]
    trades, skips = E.build_trades(df, sigs, "1d", "crypto", max_hold=50)
    tp1 = [t for t in trades if t["exit"] == "TP1"]
    assert [t["pattern_id"] for t in tp1] == ["p1", "p4"]  # p1 exits at bar 3 (TP1); p2 (t=2) and p3 (t=3) skip
    assert tp1[0]["exit_reason"] == "tp" and tp1[0]["exit_bar"] == 3
    assert {s["pattern_id"] for s in skips if s["exit"] == "TP1"} == {"p2", "p3"}
    assert all(s["reason"] == "position_open" for s in skips if s["exit"] == "TP1")
    r = tp1[0]
    assert r["risk_pct"] == pytest.approx(0.05)
    assert r["R"] == pytest.approx(E.net_return(100, 110, "crypto") / 0.05)
    # p4 never exits before the data ends -> marked open (excluded from the statistics)
    assert tp1[1]["exit_reason"] == "open" and tp1[1]["is_open"]


def test_invalid_levels_are_skipped():
    df = frame([(100, 101, 99, 100)] * 5)
    sigs = [_ihs_sig(0, "a", rs=101.0), _ihs_sig(1, "b", tp1=99.0, tp2=120.0)]
    trades, skips = E.build_trades(df, sigs, "1d", "crypto", max_hold=2)
    reasons = {(s["pattern_id"], s["exit"]): s["reason"] for s in skips}
    assert reasons[("a", "TP1")] == "stop_beyond_entry"
    assert reasons[("b", "TP1")] == "target_reached_at_entry"
    assert any(t["pattern_id"] == "b" and t["exit"] == "TP2" for t in trades)


def test_triangle_atr_stop_and_short():
    df = frame([(100, 101, 99, 100)] * 4 + [(100, 101, 90, 95)])
    sig_up = {"family": "tri", "direction": 1, "t": 1, "pattern_id": "u", "status": "breakout_up",
              "target": 120.0, "target_conservative": 110.0, "invalidation_price": 80.0}
    sig_dn = {**sig_up, "direction": -1, "pattern_id": "d", "status": "breakout_down", "target": 80.0,
              "target_conservative": 92.0, "invalidation_price": 120.0}
    atr = np.full(5, 2.0)
    trades, _ = E.build_trades(df, [sig_up, sig_dn], "1d", "crypto", atr=atr, max_hold=10)
    by = {(t["pattern_id"], t["stop_kind"], t["exit"]): t for t in trades}
    assert by[("u", "atr", "TP1")]["stop"] == pytest.approx(97.0)  # 100 - 1.5 * 2
    assert by[("u", "atr", "TP1")]["exit_reason"] == "stop"
    assert by[("u", "inval", "TP1")]["stop"] == 80.0
    assert by[("d", "atr", "TP1")]["stop"] == pytest.approx(103.0)
    assert by[("d", "inval", "TP1")]["exit_reason"] == "tp"  # low 90 <= 92


def test_first_appearance_only():
    df = load("ATUSDT_binance_spot_4h", 120)
    row = {"pattern_id": "X|4h|p", "status": "watchlist", "timeframe": "4h", "RS_price": 1.0, "tp1": 2.0, "tp2": 3.0}

    def view(win, tf, cfg, symbol):
        return [dict(row)] if len(win) > 50 else []

    sigs = E.ihs_signals(df, "4h", "crypto", view_fn=view)
    assert len(sigs) == 1 and sigs[0]["mode"] == "B" and sigs[0]["t"] == 54  # first eval bar with >50 bars


def test_double_bottom_entries_do_not_change_when_the_future_is_rewritten():
    """D49: W signals (modes A and B) as of an eval bar t depend on df[:t+1] only."""
    df = load("BTCUSDT_binance_spot_1d", 700)
    full = E.db_signals(df, "1d", "crypto", symbol="BTC")
    assert {s["mode"] for s in full} == {"A", "B"}
    for cut in sorted({s["t"] for s in full})[:4]:
        alt = E.db_signals(scramble_after(df, cut, seed=cut), "1d", "crypto", symbol="BTC")
        assert _until(alt, cut) == _until(full, cut), cut


def test_double_bottom_trades_use_invalidation_stop_and_ihs_exit_conventions():
    df = load("BTCUSDT_binance_spot_1d", 700)
    sigs = E.db_signals(df, "1d", "crypto", symbol="BTC")
    trades, _skips = E.build_trades(df, sigs, "1d", "crypto", meta={"symbol": "BTC"})
    assert trades and {t["family"] for t in trades} == {"db"}
    for t in trades:
        assert t["stop"] == t["sig_invalidation_price"] and t["stop"] < t["entry_price"]
        assert t["exit_bar"] > t["t"] and t["entry_price"] == pytest.approx(float(df["close"].iloc[t["t"]]))
