"""D36 golden cases: the user's TradingView checks of BTCUSDT 1d, ATUSDT 4h and LDOUSDT 1w / 1d
(binance spot, K lines cut at the last bar closed at D36_SCREENSHOT_UTC; points: tests/golden_examples.py).

  BTC 1d  breakout first (rule B): the user's pattern (RS 09-15/16, N2 09-03) broke out on 2026-09-21, so the later
          "RS 10-15 with N2 = the breakout bar's high" (rising neckline) must not be reported as an unbroken pattern.
  AT 4h   wick breakout (rule A): the 10-21 08:00 UTC bar's high crosses the neckline -> breakout_detected.
  LDO 1w  the user's falling-neckline weekly pattern is found (1w angle limit, T8 body lead-in), and the rising 1d
          pattern on the same head is not output (rule B) / is superseded by the weekly one (cross-timeframe).
Run with -s to see the comparison with the user's description.
"""
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from ihs_scanner.detector import detect_inverse_head_shoulders
from ihs_scanner.scanner import scan_symbols, supersede_by_higher_timeframe

from .golden_examples import D36_CASES

FIXTURES = Path(__file__).parent / "fixtures"
BAR = {"4h": pd.Timedelta(hours=4), "1d": pd.Timedelta(days=1), "1w": pd.Timedelta(days=7)}
UTC = "UTC"


def load(name: str) -> tuple[pd.DataFrame, dict]:
    spec = D36_CASES[name]
    df = pd.read_csv(FIXTURES / f"{name}.csv", encoding="utf-8")
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    shot = pd.Timestamp(spec["screenshot_utc"])
    return df[df["timestamp"] + BAR[spec["timeframe"]] <= shot].reset_index(drop=True), spec


def _bar(ts: pd.Series, t) -> int:
    return int(np.flatnonzero(ts == pd.Timestamp(t))[0])


def _near(r: dict, spec: dict, ts: pd.Series, keys, tol_bars: int, tol_price: float) -> bool:
    for key in keys:
        t_user, p_user = spec["points"][key]
        if abs(_bar(ts, r[f"{key}_time"]) - _bar(ts, t_user)) > tol_bars:
            return False
        if abs(r[f"{key}_price"] / p_user - 1) > tol_price:
            return False
    return True


def _show(title: str, r: dict, spec: dict | None = None) -> None:
    print(f"\n{title} [{r['status']}{'/' + r['stale_reason'] if r.get('stale_reason') else ''}, "
          f"score {r['pattern_score']:.1f}] neckline {r['neckline_change']:+.2%}, touches {r['neckline_touches']}, "
          f"breakout {r['breakout_time']} ({r['breakout_basis']} {r['breakout_price']})")
    for key in ("LS", "N1", "H", "N2", "RS"):
        user = (spec or {}).get("points", {}).get(key)
        print(f"  {key:<2} found {r[f'{key}_time']:%Y-%m-%d %H:%M} {r[f'{key}_price']:<10.6g}"
              + (f" user {user[0][:16]} {user[1]}" if user else ""))
    print(f"  neckline now {r['current_neckline_price']:.6g}, close {r['current_close']:.6g}, "
          f"breakout_level {r['breakout_level']:.6g}, tp1 {r['tp1']:.6g}, tp2 {r['tp2']:.6g}")


# ---------- BTC 1d: breakout first ----------
def test_btc_1d_user_pattern_broke_out():
    df, spec = load("BTCUSDT_binance_spot_1d")
    ts = df["timestamp"]
    res = detect_inverse_head_shoulders(df, "1d", {"include_stale": True}, symbol="BTCUSDT")
    hits = [r for r in res if _near(r, spec, ts, ("LS", "N1", "N2", "RS"), 3, 0.01)]
    assert hits, "user's BTC pattern (RS 09-16, N2 09-03) not found"
    r = hits[0]
    _show("BTCUSDT 1d (include_stale)", r, spec)
    assert r["breakout_detected"] and r["status"] in ("confirmed", "stale")
    assert r["breakout_time"] == pd.Timestamp("2026-09-21", tz=UTC)  # close 86,620 vs neckline ~82,2xx
    assert r["neckline_change"] < 0 and abs(r["neckline_change"]) < 0.01  # N1 -> N2 almost flat
    # the post-breakout "pattern" (N2 = 09-21 breakout high, RS after the breakout, rising neckline) is gone,
    # with or without stale results ...
    for cfg in ({"include_stale": True}, None):
        for q in detect_inverse_head_shoulders(df, "1d", cfg, symbol="BTCUSDT"):
            if q["H_time"] == r["H_time"]:
                assert not q["RS_time"] > r["breakout_time"], q
    # ... and it is exactly what rule B removes
    off = detect_inverse_head_shoulders(df, "1d", {"reject_post_breakout_rs": False}, symbol="BTCUSDT")
    retest = [q for q in off if q["H_time"] == r["H_time"] and q["RS_time"] > r["breakout_time"]]
    assert retest and all(q["neckline_change"] > 0 and not q["breakout_detected"] for q in retest)
    assert retest[0]["N2_time"] >= r["breakout_time"]


