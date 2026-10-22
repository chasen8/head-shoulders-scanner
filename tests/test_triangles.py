"""D38 converging triangles (ihs_scanner/triangles.py) + their build_site output.

Synthetic zigzags (tests/synth.make_df: piecewise-linear lows through the waypoints, high = low + 1): peaks are
pivot highs at waypoint + 1, troughs pivot lows at the waypoint value. 4h, pivot window N = 5.
"""
import json
import math

import numpy as np
import pandas as pd
import pytest

import build_site
from ihs_scanner import triangles as tri
from ihs_scanner.config import TRIANGLE_CONFIG, triangle_config
from ihs_scanner.triangles import classify, deduplicate_triangles, detect_triangles, find_triangle_candidates

from .synth import make_df, random_walk_df

N = 5
ASC = [(0, 95), (10, 110), (20, 90), (30, 110), (40, 94), (50, 110), (60, 98), (70, 110), (78, 104)]
DESC = [(0, 115), (10, 100), (20, 120), (30, 100), (40, 116), (50, 100), (60, 112), (70, 100), (78, 106)]
SYM = [(0, 95), (10, 120), (20, 90), (30, 116), (40, 94), (50, 112), (60, 98), (70, 108), (78, 103)]
ASC_UP = ASC[:-1] + [(80, 102), (90, 116)]  # close crosses 111 * 1.005 at bar 87
ASC_DOWN = ASC[:-1] + [(80, 102), (88, 107), (96, 92)]  # close < S * 0.995 at bar 91
RECT = [(0, 95), (10, 110), (20, 95), (30, 110), (40, 95), (50, 110), (60, 95), (70, 110), (78, 102)]
RISING_WEDGE = [(0, 92), (10, 110), (20, 95), (30, 114), (40, 101), (50, 118), (60, 107), (70, 122), (78, 116)]
BROADENING = [(0, 100), (10, 110), (20, 98), (30, 113), (40, 95), (50, 116), (60, 92), (70, 119), (78, 105)]

REQUIRED = ("pattern_type", "triangle_type", "bias", "status", "stale_reason", "breakout_direction", "symbol",
            "timeframe", "start_time", "end_time", "resistance_start_time", "resistance_start_price",
            "resistance_end_time", "resistance_end_price", "support_start_time", "support_start_price",
            "support_end_time", "support_end_price", "touches", "touches_resistance", "touches_support",
            "touches_total", "apex_time", "apex_price", "apex_progress", "converge_ratio", "height_start",
            "height_start_pct", "current_close", "distance_to_resistance", "distance_to_support", "breakout_time",
            "breakout_price", "target", "target_conservative", "target_direction", "invalidation_price", "score",
            "touch_score", "fit_score", "convergence_score", "fill_score", "breakout_score", "clarity_score",
            "pattern_id", "as_of_time")


def det(wp, cfg=None, n=None):
    df = make_df(wp, n=n, timeframe="4h")
    return df, detect_triangles(df, "4h", cfg, symbol="T")


def log_line(r, side):
    """(intercept at the start bar, slope per bar) in log price, from the row's two end points."""
    t0, t1 = pd.Timestamp(r[f"{side}_start_time"]), pd.Timestamp(r[f"{side}_end_time"])
    bars = (t1 - t0) / pd.Timedelta(hours=4)
    a = math.log(r[f"{side}_start_price"])
    return a, (math.log(r[f"{side}_end_price"]) - a) / bars


# ------------------------------------------------------------------ the three types pass

@pytest.mark.parametrize("wp,ttype,bias", [(ASC, "ascending", "up"), (DESC, "descending", "down"),
                                           (SYM, "symmetrical", "neutral")])
