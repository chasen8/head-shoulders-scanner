"""build_site.py: scan.json schema, NaN -> null, chart bars range, neckline end points.

No network: fetch_klines is monkeypatched with synthetic frames.
"""
import json
import math
from datetime import datetime, timezone

import pandas as pd
import pytest

import build_site
from ihs_scanner.data_binance import BinanceFetchError
from ihs_scanner.neckline import calculate_neckline
from ihs_scanner.scanner import OUTPUT_FIELDS
from ihs_scanner.swings import get_timestamps

from .synth import IHS_BREAK, IHS_WATCH, make_df

FRAMES = {
    ("AAAUSDT", "4h"): lambda: make_df(IHS_WATCH, timeframe="4h"),
    ("AAAUSDT", "1d"): lambda: make_df(IHS_BREAK, timeframe="1d"),
    ("BBBUSDT", "4h"): lambda: make_df(IHS_BREAK, timeframe="4h"),
    ("BBBUSDT", "1d"): lambda: make_df([(0, 100), (60, 130)], timeframe="1d"),  # no pattern
}


def _fake_fetch(symbol, timeframe, limit=1000, **kwargs):
    df = FRAMES[(symbol, timeframe)]()
    if symbol == "AAAUSDT" and timeframe == "4h":
        df.loc[10, "volume"] = 0.0  # zero-volume bars: keeps the frame valid, exercises averages
    return df


@pytest.fixture
def scan_json(tmp_path, monkeypatch):
    monkeypatch.setattr(build_site, "fetch_klines", _fake_fetch)
    monkeypatch.delenv("GITHUB_EVENT_NAME", raising=False)
    out = tmp_path / "data" / "scan.json"
    code = build_site.main(["--symbols", "AAAUSDT,BBBUSDT", "--timeframes", "4h,1d", "--out", str(out),
                            "--scheduled"])
    assert code == 0
    with open(out, encoding="utf-8") as fh:
        text = fh.read()
    return text, json.loads(text)


def test_schema_top_level(scan_json):
    text, data = scan_json
    assert "NaN" not in text and "Infinity" not in text
    for key in ("schema_version", "generated_at", "next_scan_at", "settings", "last_closed", "results"):
        assert key in data
    assert data["generated_at"].endswith("Z")
    gen = datetime.strptime(data["generated_at"], "%Y-%m-%dT%H:%M:%SZ")
    nxt = datetime.strptime(data["next_scan_at"], "%Y-%m-%dT%H:%M:%SZ")
    assert nxt > gen and nxt.minute == build_site.SCAN_MINUTE and nxt.hour % 4 == 0
    s = data["settings"]
    assert s["symbols"] == ["AAAUSDT", "BBBUSDT"] and s["timeframes"] == ["4h", "1d"]
    assert s["mode"] == "candidate" and s["include_stale"] is False
    expected_last = make_df(IHS_BREAK, timeframe="1d")["timestamp"].iloc[-1].strftime("%Y-%m-%dT%H:%M:%SZ")
    assert data["last_closed"]["AAAUSDT"]["1d"] == expected_last
    assert set(data["last_closed"]["BBBUSDT"]) == {"4h", "1d"}


def test_results_fields_and_nulls(scan_json):
    _, data = scan_json
    res = data["results"]
    assert len(res) >= 3
    assert [r["rank"] for r in res] == list(range(1, len(res) + 1))
    scores = [r["pattern_score"] for r in res]
    assert scores == sorted(scores, reverse=True)
    statuses = {r["status"] for r in res}
    assert statuses <= {"watchlist", "confirmed"}
    for r in res:
        for f in OUTPUT_FIELDS:
            assert f in r
        assert r["LS_time"].endswith("Z") and r["as_of_time"].endswith("Z")
        assert r["neckline_side"] in ("above", "below", "at")
        side = "above" if r["current_close"] > r["current_neckline_price"] else "below"
        assert r["neckline_side"] == side
        if not r["breakout_detected"]:
            # NaN / None -> JSON null
            assert r["breakout_time"] is None and r["volume_ratio"] is None
            assert r["average_volume_20"] is None and r["chart"]["breakout"] is None
        else:
            assert r["chart"]["breakout"]["price"] == pytest.approx(r["breakout_price"])


def test_nan_becomes_null(monkeypatch):
    df = make_df(IHS_BREAK, timeframe="4h")
    df["volume"] = 0.0  # avg volume 0 -> ratio undefined
    cfg = build_site.merge_config({})
    payload = build_site.build_payload({"ZZZUSDT": {"4h": df}}, cfg, symbols=["ZZZUSDT"],
                                       timeframes=["4h"], limit=1000,
                                       generated_at=datetime(2026, 1, 1, 3, 0, tzinfo=timezone.utc),
                                       scheduled=True)
    r = payload["results"][0]
    assert r["breakout_detected"] is True and r["volume_ratio"] is None
    assert payload["next_scan_at"] == "2026-01-01T04:07:00Z"
    json.dumps(payload, allow_nan=False)  # must not raise