# ---------- AT 4h: wick breakout ----------
def test_at_4h_wick_breakout():
    df, spec = load("ATUSDT_binance_spot_4h")
    ts = df["timestamp"]
    t_wick, wick_high, wick_close = spec["wick_bar"]
    i = _bar(ts, t_wick)
    assert df["high"][i] == pytest.approx(wick_high) and df["close"][i] == pytest.approx(wick_close)
    res = detect_inverse_head_shoulders(df, "4h", None, symbol="ATUSDT")
    hits = [r for r in res if r["H_time"] == pd.Timestamp(spec["H"])]
    assert len(hits) == 1
    r = hits[0]
    _show("ATUSDT 4h", r)
    assert r["breakout_detected"] and r["status"] == "confirmed"
    assert r["breakout_time"] == pd.Timestamp(t_wick) and r["breakout_basis"] == "high"
    assert r["breakout_price"] == pytest.approx(wick_high)
    assert r["neckline_change"] < 0  # falling neckline
    neck_i = r["N1_price"] + (r["N2_price"] - r["N1_price"]) * (
        (i - _bar(ts, r["N1_time"])) / (_bar(ts, r["N2_time"]) - _bar(ts, r["N1_time"])))
    assert wick_close < neck_i < wick_high  # the close stayed below the neckline, the wick crossed it
    assert r["breakout_level"] == pytest.approx(neck_i)  # TP2 base = neckline at the breakout bar
    assert r["tp2"] == pytest.approx(neck_i * (1 + r["head_rise_pct"]))
    assert r["signal_time"] == max(r["RS_confirm_time"], r["breakout_time"])
    # the D8 close rule on the same pattern: not broken out, RS confirmed
    close = detect_inverse_head_shoulders(df, "4h", {"breakout_price": "close"}, symbol="ATUSDT")
    same = [q for q in close if q["pattern_id"] == r["pattern_id"]]
    assert same and same[0]["status"] == "watchlist" and not same[0]["breakout_detected"]


# ---------- LDO 1w / 1d: falling weekly neckline wins ----------
def test_ldo_1w_user_pattern():
    df, spec = load("LDOUSDT_binance_spot_1w")
    ts = df["timestamp"]
    res = detect_inverse_head_shoulders(df, "1w", None, symbol="LDOUSDT")
    hits = [r for r in res if _near(r, spec, ts, ("LS", "N1", "H", "N2", "RS"), 2, 0.03)]
    assert hits, f"user's LDO 1w pattern not found: {[(r['LS_time'], r['N2_time']) for r in res]}"
    r = hits[0]
    _show("LDOUSDT 1w", r, spec)
    print(f"  head_rise {r['head_rise_pct']:.2%} (user {spec['user_head_rise']:.2%} measured at the 05-11 neckline), "
          f"drops {r['drop_left_pct']:.2%} / {r['drop_right_pct']:.2%}, angle {r['neckline_angle']:.1f} deg")
    assert r["status"] == "forming" and r["neckline_change"] < -0.2  # falling neckline (-25.6%)
    assert r["drop_left_pct"] == pytest.approx(0.2783, abs=5e-4) and r["drop_right_pct"] == pytest.approx(0.2835,
                                                                                                         abs=5e-4)
    # no bar between N1 and RS (except next to N1 / N2) has its high within 1% of this neckline in the Binance
    # weekly data: the highest mid-pattern high (2026-04-20, 0.47) is 23% below it
    assert r["neckline_touches"] == 0
    # what used to block it: the spec's 30 deg angle (-34.7 deg on weekly bars) and the T8 lead-in on the
    # 2025-10-06 flash-crash wick (low 0.2314 < H, body low 0.916)
    for cfg in ({"max_neckline_angle": 30.0}, {"head_lead_in_price": "low"}):
        assert not [q for q in detect_inverse_head_shoulders(df, "1w", cfg, symbol="LDOUSDT")
                    if _near(q, spec, ts, ("LS", "N1", "H", "N2", "RS"), 2, 0.03)], cfg