def test_each_type_is_found_forming(wp, ttype, bias):
    df, res = det(wp)
    assert len(res) == 1
    r = res[0]
    assert (r["triangle_type"], r["bias"], r["status"], r["breakout_direction"]) == (ttype, bias, "forming", None)
    assert r["pattern_type"] == "triangle"
    assert r["start_time"] == df["timestamp"].iloc[10].strftime("%Y-%m-%dT%H:%M:%SZ")
    assert r["touches_total"] == 7 and {r["touches_resistance"], r["touches_support"]} == {3, 4}
    assert {t["side"] for t in r["touches"]} == {"resistance", "support"}
    assert 0.25 <= r["apex_progress"] < 1
    assert r["target_estimated"] is True and r["breakout_time"] is None
    assert 0 <= r["score"] <= 100
    assert r["score"] == pytest.approx(sum(r[k] for k, _ in tri.SCORE_PARTS), abs=0.02)
    for k in REQUIRED:
        assert k in r, k
    json.dumps(r, allow_nan=False)  # JSON-ready


@pytest.mark.parametrize("wp", [RECT, RISING_WEDGE, BROADENING], ids=["rectangle", "rising_wedge", "broadening"])
def test_non_triangles_are_rejected(wp):
    _, res = det(wp, {"include_stale": True})
    assert res == []


def test_classification_boundaries():
    cfg = TRIANGLE_CONFIG
    assert classify(0.15, 0.30, 0.0, 0.01, cfg) == "ascending"
    assert classify(-0.15, 0.30, 0.0, 0.01, cfg) == "ascending"
    assert classify(0.151, 0.30, 0.0, 0.01, cfg) is None
    assert classify(0.0, 0.299, 0.0, 0.01, cfg) is None
    assert classify(-0.30, 0.15, -0.01, 0.0, cfg) == "descending"
    assert classify(-0.299, 0.0, -0.01, 0.0, cfg) is None
    assert classify(-0.20, 0.20, -0.01, 0.01, cfg) == "symmetrical"
    assert classify(-0.20, 0.20, -0.03, 0.01, cfg) == "symmetrical"  # slope ratio exactly 3
    assert classify(-0.20, 0.20, -0.0301, 0.01, cfg) is None  # ratio > 3
    assert classify(-0.199, 0.25, -0.01, 0.01, cfg) is None
    assert classify(-0.18, 0.50, -0.01, 0.02, cfg) is None  # resistance neither flat nor sloped enough
    assert classify(0.40, 0.60, 0.01, 0.02, cfg) is None  # rising wedge (both up)


# ------------------------------------------------------------------ convergence / apex

def test_lines_converge_to_the_apex_on_the_right():
    df, (r,) = det(ASC)
    s = 10
    last = len(df) - 1
    aR, sR = log_line(r, "resistance")
    aS, sS = log_line(r, "support")
    assert sS > sR  # converging
    apex = s + (aR - aS) / (sS - sR)
    assert apex > last
    assert r["apex_bars_ahead"] == pytest.approx(apex - last, abs=0.01)
    assert r["apex_progress"] == pytest.approx((last - s) / (apex - s), abs=1e-3)
    assert r["converge_ratio"] == pytest.approx(1 - r["apex_progress"], abs=1e-3)
    assert r["apex_price"] == pytest.approx(math.exp(aR + sR * (apex - s)), rel=1e-6)
    assert r["apex_price"] == pytest.approx(math.exp(aS + sS * (apex - s)), rel=1e-6)
    assert pd.Timestamp(r["apex_time"]) > pd.Timestamp(r["as_of_time"])
    assert r["height_start_pct"] == pytest.approx(math.expm1(aR - aS), abs=2e-6)


def test_reaching_the_apex_without_breakout_is_stale():
    wp = ASC[:-1] + [(80, 102), (86, 109.0), (140, 109.0)]  # flat after the last trough, through the apex
    cfg = {"breakout_buffer_by_timeframe": {"4h": 0.2}}  # nothing counts as a breakout
    _, res = det(wp, {**cfg, "include_stale": True})
    assert [(r["status"], r["stale_reason"]) for r in res] == [("stale", "apex")]
    assert res[0]["apex_progress"] >= 1
    _, res2 = det(wp, cfg)
    assert res2 == []


def test_too_young_pattern_is_not_reported():
    # cut right after the 5th pivot is confirmed: p < 0.25 -> not converged enough yet
    df = make_df(ASC, timeframe="4h")
    cfg = triangle_config({"min_apex_progress": 0.9})
    assert detect_triangles(df, "4h", cfg, symbol="T") == []


# ------------------------------------------------------------------ enclosure tolerance

