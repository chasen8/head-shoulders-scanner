"""build_site.py + data_binance helpers: split JSON schema, NaN -> null, chart bars range,
neckline end points, partial failures, symbol listing filter, weight throttling.

No network: fetch_klines / list_usdt_symbols are monkeypatched; HTTP uses fake sessions.
"""
import json
import math
from datetime import datetime, timezone

import pandas as pd
import pytest

import build_site
from ihs_scanner import data_binance as db
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


def _read(path):
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    return text, json.loads(text)


def _charts(out, data):
    return {r["pattern_id"]: _read(out.parent / r["chart_file"])[1] for r in data["results"]}


@pytest.fixture
def built(tmp_path, monkeypatch):
    monkeypatch.setattr(build_site, "fetch_klines", _fake_fetch)
    monkeypatch.delenv("GITHUB_EVENT_NAME", raising=False)
    out = tmp_path / "data" / "scan.json"
    code = build_site.main(["--symbols", "AAAUSDT,BBBUSDT", "--timeframes", "4h,1d", "--out", str(out),
                            "--scheduled"])
    assert code == 0
    text, data = _read(out)
    return out, text, data, _charts(out, data)


def test_schema_top_level(built):
    out, text, data, _ = built
    assert "NaN" not in text and "Infinity" not in text
    for key in ("schema_version", "generated_at", "next_scan_at", "settings", "last_closed", "results",
                "failures", "skipped", "scanned_symbols", "requested_symbols", "series_total", "series_scanned"):
        assert key in data
    assert data["schema_version"] == build_site.SCHEMA_VERSION
    assert data["generated_at"].endswith("Z")
    gen = datetime.strptime(data["generated_at"], "%Y-%m-%dT%H:%M:%SZ")
    nxt = datetime.strptime(data["next_scan_at"], "%Y-%m-%dT%H:%M:%SZ")
    assert nxt > gen and nxt.minute == build_site.SCAN_MINUTE and nxt.hour % 4 == 0
    s = data["settings"]
    assert s["symbols"] == ["AAAUSDT", "BBBUSDT"] and s["timeframes"] == ["4h", "1d"]
    assert s["symbols_source"] == "list"
    assert s["mode"] == "candidate" and s["include_stale"] is False
    assert data["scanned_symbols"] == 2 and data["series_total"] == 4 and data["series_scanned"] == 4
    assert data["failures"] == [] and data["skipped"] == []
    expected_last = make_df(IHS_BREAK, timeframe="1d")["timestamp"].iloc[-1].strftime("%Y-%m-%dT%H:%M:%SZ")
    assert data["last_closed"]["1d"] == expected_last
    assert set(data["last_closed"]) == {"4h", "1d"}


def test_scan_json_has_no_bars_and_files_match(built):
    out, text, data, charts = built
    assert '"bars"' not in text  # bars live only in site/data/bars/*.json
    files = sorted(p.name for p in (out.parent / build_site.BARS_DIRNAME).iterdir())
    assert files == sorted(r["chart_file"].split("/", 1)[1] for r in data["results"])
    assert len(set(files)) == len(files)
    for r in data["results"]:
        assert r["chart_file"].startswith("bars/") and r["chart_file"].endswith(".json")
        c = charts[r["pattern_id"]]
        assert c["pattern_id"] == r["pattern_id"] and c["symbol"] == r["symbol"]
        sh = r["shape"]
        assert sh["points"] == [[p["time"], p["price"]] for p in c["points"]]
        assert sh["neckline"] == [[p["time"], p["price"]] for p in c["neckline"]]
        assert sh["last"] == [c["bars"][-1][0], c["bars"][-1][4]]