def test_ldo_1d_rising_neckline_not_reported():
    wk, _ = load("LDOUSDT_binance_spot_1w")
    dy, _ = load("LDOUSDT_binance_spot_1d")
    weekly = [r for r in detect_inverse_head_shoulders(wk, "1w", None, symbol="LDOUSDT") if r["neckline_change"] < 0]
    assert weekly
    w = weekly[0]

    def covered(q):
        return w["LS_time"] <= q["H_time"] <= w["RS_time"]

    # default: no rising 1d neckline on the weekly pattern's head is reported (rule B drops the post-breakout one)
    daily = detect_inverse_head_shoulders(dy, "1d", None, symbol="LDOUSDT")
    for q in daily:
        _show("LDOUSDT 1d (default)", q)
    assert not [q for q in daily if covered(q) and q["neckline_change"] > 0]
    # the D36 de-duplication alone also prefers the falling 1d neckline over the rising one ...
    old_rules = {"reject_post_breakout_rs": False, "dedup_priority": "score"}
    no_b = detect_inverse_head_shoulders(dy, "1d", {"reject_post_breakout_rs": False}, symbol="LDOUSDT")
    assert not [q for q in no_b if covered(q) and q["neckline_change"] > 0]
    # ... with rule B off and the D11 score order the rising 1d pattern (N1 0.47 -> N2 0.5066, what the user saw)
    # comes back, and the cross-timeframe step supersedes it with the weekly pattern
    rising = [q for q in detect_inverse_head_shoulders(dy, "1d", old_rules, symbol="LDOUSDT")
              if covered(q) and q["neckline_change"] > 0]
    assert rising
    kept, gone = supersede_by_higher_timeframe(rising + weekly)
    assert {g["pattern_id"] for g in gone} == {q["pattern_id"] for q in rising}
    assert all(g["superseded_by"] == w["pattern_id"] for g in gone) and w in kept
    assert rising[0]["N1_price"] == pytest.approx(0.47) and rising[0]["N2_price"] == pytest.approx(0.5066)
    out = scan_symbols({"LDOUSDT": {"1w": wk, "1d": dy}}, old_rules)
    assert not [q for q in out if q["timeframe"] == "1d" and covered(q) and q["neckline_change"] > 0]
    assert any(q["pattern_id"] == w["pattern_id"] for q in out)


# ---------- no repaint with the D36 rules ----------
@pytest.mark.parametrize("name,bars", [("ATUSDT_binance_spot_4h", 30), ("BTCUSDT_binance_spot_1d", 40),
                                       ("LDOUSDT_binance_spot_1w", 12)])
def test_d36_truncation(name, bars):
    """As of each of the last `bars` closed bars: results only reference bars <= t, a breakout / first cross is
    after RS and <= t, and the result equals a fresh run on df[:t+1] whose future was mutated (rules A / B and the
    touch count read no later bars). Includes stale results so rule B's blocking candidates are compared too."""
    df, spec = load(name)
    tf = spec["timeframe"]
    ts = df["timestamp"]
    n = len(df)
    cfg = {"include_stale": True}
    for t in range(n - bars, n):
        res = detect_inverse_head_shoulders(df.iloc[:t + 1], tf, cfg, symbol=spec["symbol"])
        for r in res:
            assert r["RS_time"] <= ts[t] and r["signal_time"] <= ts[t]
            for k in ("breakout_time", "first_cross_time"):
                if r[k] is not None:
                    assert r["RS_time"] < r[k] <= ts[t]
        mutated = df.copy()
        mutated.loc[t + 1:, ["open", "high", "low", "close"]] *= 3.0
        again = detect_inverse_head_shoulders(mutated.iloc[:t + 1], tf, cfg, symbol=spec["symbol"])
        assert again == res
    # the AT wick breakout appears exactly when the wick bar closes, never before
    if name.startswith("AT"):
        i = _bar(ts, spec["wick_bar"][0])
        before = detect_inverse_head_shoulders(df.iloc[:i], tf, None, symbol=spec["symbol"])
        at = detect_inverse_head_shoulders(df.iloc[:i + 1], tf, None, symbol=spec["symbol"])
        h = pd.Timestamp(spec["H"])
        assert not [r for r in before if r["H_time"] == h and r["breakout_detected"]]
        assert [r for r in at if r["H_time"] == h and r["breakout_time"] == ts[i]]


def test_build_payload_supersedes_across_timeframes():
    """build_site scans every series on its own, so the cross-timeframe step runs after all of them:
    the rising LDO 1d pattern (old rules) is listed in `superseded`, not in `results`."""
    import build_site
    wk, _ = load("LDOUSDT_binance_spot_1w")
    dy, _ = load("LDOUSDT_binance_spot_1d")
    cfg = build_site.merge_config({"reject_post_breakout_rs": False, "dedup_priority": "score"})
    payload, _ = build_site.build_payload({"LDOUSDT": {"1d": dy, "1w": wk}}, cfg, symbols=["LDOUSDT"],
                                          timeframes=["1d", "1w"], limit=1000)
    assert payload["settings"]["breakout_price"] == "high"
    sup = payload["superseded"]
    assert len(sup) == 1 and "|1d|" in sup[0]["pattern_id"] and "|1w|" in sup[0]["superseded_by"]
    assert sup[0]["pattern_id"] not in {r["pattern_id"] for r in payload["results"]}
    assert all("neckline_touches" in r and r["breakout_basis"] == "high" for r in payload["results"])