@pytest.mark.parametrize("below,found", [(0.004, True), (0.007, False)])
def test_close_tolerance_below_support(below, found):
    df, (r,) = det(ASC)
    aS, sS = log_line(r, "support")
    s_45 = math.exp(aS + sS * (45 - 10))
    df = df.copy()
    df.loc[45, "close"] = s_45 * (1 - below)
    df.loc[45, "low"] = s_45 * (1 - below - 0.0005)  # still within the 1% wick tolerance, not a new pivot
    df.loc[45, "open"] = df.loc[45, "close"]
    res = detect_triangles(df, "4h", symbol="T")
    assert bool(res) is found


# ------------------------------------------------------------------ breakout up / down, stale

def test_breakout_up_targets_and_invalidation():
    df, res = det(ASC_UP)
    assert len(res) == 1
    r = res[0]
    ts = df["timestamp"]
    assert (r["status"], r["breakout_direction"], r["target_direction"]) == ("breakout_up", "up", "up")
    assert r["breakout_time"] == ts.iloc[87].strftime("%Y-%m-%dT%H:%M:%SZ")
    assert r["bars_since_breakout"] == 3
    aR, sR = log_line(r, "resistance")
    assert r["breakout_price"] == pytest.approx(math.exp(aR + sR * (87 - 10)), rel=1e-6)
    assert df["close"].iloc[87] > r["breakout_price"] * 1.005 >= df["close"].iloc[86]
    assert r["target"] == pytest.approx(r["breakout_price"] + r["target_height"], rel=1e-9)
    assert r["target_conservative"] == pytest.approx(r["breakout_price"] + 0.70 * r["target_height"], rel=1e-9)
    assert r["target_hit_rate"] == 0.70
    # ascending: height = resistance at the start - lowest pivot low (90)
    assert r["target_height"] == pytest.approx(r["resistance_start_price"] - 90.0, rel=1e-9)
    assert r["invalidation_price"] == pytest.approx(102.0)  # newest pivot low
    assert r["breakout_score"] == 20
    assert r["target_estimated"] is False


def test_breakout_down():
    df, res = det(ASC_DOWN)
    assert len(res) == 1
    r = res[0]
    assert (r["status"], r["breakout_direction"], r["target_direction"]) == ("breakout_down", "down", "down")
    aS, sS = log_line(r, "support")
    b = int(np.flatnonzero(df["timestamp"] == pd.Timestamp(r["breakout_time"]))[0])
    assert b == 91 and r["bars_since_breakout"] == 5
    assert r["breakout_price"] == pytest.approx(math.exp(aS + sS * (b - 10)), rel=1e-6)
    assert r["target"] == pytest.approx(r["breakout_price"] - r["target_height"], rel=1e-9)
    assert r["target_conservative"] == pytest.approx(r["breakout_price"] - 0.44 * r["target_height"], rel=1e-9)
    aR, sR = log_line(r, "resistance")
    assert r["invalidation_price"] == pytest.approx(math.exp(aR + sR * (b - 10)), rel=1e-6)  # ascending: R at b


def test_wick_breakout_option_is_not_later():
    df = make_df(ASC_UP, timeframe="4h")
    close_b = detect_triangles(df, "4h", symbol="T")[0]["breakout_time"]
    high_b = detect_triangles(df, "4h", {"breakout_price": "high"}, symbol="T")[0]
    assert high_b["breakout_basis"] == "high"
    assert pd.Timestamp(high_b["breakout_time"]) <= pd.Timestamp(close_b)


def test_old_breakout_is_stale():
    wp = ASC_UP + [(100, 125)]  # 13 bars after the breakout (4h limit 6)
    _, res = det(wp, {"include_stale": True})
    assert [(r["status"], r["stale_reason"]) for r in res] == [("stale", "breakout_age")]
    assert det(wp)[1] == []


def test_failed_breakout_is_stale():
    wp = ASC_UP[:-1] + [(89, 114.5), (93, 95)]  # breaks out, then closes deep back inside
    _, res = det(wp, {"include_stale": True})
    assert [(r["status"], r["stale_reason"]) for r in res] == [("stale", "failed_breakout")]