def test_results_fields_and_nulls(built):
    _, _, data, charts = built
    res = data["results"]
    assert len(res) >= 3
    assert [r["rank"] for r in res] == list(range(1, len(res) + 1))
    scores = [r["pattern_score"] for r in res]
    assert scores == sorted(scores, reverse=True)
    assert {r["status"] for r in res} <= {"watchlist", "confirmed"}
    for r in res:
        for f in OUTPUT_FIELDS:
            assert f in r
        assert r["LS_time"].endswith("Z") and r["as_of_time"].endswith("Z")
        assert r["neckline_side"] in ("above", "below", "at")
        side = "above" if r["current_close"] > r["current_neckline_price"] else "below"
        assert r["neckline_side"] == side
        c = charts[r["pattern_id"]]
        if not r["breakout_detected"]:
            assert r["breakout_time"] is None and r["volume_ratio"] is None
            assert r["average_volume_20"] is None and c["breakout"] is None and r["shape"]["breakout"] is None
        else:
            assert c["breakout"]["price"] == pytest.approx(r["breakout_price"])


def test_nan_becomes_null():
    df = make_df(IHS_BREAK, timeframe="4h")
    df["volume"] = 0.0  # avg volume 0 -> ratio undefined
    cfg = build_site.merge_config({})
    payload, charts = build_site.build_payload({"ZZZUSDT": {"4h": df}}, cfg, symbols=["ZZZUSDT"],
                                               timeframes=["4h"], limit=1000,
                                               generated_at=datetime(2026, 1, 1, 3, 0, tzinfo=timezone.utc),
                                               scheduled=True)
    r = payload["results"][0]
    assert r["breakout_detected"] is True and r["volume_ratio"] is None
    assert payload["next_scan_at"] == "2026-01-01T04:07:00Z"
    json.dumps(payload, allow_nan=False)  # must not raise
    json.dumps(charts, allow_nan=False)