def test_chart_bars_cover_ls_to_last(scan_json):
    _, data = scan_json
    for r in data["results"]:
        df = FRAMES[(r["symbol"], r["timeframe"])]()
        ts = get_timestamps(df)
        unix = [int(t.value // 1_000_000_000) for t in ts]
        bars = r["chart"]["bars"]
        times = [b[0] for b in bars]
        assert all(len(b) == 6 for b in bars)
        assert times == sorted(times) and len(set(times)) == len(times)
        ls = int(pd.Timestamp(r["LS_time"]).value // 1_000_000_000)
        ls_pos = unix.index(ls)
        assert times[0] == unix[max(0, ls_pos - build_site.BARS_BEFORE_LS)]
        assert times[-1] == unix[-1]  # last closed bar
        assert ls in times
        first = unix.index(times[0])
        assert bars[0][1:5] == pytest.approx(
            [df["open"].iloc[first], df["high"].iloc[first], df["low"].iloc[first], df["close"].iloc[first]])
        pts = r["chart"]["points"]
        assert [p["label"] for p in pts] == ["LS", "N1", "H", "N2", "RS"]
        for p in pts:
            assert p["time"] in times
            assert p["price"] == pytest.approx(r[f"{p['label']}_price"])


def test_neckline_endpoints_match_neckline_price_at(scan_json):
    _, data = scan_json
    for r in data["results"]:
        df = FRAMES[(r["symbol"], r["timeframe"])]()
        ts = get_timestamps(df)
        pos = {k: int(ts.get_loc(pd.Timestamp(r[f"{k}_time"]))) for k in ("LS", "N1", "N2")}
        neck = calculate_neckline((pos["N1"], r["N1_price"]), (pos["N2"], r["N2_price"]))
        start, end = r["chart"]["neckline"]
        assert start["time"] == int(ts[pos["LS"]].value // 1_000_000_000)
        assert start["price"] == pytest.approx(neck.neckline_price_at(pos["LS"]))
        assert end["time"] == int(ts[-1].value // 1_000_000_000)
        assert end["price"] == pytest.approx(neck.neckline_price_at(len(df) - 1))
        assert end["price"] == pytest.approx(r["current_neckline_price"])


def test_next_scan_only_when_scheduled(tmp_path, monkeypatch):
    monkeypatch.setattr(build_site, "fetch_klines", _fake_fetch)
    out = tmp_path / "scan.json"
    args = ["--symbols", "AAAUSDT", "--timeframes", "4h", "--out", str(out)]
    monkeypatch.delenv("GITHUB_EVENT_NAME", raising=False)
    assert build_site.main(args) == 0
    assert json.loads(out.read_text(encoding="utf-8"))["next_scan_at"] is None
    monkeypatch.setenv("GITHUB_EVENT_NAME", "schedule")
    assert build_site.main(args) == 0
    assert json.loads(out.read_text(encoding="utf-8"))["next_scan_at"].endswith(":07:00Z")
    gen = datetime(2026, 1, 1, 23, 59, tzinfo=timezone.utc)
    assert build_site.next_scan_time(gen) == datetime(2026, 1, 2, 0, 7, tzinfo=timezone.utc)
    assert build_site.next_scan_time(datetime(2026, 1, 1, 4, 7, tzinfo=timezone.utc)).hour == 8


def test_fetch_failure_exits_nonzero_and_keeps_old_file(tmp_path, monkeypatch, capsys):
    out = tmp_path / "scan.json"
    out.write_text('{"old": true}', encoding="utf-8")

    def boom(symbol, timeframe, limit=1000, **kwargs):
        raise BinanceFetchError("all kline endpoints failed: timeout")

    monkeypatch.setattr(build_site, "fetch_klines", boom)
    code = build_site.main(["--symbols", "AAAUSDT", "--timeframes", "4h", "--out", str(out)])
    assert code != 0
    assert "NOT updated" in capsys.readouterr().err
    assert out.read_text(encoding="utf-8") == '{"old": true}'


def test_include_stale_flag(tmp_path, monkeypatch):
    stale = make_df(IHS_BREAK + [(110, 125)], timeframe="4h")  # breakout age 18 > 6
    monkeypatch.setattr(build_site, "fetch_klines", lambda s, tf, limit=1000, **k: stale.copy())
    out = tmp_path / "scan.json"
    for flag, expect in (([], 0), (["--include-stale"], 1)):
        assert build_site.main(["--symbols", "SSSUSDT", "--timeframes", "4h", "--out", str(out)] + flag) == 0
        data = json.loads(out.read_text(encoding="utf-8"))
        assert len(data["results"]) == expect
        if expect:
            assert data["results"][0]["status"] == "stale" and data["settings"]["include_stale"] is True
            assert not math.isnan(data["results"][0]["chart"]["neckline"][1]["price"])