# ------------------------------------------------------------------ de-duplication

def _row(start, end, touches, err, symbol="X", tf="4h", score=50.0):
    return {"symbol": symbol, "timeframe": tf, "start_time": start, "end_time": end, "touches_total": touches,
            "fit_error_resistance": err, "fit_error_support": err, "score": score}


def test_dedup_order_touches_then_fit_then_newer():
    a = _row("2024-01-01T00:00:00Z", "2024-02-01T00:00:00Z", 6, 0.1)
    b = _row("2024-01-05T00:00:00Z", "2024-02-01T00:00:00Z", 7, 0.5)
    assert deduplicate_triangles([a, b]) == [b]  # more touches
    c = _row("2024-01-05T00:00:00Z", "2024-02-01T00:00:00Z", 6, 0.05)
    assert deduplicate_triangles([a, c]) == [c]  # same touches, smaller fit error
    d = _row("2024-01-03T00:00:00Z", "2024-02-01T00:00:00Z", 6, 0.1)
    assert deduplicate_triangles([a, d]) == [d]  # same touches and error: the newer start
    e = _row("2024-03-01T00:00:00Z", "2024-04-01T00:00:00Z", 5, 0.9)
    assert sorted(x["start_time"] for x in deduplicate_triangles([a, e])) == [a["start_time"], e["start_time"]]
    f = _row("2024-01-05T00:00:00Z", "2024-02-01T00:00:00Z", 7, 0.5, tf="1d")
    assert len(deduplicate_triangles([a, f])) == 2  # other timeframe


def test_detector_keeps_one_of_the_nested_candidates():
    df = make_df(ASC_UP, timeframe="4h")
    cands = find_triangle_candidates(df, "4h", symbol="T")
    assert len(cands) >= 2  # starts at bar 10, 20, ... all describe the same triangle
    (r,) = detect_triangles(df, "4h", symbol="T")
    assert r["touches_total"] == max(c["touches_total"] for c in cands)


# ------------------------------------------------------------------ no look-ahead (truncation)

def _times_ok(res, df, t):
    now = df["timestamp"].iloc[t]
    confirmed = df["timestamp"].iloc[t - N]
    for r in res:
        for k in ("start_time", "end_time", "breakout_time", "signal_time", "as_of_time"):
            if r[k] is not None:
                assert pd.Timestamp(r[k]) <= now, (t, k)
        assert pd.Timestamp(r["as_of_time"]) == now
        for touch in r["touches"]:
            assert pd.Timestamp(touch["time"]) <= confirmed  # only confirmed pivots


def test_truncation_breakout_never_repaints():
    df = make_df(ASC_UP, timeframe="4h")
    ts = df["timestamp"]
    seen = None
    for t in range(40, len(df)):
        part = df.iloc[:t + 1]
        res = detect_triangles(part, "4h", {"include_stale": True}, symbol="T")
        _times_ok(res, df, t)
        mutated = df.copy()
        mutated.loc[t + 1:, ["open", "high", "low", "close"]] *= 0.5
        assert detect_triangles(mutated.iloc[:t + 1], "4h", {"include_stale": True}, symbol="T") == res
        brk = [r for r in res if r["breakout_time"]]
        if t < 87:
            assert brk == [], t
        else:
            assert len(brk) == 1
            key = (brk[0]["start_time"], brk[0]["breakout_time"], brk[0]["breakout_price"], brk[0]["touches_total"])
            seen = seen or key
            assert key == seen, t  # the first breakout and its lines never change
    assert seen is not None and seen[1] == ts.iloc[87].strftime("%Y-%m-%dT%H:%M:%SZ")


def test_random_walk_truncation_invariants():
    df = random_walk_df(700, seed=3)
    for t in range(150, len(df), 23):
        res = detect_triangles(df.iloc[:t + 1], "4h", {"include_stale": True}, symbol="RW")
        _times_ok(res, df, t)


# ------------------------------------------------------------------ config / speed