def test_chart_bars_cover_ls_to_last(built):
    _, _, data, charts = built
    for r in data["results"]:
        df = FRAMES[(r["symbol"], r["timeframe"])]()
        ts = get_timestamps(df)
        unix = [int(t.value // 1_000_000_000) for t in ts]
        c = charts[r["pattern_id"]]
        bars = c["bars"]
        times = [b[0] for b in bars]
        assert all(len(b) == 6 for b in bars)
        assert times == sorted(times) and len(set(times)) == len(times)
        ls = int(pd.Timestamp(r["LS_time"]).value // 1_000_000_000)
        ls_pos = unix.index(ls)
        assert times[0] == unix[max(0, ls_pos - build_site.BARS_BEFORE_LS)]
        assert times[-1] == unix[-1]  # last closed bar
        first = unix.index(times[0])
        assert bars[0][1:5] == pytest.approx(
            [df["open"].iloc[first], df["high"].iloc[first], df["low"].iloc[first], df["close"].iloc[first]])
        pts = c["points"]
        assert [p["label"] for p in pts] == ["LS", "N1", "H", "N2", "RS"]
        for p in pts:
            assert p["time"] in times
            assert p["price"] == pytest.approx(r[f"{p['label']}_price"])


def test_neckline_endpoints_match_neckline_price_at(built):
    _, _, data, charts = built
    for r in data["results"]:
        df = FRAMES[(r["symbol"], r["timeframe"])]()
        ts = get_timestamps(df)
        pos = {k: int(ts.get_loc(pd.Timestamp(r[f"{k}_time"]))) for k in ("LS", "N1", "N2")}
        neck = calculate_neckline((pos["N1"], r["N1_price"]), (pos["N2"], r["N2_price"]))
        start, end = charts[r["pattern_id"]]["neckline"]
        assert start["time"] == int(ts[pos["LS"]].value // 1_000_000_000)
        assert start["price"] == pytest.approx(neck.neckline_price_at(pos["LS"]))
        assert end["time"] == int(ts[-1].value // 1_000_000_000)
        assert end["price"] == pytest.approx(neck.neckline_price_at(len(df) - 1))
        assert end["price"] == pytest.approx(r["current_neckline_price"])


def test_old_bars_files_are_removed(tmp_path, monkeypatch):
    monkeypatch.setattr(build_site, "fetch_klines", _fake_fetch)
    out = tmp_path / "scan.json"
    leftover = tmp_path / "bars" / "OLD_4h_1_deadbeef.json"
    leftover.parent.mkdir()
    leftover.write_text("{}", encoding="utf-8")
    (tmp_path / "bars.tmp").mkdir()  # crashed previous run
    assert build_site.main(["--symbols", "AAAUSDT", "--timeframes", "4h", "--out", str(out)]) == 0
    assert not leftover.exists() and not (tmp_path / "bars.tmp").exists()
    _, data = _read(out)
    assert len(list((tmp_path / "bars").iterdir())) == len(data["results"]) >= 1


def test_chart_file_name_is_safe_and_unique():
    base = {"symbol": "币安人生USDT", "timeframe": "4h", "LS_time": "2026-01-01T00:00:00Z",
            "pattern_id": "币安人生USDT|4h|2026-01-01T00:00:00Z|a|b"}
    name = build_site.chart_file_name(base)
    assert name.isascii() and name.endswith(".json") and "|" not in name and ":" not in name
    other = build_site.chart_file_name({**base, "symbol": "牛来USDT", "pattern_id": "牛来USDT|4h|x"})
    assert other != name


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


def _symbols(n):
    return [f"S{i:02d}USDT" for i in range(n)]


def _fetch_failing(bad):
    def fetch(symbol, timeframe, limit=1000, **kwargs):
        if symbol in bad:
            raise BinanceFetchError("all endpoints failed: HTTP 400 Invalid symbol")
        return make_df(IHS_BREAK, timeframe=timeframe)
    return fetch


def test_partial_failure_recorded(tmp_path, monkeypatch):
    syms = _symbols(10)
    monkeypatch.setattr(build_site, "fetch_klines", _fetch_failing({"S03USDT"}))  # 10% of series fail
    monkeypatch.setattr(build_site, "list_usdt_symbols", lambda **k: syms)
    out = tmp_path / "scan.json"
    assert build_site.main(["--sources", "binance-spot", "--timeframes", "4h", "--out", str(out)]) == 0
    _, data = _read(out)
    assert data["settings"]["symbols_source"] == "all" and data["requested_symbols"] == 10
    assert data["scanned_symbols"] == 9 and data["series_scanned"] == 9
    assert len(data["failures"]) == 1
    f = data["failures"][0]
    assert f["symbol"] == "S03USDT" and f["timeframe"] == "4h" and f["stage"] == "fetch"
    assert "Invalid symbol" in f["error"] and len(f["error"]) <= build_site.ERROR_CHARS
    assert all(r["symbol"] != "S03USDT" for r in data["results"])


def test_empty_series_is_skipped_not_failed(tmp_path, monkeypatch):
    def fetch(symbol, timeframe, limit=1000, **kwargs):
        df = make_df(IHS_BREAK, timeframe=timeframe)
        return df.iloc[0:0] if timeframe == "1d" else df
    monkeypatch.setattr(build_site, "fetch_klines", fetch)
    out = tmp_path / "scan.json"
    assert build_site.main(["--symbols", "NEWUSDT", "--timeframes", "4h,1d", "--out", str(out)]) == 0
    _, data = _read(out)
    assert data["failures"] == [] and data["skipped"][0]["timeframe"] == "1d"


def test_failure_ratio_over_limit_keeps_old_files(tmp_path, monkeypatch, capsys):
    out = tmp_path / "scan.json"
    out.write_text('{"old": true}', encoding="utf-8")
    old_bar = tmp_path / "bars" / "keep.json"
    old_bar.parent.mkdir()
    old_bar.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(build_site, "fetch_klines", _fetch_failing({"S00USDT", "S01USDT", "S02USDT"}))
    code = build_site.main(["--symbols", ",".join(_symbols(10)), "--timeframes", "4h", "--out", str(out)])
    assert code != 0  # 30% > 20%
    assert "NOT updated" in capsys.readouterr().err
    assert out.read_text(encoding="utf-8") == '{"old": true}' and old_bar.exists()


def test_failure_ratio_at_limit_still_writes(tmp_path, monkeypatch):
    monkeypatch.setattr(build_site, "fetch_klines", _fetch_failing({"S00USDT", "S01USDT"}))
    out = tmp_path / "scan.json"
    assert build_site.main(["--symbols", ",".join(_symbols(10)), "--timeframes", "4h", "--out", str(out)]) == 0
    assert len(_read(out)[1]["failures"]) == 2  # exactly 20% is allowed


def test_all_fail_exits_nonzero(tmp_path, monkeypatch, capsys):
    out = tmp_path / "scan.json"
    out.write_text('{"old": true}', encoding="utf-8")

    def boom(symbol, timeframe, limit=1000, **kwargs):
        raise BinanceFetchError("all endpoints failed: timeout")

    monkeypatch.setattr(build_site, "fetch_klines", boom)
    assert build_site.main(["--symbols", "AAAUSDT", "--timeframes", "4h", "--out", str(out)]) != 0
    assert "NOT updated" in capsys.readouterr().err
    assert out.read_text(encoding="utf-8") == '{"old": true}'


def test_symbol_list_failure_keeps_old_file(tmp_path, monkeypatch):
    out = tmp_path / "scan.json"
    out.write_text('{"old": true}', encoding="utf-8")

    def boom(**k):
        raise BinanceFetchError("exchangeInfo down")

    monkeypatch.setattr(build_site, "list_usdt_symbols", boom)
    assert build_site.main(["--sources", "binance-spot", "--out", str(out)]) == 1
    assert out.read_text(encoding="utf-8") == '{"old": true}'


def test_min_quote_volume_filter(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(build_site, "fetch_klines",
                        lambda s, tf, limit=1000, **k: seen.append(s) or make_df(IHS_BREAK, timeframe=tf))
    monkeypatch.setattr(build_site, "list_usdt_symbols", lambda **k: ["AUSDT", "BUSDT_", "CUSDT"])
    monkeypatch.setattr(build_site, "fetch_quote_volumes", lambda **k: {"AUSDT": 5e6, "CUSDT": 1e5})
    out = tmp_path / "scan.json"
    assert build_site.main(["--sources", "binance-spot", "--min-quote-volume", "1000000", "--timeframes", "4h", "--out", str(out)]) == 0
    assert seen == ["AUSDT"]
    assert _read(out)[1]["settings"]["symbols"] == ["AUSDT"]


def test_weekly_limit_capped(tmp_path, monkeypatch):
    limits = {}
    monkeypatch.setattr(build_site, "fetch_klines",
                        lambda s, tf, limit=1000, **k: limits.setdefault(tf, limit) and make_df(IHS_BREAK, timeframe=tf))
    out = tmp_path / "scan.json"
    assert build_site.main(["--symbols", "AUSDT", "--out", str(out)]) == 0
    assert limits == {"4h": 1000, "1d": 1000, "1w": build_site.LIMIT_CAP_BY_TIMEFRAME["1w"]}


def test_include_stale_flag(tmp_path, monkeypatch):
    stale = make_df(IHS_BREAK + [(110, 125)], timeframe="4h")  # breakout age 18 > 6
    monkeypatch.setattr(build_site, "fetch_klines", lambda s, tf, limit=1000, **k: stale.copy())
    out = tmp_path / "scan.json"
    for flag, expect in (([], 0), (["--include-stale"], 1)):
        assert build_site.main(["--symbols", "SSSUSDT", "--timeframes", "4h", "--out", str(out)] + flag) == 0
        data = json.loads(out.read_text(encoding="utf-8"))
        assert len(data["results"]) == expect
        if expect:
            r = data["results"][0]
            assert r["status"] == "stale" and data["settings"]["include_stale"] is True
            chart = json.loads((out.parent / r["chart_file"]).read_text(encoding="utf-8"))
            assert not math.isnan(chart["neckline"][1]["price"])


# ---------------- data_binance: symbol list ----------------
def _sym(symbol, base, quote="USDT", status="TRADING", spot=True):
    return {"symbol": symbol, "baseAsset": base, "quoteAsset": quote, "status": status,
            "isSpotTradingAllowed": spot}


FAKE_INFO = {"symbols": [
    _sym("BTCUSDT", "BTC"), _sym("ETHUSDT", "ETH"), _sym("JUPUSDT", "JUP"), _sym("SYRUPUSDT", "SYRUP"),
    _sym("BTCUPUSDT", "BTCUP"), _sym("ETHDOWNUSDT", "ETHDOWN"), _sym("ETHBULLUSDT", "ETHBULL"),
    _sym("USDCUSDT", "USDC"), _sym("FDUSDUSDT", "FDUSD"), _sym("EURUSDT", "EUR"), _sym("TRYUSDT", "TRY"),
    _sym("USD1USDT", "USD1"), _sym("PAXGUSDT", "PAXG"),
    _sym("LUNAUSDT", "LUNA", status="BREAK"), _sym("ETHBTC", "ETH", quote="BTC"),
    _sym("XYZUSDT", "XYZ", spot=False), _sym("币安人生USDT", "币安人生"),
]}


class _Resp:
    def __init__(self, data, status=200, headers=None):
        self._data, self.status_code, self.headers = data, status, headers or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._data


class _Session:
    def __init__(self, responses):
        self.responses, self.calls = list(responses), []

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, params))
        return self.responses.pop(0)


def test_list_usdt_symbols_filters(capsys):
    s = _Session([_Resp(FAKE_INFO, headers={"x-mbx-used-weight-1m": "20"})])
    lim = db.WeightLimiter(clock=lambda: 30.0, sleep=lambda x: None)
    got = db.list_usdt_symbols(session=s, limiter=lim)
    assert got == sorted(["BTCUSDT", "ETHUSDT", "JUPUSDT", "SYRUPUSDT", "PAXGUSDT", "币安人生USDT"])
    assert "6 Binance spot USDT pairs" in capsys.readouterr().out
    assert s.calls[0][0] == db.PRIMARY_BASE + db.EXCHANGE_INFO_PATH
    assert lim.max_seen == 20


def test_excluded_list_is_adjustable():
    got = db.filter_usdt_symbols(FAKE_INFO, excluded_bases={"BTC"})
    assert "BTCUSDT" not in got and "USDCUSDT" in got


# ---------------- data_binance: weight throttle ----------------
class _Clock:
    def __init__(self, t):
        self.t, self.sleeps = t, []

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.sleeps.append(s)
        self.t += s


def test_kline_weight():
    assert db.kline_weight(1) == db.kline_weight(1000) == 2
    with pytest.raises(ValueError):
        db.kline_weight(0)


def test_limiter_waits_for_next_minute_when_header_high():
    clk = _Clock(600.0 + 42.0)  # 42 s into a minute
    lim = db.WeightLimiter(threshold=4500, margin=1.0, clock=clk, sleep=clk.sleep)
    lim.acquire(2)
    assert clk.sleeps == []
    lim.observe({"X-MBX-USED-WEIGHT-1M": "4499"})
    assert lim.used == 4499 and lim.max_seen == 4499
    lim.acquire(2)  # 4499 + 2 > 4500 -> wait 18 s + 1 s margin
    assert clk.sleeps == [pytest.approx(19.0)] and lim.throttle_waits == 1
    assert lim.used == 2  # new minute
    lim.observe({"x-mbx-used-weight-1m": "8"})  # case-insensitive
    assert lim.used == 8 and lim.max_seen == 4499


def test_limiter_counts_own_requests_without_headers():
    clk = _Clock(0.0)
    lim = db.WeightLimiter(threshold=10, clock=clk, sleep=clk.sleep)
    for _ in range(5):
        lim.acquire(2)
    assert clk.sleeps == []
    lim.acquire(2)
    assert len(clk.sleeps) == 1


def test_retry_after_on_429_then_success():
    clk = _Clock(0.0)
    lim = db.WeightLimiter(clock=clk, sleep=clk.sleep)
    s = _Session([_Resp({"code": -1003}, status=429, headers={"Retry-After": "7"}),
                  _Resp([], headers={"X-MBX-USED-WEIGHT-1M": "12"})])
    assert db._get_json(s, db.KLINES_PATH, {}, (db.PRIMARY_BASE,), 5, limiter=lim, weight=2) == []
    assert clk.sleeps == [pytest.approx(7.0)] and len(s.calls) == 2


def test_long_ban_is_an_error_not_a_wait():
    clk = _Clock(0.0)
    lim = db.WeightLimiter(clock=clk, sleep=clk.sleep)
    s = _Session([_Resp({}, status=418, headers={"Retry-After": "3600"})])
    with pytest.raises(BinanceFetchError, match="418"):
        db._get_json(s, db.KLINES_PATH, {}, (db.PRIMARY_BASE,), 5, limiter=lim)
    assert clk.sleeps == []
