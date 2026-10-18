import json
import math

import pandas as pd
import pytest

from ihs_scanner.config import DEFAULT_CONFIG, merge_config
from ihs_scanner.detector import deduplicate_patterns
from ihs_scanner.scanner import export_results, scan_symbols, sort_results
from ihs_scanner.scoring import calculate_pattern_score

from .synth import IHS_BREAK, IHS_WATCH, make_df


# ---------- 6. score ----------
def test_pattern_score_by_hand():
    s = calculate_pattern_score(shoulder_diff=0.02, max_allowed_shoulder_diff=0.05,
                                head_depth=0.08, neckline_angle=-10, max_neckline_angle=30,
                                breakout_detected=False, distance_to_neckline=0.03)
    assert s["shoulder_score"] == pytest.approx(18.0)  # (1 - 0.4) * 30
    assert s["head_depth_score"] == pytest.approx(20.0)  # 0.8 * 25
    assert s["neckline_score"] == pytest.approx(25 * 2 / 3)  # (1 - 10/30) * 25
    assert s["breakout_score"] == pytest.approx(14.0)  # (1 - 0.3) * 20
    assert s["pattern_score"] == pytest.approx(18 + 20 + 25 * 2 / 3 + 14)

    s2 = calculate_pattern_score(0.0, 0.10, 0.25, 0.0, 30, True, 0.5)
    assert s2["head_depth_score"] == 25 and s2["breakout_score"] == 20
    assert s2["pattern_score"] == pytest.approx(100.0)

    s3 = calculate_pattern_score(0.05, 0.05, 0.05, 30, 30, False, 0.2)
    assert s3["shoulder_score"] == 0 and s3["neckline_score"] == 0 and s3["breakout_score"] == 0
    assert s3["pattern_score"] == pytest.approx(12.5)


# ---------- 7. ordering ----------
def _r(name, score, brk=False, dist=0.05, vr=None, tf="4h"):
    return {"symbol": name, "timeframe": tf, "pattern_score": score, "breakout_detected": brk,
            "distance_to_neckline": dist, "volume_ratio": vr, "RS_time": "2024"}


def test_sort_tie_breaks():
    rows = [
        _r("low_score", 50, brk=True, dist=0.0, vr=9, tf="1w"),
        _r("tf_4h", 80, brk=True, dist=0.01, vr=2.0, tf="4h"),
        _r("tf_1w", 80, brk=True, dist=0.01, vr=2.0, tf="1w"),
        _r("tf_1d", 80, brk=True, dist=0.01, vr=2.0, tf="1d"),
        _r("vr_low", 80, brk=True, dist=0.01, vr=1.5, tf="1w"),
        _r("vr_none", 80, brk=True, dist=0.01, vr=None, tf="1w"),
        _r("dist_far", 80, brk=True, dist=0.02, vr=5.0, tf="1w"),
        _r("no_brk", 80, brk=False, dist=0.0, vr=None, tf="1w"),
        _r("top", 90),
    ]
    order = [r["symbol"] for r in sort_results(rows)]
    assert order == ["top", "tf_1w", "tf_1d", "tf_4h", "vr_low", "vr_none",
                     "dist_far", "no_brk", "low_score"]


def test_scan_symbols_sorts_and_applies_min_score():
    data = {
        "AAA": {"4h": make_df(IHS_WATCH)},
        "BBB": {"1d": make_df(IHS_BREAK, timeframe="1d")},
    }
    res = scan_symbols(data, {})
    assert len(res) == 2
    assert res[0]["pattern_score"] >= res[1]["pattern_score"]
    assert {r["symbol"] for r in res} == {"AAA", "BBB"}
    cut = res[0]["pattern_score"] - 0.01
    assert [r["symbol"] for r in scan_symbols(data, {"min_pattern_score": cut})] == [res[0]["symbol"]]


def test_merge_config():
    cfg = merge_config({"pivot_window_by_timeframe": {"4h": 4}, "max_neckline_angle": 20})
    assert cfg["pivot_window_by_timeframe"] == {"4h": 4, "1d": 5, "1w": 3}
    assert cfg["max_neckline_angle"] == 20
    assert DEFAULT_CONFIG["pivot_window_by_timeframe"]["4h"] == 5  # default untouched
    with pytest.raises(KeyError):
        merge_config({"typo_key": 1})


# ---------- 9. dedup (unit) ----------
def test_deduplicate_patterns_unit():
    t = pd.Timestamp
    base = {"symbol": "X", "timeframe": "4h"}
    a = {**base, "LS_time": t("2024-01-01", tz="UTC"), "RS_time": t("2024-01-10", tz="UTC"), "pattern_score": 70}
    b = {**base, "LS_time": t("2024-01-05", tz="UTC"), "RS_time": t("2024-01-15", tz="UTC"), "pattern_score": 80}
    c = {**base, "LS_time": t("2024-01-20", tz="UTC"), "RS_time": t("2024-01-30", tz="UTC"), "pattern_score": 60}
    d = {**b, "timeframe": "1d", "pattern_score": 10}  # other timeframe: independent
    e = {**base, "LS_time": t("2024-01-06", tz="UTC"), "RS_time": t("2024-01-16", tz="UTC"), "pattern_score": 80}
    kept = deduplicate_patterns([a, b, c, d])
    assert [k["pattern_score"] for k in kept] == [80, 60, 10]
    kept2 = deduplicate_patterns([b, e])  # same score -> newer RS wins
    assert len(kept2) == 1 and kept2[0] is e


# ---------- 10. export ----------
def test_export_csv_and_json(tmp_path):
    res = scan_symbols({"AAA": {"4h": make_df(IHS_WATCH)}}, {})
    assert len(res) == 1
    res[0]["average_volume_20"] = float("nan")  # NaN must become null
    jp = export_results(res, tmp_path / "out" / "r.json")
    loaded = json.loads(jp.read_text(encoding="utf-8"))
    assert loaded[0]["breakout_time"] is None and loaded[0]["volume_ratio"] is None
    assert loaded[0]["average_volume_20"] is None
    assert loaded[0]["LS_time"] == "2024-01-04T08:00:00Z"
    assert loaded[0]["breakout_detected"] is False
    assert loaded[0]["pattern_id"] == res[0]["pattern_id"]

    cp = export_results(res, tmp_path / "r.csv")
    frame = pd.read_csv(cp, encoding="utf-8")
    assert len(frame) == 1 and frame.loc[0, "symbol"] == "AAA"
    assert math.isnan(frame.loc[0, "breakout_price"])
    assert frame.loc[0, "RS_time"] == "2024-01-14T08:00:00Z"

    empty = export_results([], tmp_path / "empty.csv")
    assert "pattern_score" in empty.read_text(encoding="utf-8")
    assert json.loads(export_results([], tmp_path / "empty.json").read_text(encoding="utf-8")) == []
    with pytest.raises(ValueError):
        export_results(res, tmp_path / "r.txt")