def test_triangle_config_is_separate_and_market_aware():
    from ihs_scanner.config import DEFAULT_CONFIG
    assert "max_fit_removals" not in DEFAULT_CONFIG and "min_head_atr" not in TRIANGLE_CONFIG
    assert triangle_config(market="tw")["max_pattern_bars_by_timeframe"] == {"4h": 120, "1d": 120, "1w": 60}
    assert triangle_config()["max_pattern_bars_by_timeframe"]["4h"] == 200
    with pytest.raises(KeyError):
        triangle_config({"no_such_key": 1})


def test_speed_1000_bars():
    import time
    df = random_walk_df(1000, seed=1)
    t0 = time.perf_counter()
    detect_triangles(df, "4h", symbol="RW")
    assert time.perf_counter() - t0 < 1.0  # target 50 ms (docs/current-rules.md), loose here for slow CI


# ------------------------------------------------------------------ build_site output and failure isolation

FRAMES = {
    ("AAAUSDT", "4h"): lambda: make_df(ASC, timeframe="4h"),
    ("AAAUSDT", "1d"): lambda: make_df(DESC, timeframe="1d"),
    ("BBBUSDT", "4h"): lambda: make_df(ASC_UP, timeframe="4h"),
    ("BBBUSDT", "1d"): lambda: make_df([(0, 100), (60, 130)], timeframe="1d"),
}


def _fake_fetch(symbol, timeframe, limit=1000, **kwargs):
    return FRAMES[(symbol, timeframe)]()


def _build(tmp_path, monkeypatch, *extra):
    monkeypatch.setattr(build_site, "fetch_klines", _fake_fetch)
    monkeypatch.delenv("GITHUB_EVENT_NAME", raising=False)
    out = tmp_path / "data" / "scan.json"
    code = build_site.main(["--symbols", "AAAUSDT,BBBUSDT", "--timeframes", "4h,1d", "--out", str(out), *extra])
    return code, out


def test_build_writes_triangles_json_and_bars(tmp_path, monkeypatch):
    code, out = _build(tmp_path, monkeypatch)
    assert code == 0 and out.exists()
    path = out.parent / "triangles.json"
    text = path.read_text(encoding="utf-8")
    assert "NaN" not in text
    data = json.loads(text)
    assert data["kind"] == "triangles" and data["market"] == "crypto"
    assert data["schema_version"] == build_site.TRI_SCHEMA_VERSION
    assert data["settings"]["config"]["touch_min_pct"] == 0.005
    got = {(r["symbol"], r["timeframe"], r["triangle_type"], r["status"]) for r in data["results"]}
    assert got == {("AAAUSDT", "4h", "ascending", "forming"), ("AAAUSDT", "1d", "descending", "forming"),
                   ("BBBUSDT", "4h", "ascending", "breakout_up")}
    assert [r["rank"] for r in data["results"]] == [1, 2, 3]
    assert [r["score"] for r in data["results"]] == sorted((r["score"] for r in data["results"]), reverse=True)
    for r in data["results"]:
        assert r["pattern_key"] == f"tri|binance|spot|{r['symbol']}|{r['timeframe']}|{r['start_time']}"
        assert r["pattern_id"].startswith("binance|spot|")
        assert r["exchange"] == "binance" and r["tv_symbol"] == f"BINANCE:{r['symbol']}"
        assert r["chart_file"].startswith("tri_bars/")
        c = json.loads((out.parent / r["chart_file"]).read_text(encoding="utf-8"))
        for k in ("bars", "resistance_line", "resistance_ext", "support_line", "support_ext", "future_times",
                  "touches", "breakout", "levels", "levels_estimated", "apex"):
            assert k in c, k
        times = [b[0] for b in c["bars"]]
        assert c["resistance_line"][0]["time"] == build_site._unix(r["start_time"]) in times
        assert c["resistance_line"][-1]["time"] == build_site._unix(r["end_time"])
        assert c["resistance_line"][0]["price"] == pytest.approx(r["resistance_start_price"])
        assert c["support_line"][-1]["price"] == pytest.approx(r["support_end_price"])
        assert all(t > times[-1] for t in c["future_times"])
        assert c["future_times"] == sorted(set(c["future_times"]))
        ext_t = [p["time"] for p in c["resistance_ext"] if p["time"] > times[-1]]
        assert set(ext_t) <= set(c["future_times"])  # every future line point sits on a future bar
        if c["apex"]["shown"]:
            assert c["resistance_ext"][-1]["price"] == c["support_ext"][-1]["price"] == pytest.approx(r["apex_price"])
        assert {lv["key"] for lv in c["levels"]} == {"target", "target_conservative", "invalidation_price"}
        assert len(c["touches"]) == r["touches_total"]
        assert set(r["shape"]) == {"resistance", "support", "breakout", "last"}
    brk = next(r for r in data["results"] if r["status"] == "breakout_up")
    c = json.loads((out.parent / brk["chart_file"]).read_text(encoding="utf-8"))
    assert c["breakout"]["direction"] == "up" and c["levels_estimated"] is False
    # the IHS scan.json is untouched by the triangle step
    scan = json.loads(out.read_text(encoding="utf-8"))
    assert "triangles" not in scan and scan["schema_version"] == build_site.SCHEMA_VERSION


def test_old_tri_bars_are_replaced(tmp_path, monkeypatch):
    stale = tmp_path / "data" / "tri_bars" / "old.json"
    stale.parent.mkdir(parents=True)
    stale.write_text("{}", encoding="utf-8")
    code, out = _build(tmp_path, monkeypatch)
    assert code == 0 and not stale.exists()


def test_triangle_detector_crash_does_not_fail_ihs(tmp_path, monkeypatch, capsys):
    def boom(*a, **k):
        raise RuntimeError("triangle bug")
    monkeypatch.setattr(tri, "detect_triangles", boom)
    old = tmp_path / "data" / "triangles.json"
    old.parent.mkdir(parents=True)
    old.write_text('{"old": true}', encoding="utf-8")
    code, out = _build(tmp_path, monkeypatch)
    assert code == 0 and json.loads(out.read_text(encoding="utf-8"))["results"] is not None
    assert json.loads(old.read_text(encoding="utf-8")) == {"old": True}  # > 20% failed: previous file kept
    assert "[triangles FAIL]" in capsys.readouterr().err


def test_triangle_step_crash_does_not_fail_ihs(tmp_path, monkeypatch, capsys):
    def boom(*a, **k):
        raise RuntimeError("payload bug")
    monkeypatch.setattr(build_site, "build_triangle_payload", boom)
    code, out = _build(tmp_path, monkeypatch)
    assert code == 0 and out.exists() and not (out.parent / "triangles.json").exists()
    assert "payload bug" in capsys.readouterr().err


def test_one_failing_series_is_recorded(tmp_path, monkeypatch):
    real = tri.detect_triangles

    def flaky(df, tf, cfg=None, symbol=""):
        if symbol == "AAAUSDT" and tf == "1d":
            raise ValueError("bad series")
        return real(df, tf, cfg, symbol=symbol)
    monkeypatch.setattr(tri, "detect_triangles", flaky)
    monkeypatch.setattr(build_site, "MAX_FAILURE_RATIO", 0.5)
    code, out = _build(tmp_path, monkeypatch)
    data = json.loads((out.parent / "triangles.json").read_text(encoding="utf-8"))
    assert code == 0
    assert [(f["symbol"], f["timeframe"], f["stage"]) for f in data["failures"]] == [("AAAUSDT", "1d", "triangle")]
    assert {(r["symbol"], r["timeframe"]) for r in data["results"]} == {("AAAUSDT", "4h"), ("BBBUSDT", "4h")}


def test_publish_merge_keeps_the_other_markets_triangles(tmp_path):
    import scripts.merge_published as mp

    def write(root, rel, text="x"):
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")

    def tree(root):
        return {p.relative_to(root).as_posix(): p.read_text(encoding="utf-8") for p in root.rglob("*") if p.is_file()}

    prev, new, out = tmp_path / "prev", tmp_path / "new", tmp_path / "out"
    write(prev, "data/scan.json", "crypto")
    write(prev, "data/triangles.json", "crypto tri")
    write(prev, "data/tri_bars/a.json", "crypto tri bar")
    write(prev, "data/tw/triangles.json", "tw tri old")
    write(new, "data/tw/scan.json", "tw")
    write(new, "data/tw/triangles.json", "tw tri new")
    write(new, "data/triangles.json", "STALE crypto copy")
    mp.merge_tree(new, prev, out, "tw", mp.OWNED["site"])
    t = tree(out)
    assert t["data/triangles.json"] == "crypto tri" and t["data/tri_bars/a.json"] == "crypto tri bar"
    assert t["data/tw/triangles.json"] == "tw tri new"
    out2, new2 = tmp_path / "out2", tmp_path / "new2"
    write(new2, "data/scan.json", "crypto 2")
    write(new2, "data/triangles.json", "crypto tri 2")
    mp.merge_tree(new2, out, out2, "crypto", mp.OWNED["site"])
    t2 = tree(out2)
    assert t2["data/triangles.json"] == "crypto tri 2" and "data/tri_bars/a.json" not in t2
    assert t2["data/tw/triangles.json"] == "tw tri new"
    # scan-state branch: crypto owns site/data/triangles.json + site/data/tri_bars
    assert {"site/data/triangles.json", "site/data/tri_bars"} <= set(mp.OWNED["state"]["crypto"])


def test_tw_build_writes_its_own_triangles(tmp_path, monkeypatch):
    from argparse import Namespace

    import build_site_tw
    from ihs_scanner.sources import Instrument
    monkeypatch.delenv("GITHUB_EVENT_NAME", raising=False)
    insts = {"twse": [Instrument(base="2330", symbol="2330", native="2330.TW", exchange="twse", market="stock",
                                 name="台積電")],
             "tpex": [Instrument(base="6488", symbol="6488", native="6488.TWO", exchange="tpex", market="stock",
                                 name="環球晶")]}

    def lister(session, exchange, include_etf=False):
        return insts[exchange]

    def fetch(inst, timeframes, limit, client):
        return {tf: make_df(ASC if inst.symbol == "2330" else DESC, timeframe=tf) for tf in timeframes}, {}

    class Client:
        requests = rate_limited = 0

    out = tmp_path / "data" / "tw" / "scan.json"
    args = Namespace(timeframes="4h", limit=1000, tw_include_etf=False, symbols="all", workers=1, include_stale=False,
                     scheduled=False, out=str(out), tw_rps=None)
    assert build_site_tw.main_tw(args, client=Client(), lister=lister, fetch=fetch) == 0
    data = json.loads((out.parent / "triangles.json").read_text(encoding="utf-8"))
    assert data["market"] == "tw" and not (tmp_path / "data" / "triangles.json").exists()
    assert data["settings"]["config"]["max_pattern_bars_by_timeframe"]["4h"] == 120  # TW override
    got = {(r["symbol"], r["name"], r["exchange"], r["market"], r["triangle_type"]) for r in data["results"]}
    assert got == {("2330", "台積電", "twse", "stock", "ascending"), ("6488", "環球晶", "tpex", "stock", "descending")}
    for r in data["results"]:
        assert r["tv_symbol"] == ("TWSE:" if r["exchange"] == "twse" else "TPEX:") + r["symbol"]
        assert r["pattern_key"].startswith(f"tri|{r['exchange']}|stock|{r['symbol']}|4h|")
        assert (out.parent / r["chart_file"]).exists()



def test_invalidation_is_on_the_other_side_of_the_breakout():  # D41 fix: real TW 6143 1d (TPEx)
    """Descending triangle broken upward close to the apex: the newest support pivot (79.30) sat above the
    resistance at the breakout bar (77.44), so the old invalidation was ABOVE the breakout price."""
    import pandas as pd
    from pathlib import Path
    df = pd.read_csv(Path(__file__).parent / "fixtures" / "6143-TPEX_yahoo_1d_triangle.csv")
    for col in ("timestamp", "close_time"):
        if col in df:
            df[col] = pd.to_datetime(df[col], utc=True)
    res = [r for r in detect_triangles(df, "1d", {"include_stale": True}, symbol="6143") if r.get("breakout_time")]
    assert res, "the 6143 breakout should be detected"
    for r in res:
        if r["breakout_direction"] == "up":
            assert r["invalidation_price"] < r["breakout_price"], r
        else:
            assert r["invalidation_price"] > r["breakout_price"], r
