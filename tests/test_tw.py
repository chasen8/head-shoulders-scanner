"""Taiwan stocks (charter D29): stock list filter, Yahoo parsing / time zones, 60m -> 4h session split,
1d -> 1w, closed bars only, crumb + 429 handling, the TW build (schema, separate output, failure ratio),
Discord TW embeds, and the publish merge that keeps the other market's data. No network."""
from __future__ import annotations

import json
from argparse import Namespace
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pytest

import build_site
import build_site_tw
import notify_discord as nd
from ihs_scanner import data_tw as tw
from ihs_scanner.keys import pattern_key
from ihs_scanner.sources import Instrument, SourceFetchError
from scripts import merge_published as mp

from .synth import IHS_BREAK, IHS_WATCH, make_df


def utc(text):
    return pd.Timestamp(text, tz="UTC")


def taipei(text):
    return pd.Timestamp(text, tz=tw.TZ)


def unix(ts):
    return int(pd.Timestamp(ts).tz_convert("UTC").timestamp())


# ------------------------------------------------------------------ stock list

TWSE_ROWS = [
    {"Code": "2330", "Name": "台積電"}, {"Code": "1101", "Name": " 台泥 "}, {"Code": "9958", "Name": "世紀鋼"},
    {"Code": "0050", "Name": "元大台灣50"}, {"Code": "00632R", "Name": "元大台灣50反1"},
    {"Code": "00679B", "Name": "元大美債20年"}, {"Code": "020011", "Name": "統一微波高息20N"},  # ETN
    {"Code": "030001", "Name": "某某權證購01"}, {"Code": "2881A", "Name": "富邦特"},  # warrant, preferred
    {"Code": "9105", "Name": "泰金寶-DR"}, {"Code": "910322", "Name": "康師傅-DR"},  # depositary receipts
    {"Code": "01001T", "Name": "土銀富邦R1"}, {"Code": "", "Name": "空"},  # REIT / beneficiary cert, blank
]
TPEX_ROWS = [{"SecuritiesCompanyCode": "6488", "CompanyName": "環球晶"},
             {"SecuritiesCompanyCode": "006201", "CompanyName": "元大富櫃50"},
             {"SecuritiesCompanyCode": "00687B", "CompanyName": "國泰20年美債"}]


def test_listing_keeps_common_stocks_only():
    got = tw.filter_listing(TWSE_ROWS, "twse")
    assert [(i.symbol, i.name) for i in got] == [("1101", "台泥"), ("2330", "台積電"), ("9958", "世紀鋼")]
    i = got[1]
    assert (i.exchange, i.market, i.native, i.tv_symbol) == ("twse", "stock", "2330.TW", "TWSE:2330")
    otc = tw.filter_listing(TPEX_ROWS, "tpex")
    assert [(i.symbol, i.native, i.tv_symbol) for i in otc] == [("6488", "6488.TWO", "TPEX:6488")]


def test_listing_include_etf_option():
    got = {i.symbol for i in tw.filter_listing(TWSE_ROWS, "twse", include_etf=True)}
    assert {"0050", "00632R", "00679B"} <= got
    assert not got & {"020011", "030001", "2881A", "9105", "910322", "01001T"}
    assert {i.symbol for i in tw.filter_listing(TPEX_ROWS, "tpex", include_etf=True)} == {"6488", "006201", "00687B"}


def test_build_site_option_parsing():
    args = build_site.parse_args(["--market", "tw", "--tw-include-etf"])
    assert args.market == "tw" and args.tw_include_etf
    assert Path(args.out) == build_site.DEFAULT_OUT_TW
    assert Path(build_site.parse_args([]).out) == build_site.DEFAULT_OUT


# ------------------------------------------------------------------ Yahoo parsing / time zones

def chart_payload(rows, error=None):
    """rows: [(utc_text, o, h, l, c, v)] -> Yahoo chart JSON."""
    if error:
        return {"chart": {"result": None, "error": {"code": "Not Found", "description": error}}}
    return {"chart": {"error": None, "result": [{
        "meta": {"currency": "TWD", "exchangeTimezoneName": tw.TZ},
        "timestamp": [unix(utc(t)) for t, *_ in rows],
        "indicators": {"quote": [{"open": [r[1] for r in rows], "high": [r[2] for r in rows],
                                  "low": [r[3] for r in rows], "close": [r[4] for r in rows],
                                  "volume": [r[5] for r in rows]}]}}]}}


def test_parse_chart_drops_missing_prices_and_is_utc():
    raw = tw.parse_chart(tw.chart_result(chart_payload([
        ("2026-10-15 01:00", 10, 11, 9, 10.5, 100), ("2026-10-16 01:00", None, None, None, None, None),
        ("2026-10-19 01:00", 10.5, 12, 10, 11.5, None)])))
    assert len(raw) == 2
    assert str(raw["timestamp"].dt.tz) == "UTC"
    assert raw["volume"].tolist() == [100.0, 0.0]
    with pytest.raises(SourceFetchError, match="No data"):
        tw.chart_result(chart_payload([], error="No data found, symbol may be delisted"))


def daily_raw():
    # Yahoo stamps daily bars at the session open, 09:00 Taipei = 01:00 UTC. 10-16 is a Friday.
    return tw.parse_chart(tw.chart_result(chart_payload([
        ("2026-10-12 01:00", 100, 105, 99, 104, 1000), ("2026-10-13 01:00", 104, 106, 101, 102, 1100),
        ("2026-10-14 01:00", 102, 103, 98, 99, 900), ("2026-10-15 01:00", 99, 101, 97, 100, 800),
        ("2026-10-16 01:00", 100, 108, 100, 107, 1500), ("2026-10-19 01:00", 107, 110, 106, 109, 1200)])))


def test_daily_bars_times_and_closed_only():
    before = tw.daily_bars(daily_raw(), now=taipei("2026-10-19 13:50"))  # 13:30 close + 30 min buffer not over
    after = tw.daily_bars(daily_raw(), now=taipei("2026-10-19 14:00"))
    assert len(before) == 5 and len(after) == 6
    last = after.iloc[-1]
    assert last["timestamp"] == utc("2026-10-19 01:00") and last["close_time"] == utc("2026-10-19 05:30")
    assert list(after.columns) == ["timestamp", "open", "high", "low", "close", "volume", "close_time"]


def test_weekly_from_daily_monday_anchor_and_closed_only():
    daily = tw.daily_bars(daily_raw(), now=taipei("2026-10-19 15:00"))
    weekly = tw.weekly_bars(daily, now=taipei("2026-10-19 15:00"))
    assert len(weekly) == 1  # the week of 10-19 is still open
    w = weekly.iloc[0]
    assert w["timestamp"] == utc("2026-10-12 01:00")  # Monday 09:00 Taipei
    assert w["close_time"] == utc("2026-10-16 05:30")  # Friday 13:30 Taipei
    assert (w["open"], w["high"], w["low"], w["close"], w["volume"]) == (100, 108, 97, 107, 5300)
    # before Friday's close (+buffer) that week is not closed either
    assert tw.weekly_bars(daily, now=taipei("2026-10-16 13:45")).empty


def test_weekly_closes_after_friday_even_when_friday_is_a_holiday():
    raw = daily_raw()
    raw = raw[raw["timestamp"] < utc("2026-10-16")]  # Mon-Thu only
    daily = tw.daily_bars(raw, now=taipei("2026-10-16 10:00"))
    assert tw.weekly_bars(daily, now=taipei("2026-10-16 10:00")).empty
    w = tw.weekly_bars(daily, now=taipei("2026-10-16 14:01")).iloc[0]
    assert w["close"] == 100 and w["volume"] == 3800


def hourly_raw(first_hour_volume=0):
    # Taipei 09..13 hourly bars for 10-15 plus 10-16, and the 13:30 last-price tick Yahoo adds on the live day
    rows = []
    for day, base in (("2026-10-15", 99), ("2026-10-16", 100)):
        for h, (o, hi, lo, c, v) in zip(range(9, 14), [(base, base + 1, base - 1, base + 0.5, first_hour_volume),
                                                         (base + .5, base + 2, base, base + 1, 100),
                                                         (base + 1, base + 3, base + .5, base + 2, 100),
                                                         (base + 2, base + 2.5, base + 1, base + 1.5, 100),
                                                         (base + 1.5, base + 2, base + 1, base + 1.2, 100)]):
            rows.append((taipei(f"{day} {h:02d}:00").tz_convert("UTC"), o, hi, lo, c, v))
    rows.append((taipei("2026-10-16 13:30").tz_convert("UTC"), 107, 107, 107, 107, 0))
    return tw.parse_chart(tw.chart_result(chart_payload([(str(t), *r) for t, *r in rows])))


def test_four_hour_session_split():
    bars = tw.four_hour_bars(hourly_raw(), None, now=taipei("2026-10-17 12:00"))
    assert bars["timestamp"].tolist() == [utc("2026-10-15 01:00"), utc("2026-10-15 05:00"),
                                          utc("2026-10-16 01:00"), utc("2026-10-16 05:00")]
    assert bars["close_time"].tolist() == [utc("2026-10-15 05:00"), utc("2026-10-15 05:30"),
                                           utc("2026-10-16 05:00"), utc("2026-10-16 05:30")]
    am = bars.iloc[0]
    assert (am["open"], am["high"], am["low"], am["close"], am["volume"]) == (99, 102, 98, 100.5, 300)
    pm = bars.iloc[3]  # 13:00 bar + the 13:30 tick
    assert (pm["open"], pm["high"], pm["low"], pm["close"]) == (101.5, 107, 101, 107)


def test_four_hour_repairs_close_and_volume_from_daily():
    bars = tw.four_hour_bars(hourly_raw(first_hour_volume=0), daily_raw(), now=taipei("2026-10-17 12:00"))
    d15 = bars.iloc[0:2]
    # 10-15 daily: close 100, volume 800; hourly 13:00 close 100.2 -> 100; missing 400 goes to the 09:00 bar
    assert d15.iloc[1]["close"] == 100 and d15.iloc[1]["low"] == 100
    assert d15["volume"].tolist() == [300 + 400, 100]
    assert d15["volume"].sum() == 800
    with_first_hour = tw.four_hour_bars(hourly_raw(first_hour_volume=50), daily_raw(), now=taipei("2026-10-17"))
    # the first hour has volume -> the residual (closing auction) goes to the 13:00 bar
    assert with_first_hour.iloc[0:2]["volume"].tolist() == [350, 100 + 350]


def test_four_hour_closed_only():
    now = taipei("2026-10-16 13:35")  # morning bar closed at 13:00 (+30 min), afternoon bar not yet
    bars = tw.four_hour_bars(hourly_raw(), None, now=now)
    assert bars["timestamp"].iloc[-1] == utc("2026-10-16 01:00")
    assert len(tw.four_hour_bars(hourly_raw(), None, now=taipei("2026-10-16 12:59"))) == 2


# ------------------------------------------------------------------ Yahoo client

class Resp:
    def __init__(self, status=200, data=None, text=""):
        self.status_code, self._d, self.text = status, data, text

    def json(self):
        return self._d


class FakeHTTP:
    """requests.Session look-alike shared by the factory; routes by URL."""

    def __init__(self, chart_responses):
        self.calls, self.chart_responses = [], list(chart_responses)
        self.headers, self.cookies = {}, FakeJar()
        self.crumbs = 0

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, dict(params or {})))
        if url == tw.YAHOO_HOME:
            self.cookies.set("A3", "cookie")
            return Resp(200, text="<html>")
        if url == tw.YAHOO_CRUMB_URL:
            self.crumbs += 1
            return Resp(200, text=f"crumb{self.crumbs}")
        return self.chart_responses.pop(0)


class FakeJar(dict):
    def set(self, k, v):
        self[k] = v


class NoLimit:
    requests = 0

    def acquire(self):
        self.requests += 1


def client_with(http):
    return tw.YahooClient(limiter=NoLimit(), session_factory=lambda: http, sleep=lambda s: None)


def test_crumb_then_chart_with_crumb():
    http = FakeHTTP([Resp(200, chart_payload([("2026-10-15 01:00", 1, 2, 0.5, 1.5, 10)]))])
    c = client_with(http)
    assert c.refresh_crumb(min_age=0)
    res = c.chart("2330.TW", "1d", "10y")
    assert res["timestamp"]
    url, params = http.calls[-1]
    assert url.endswith("/2330.TW") and params["crumb"] == "crumb1" and params["interval"] == "1d"
    assert c.cookies == {"A3": "cookie"}


def test_429_refreshes_crumb_and_retries():
    http = FakeHTTP([Resp(429, text="Too Many Requests"),
                     Resp(200, chart_payload([("2026-10-15 01:00", 1, 2, 0.5, 1.5, 10)]))])
    c = client_with(http)
    c.refresh_crumb(min_age=0)
    c._crumb_at = -1e9  # allow the refresh triggered by the 429
    c.chart("6488.TWO", "60m", "730d")
    assert c.rate_limited == 1 and http.crumbs == 2
    assert http.calls[-1][1]["crumb"] == "crumb2"


def test_404_is_an_error_without_retry_and_retries_are_bounded():
    http = FakeHTTP([Resp(404, text="Not Found")])
    with pytest.raises(SourceFetchError, match="HTTP 404"):
        client_with(http).chart("9999.TW", "1d", "10y")
    http = FakeHTTP([Resp(503)] * 10)
    c = client_with(http)
    with pytest.raises(SourceFetchError, match="after 5 attempts"):
        c.chart("2330.TW", "1d", "10y")


def test_fetch_series_two_requests_per_stock():
    class C:
        def __init__(self):
            self.calls = []

        def chart(self, sym, interval, range_):
            self.calls.append((sym, interval, range_))
            if interval == "1d":
                return tw.chart_result(chart_payload([(str(t.tz_convert("UTC")), 100, 101, 99, 100, 10) for t in
                                                      pd.date_range("2026-01-05 09:00", periods=60, freq="B", tz=tw.TZ)]))
            raise SourceFetchError("60m boom")

    c = C()
    inst = tw.filter_listing([{"Code": "2330", "Name": "台積電"}], "twse")[0]
    frames, errors = tw.fetch_series(inst, ["4h", "1d", "1w"], 1000, c, now=taipei("2026-10-19 15:00"))
    assert [x[1] for x in c.calls] == ["1d", "60m"]
    assert set(frames) == {"1d", "1w"} and "4h" in errors and "boom" in errors["4h"]
    assert len(frames["1d"]) == 60 and len(frames["1w"]) == 12


def test_hourly_422_falls_back_to_shorter_range():
    class C:
        def __init__(self):
            self.ranges = []

        def chart(self, sym, interval, range_):
            self.ranges.append(range_)
            if range_ == "730d":
                raise SourceFetchError(f"{sym} 60m: HTTP 422 'requested range must be within the last 730 days'")
            return tw.chart_result(chart_payload([("2026-10-15 01:00", 1, 2, 0.5, 1.5, 10)]))

    c = C()
    inst = tw.filter_listing([{"Code": "6890", "Name": "來億-KY"}], "twse")[0]
    frames, errors = tw.fetch_series(inst, ["4h"], 1000, c, now=taipei("2026-10-19 15:00"))
    assert c.ranges == ["730d", "2y"] and not errors and len(frames["4h"]) == 1


# ------------------------------------------------------------------ build (TW market)

TW_INSTS = {
    "twse": [Instrument(base="2330", symbol="2330", native="2330.TW", exchange="twse", market="stock", name="台積電"),
             Instrument(base="1101", symbol="1101", native="1101.TW", exchange="twse", market="stock", name="台泥")],
    "tpex": [Instrument(base="6488", symbol="6488", native="6488.TWO", exchange="tpex", market="stock", name="環球晶")],
}


def fake_lister(session, exchange, include_etf=False):
    return TW_INSTS[exchange]


def fake_fetch_factory(fail=()):
    def fetch(inst, timeframes, limit, client):
        frames, errors = {}, {}
        for tf in timeframes:
            if inst.symbol in fail:
                errors[tf] = "SourceFetchError: boom"
            else:
                frames[tf] = make_df(IHS_WATCH if inst.symbol != "6488" else IHS_BREAK, timeframe=tf)
        return frames, errors
    return fetch


class StubClient:
    requests = 0
    rate_limited = 0


def tw_args(out, **kw):
    a = dict(timeframes="4h,1d", limit=1000, tw_include_etf=False, symbols="all", workers=2, include_stale=True,
             scheduled=True, out=str(out), tw_rps=None)
    a.update(kw)
    return Namespace(**a)


def test_tw_build_schema_and_separate_output(tmp_path, monkeypatch):
    monkeypatch.delenv("GITHUB_EVENT_NAME", raising=False)
    crypto = tmp_path / "data" / "scan.json"
    crypto.parent.mkdir(parents=True)
    crypto.write_text('{"market": "crypto"}', encoding="utf-8")
    out = tmp_path / "data" / "tw" / "scan.json"
    code = build_site_tw.main_tw(tw_args(out), client=StubClient(), lister=fake_lister, fetch=fake_fetch_factory())
    assert code == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    assert crypto.read_text(encoding="utf-8") == '{"market": "crypto"}'  # untouched
    assert data["market"] == "tw" and data["schema_version"] == build_site.SCHEMA_VERSION
    assert data["settings"]["universe"] == "common" and data["settings"]["currency"] == "TWD"
    assert data["settings"]["sources"] == ["twse-stock", "tpex-stock"]
    assert data["source_counts"]["twse-stock"]["symbols"] == 2 and data["source_counts"]["tpex-stock"]["symbols"] == 1
    assert data["next_scan_at"] is not None and data["results"]
    names = {"2330": "台積電", "1101": "台泥", "6488": "環球晶"}
    for r in data["results"]:
        assert r["market"] == "stock" and r["exchange"] in ("twse", "tpex")
        assert r["name"] == names[r["symbol"]]
        assert r["tv_symbol"] == ("TWSE:" if r["exchange"] == "twse" else "TPEX:") + r["symbol"]
        assert r["pattern_key"] == pattern_key(r) and r["pattern_key"].startswith(f"{r['exchange']}|stock|{r['symbol']}|")
        assert r["pattern_id"].startswith(f"{r['exchange']}|stock|")
        chart = json.loads((out.parent / r["chart_file"]).read_text(encoding="utf-8"))
        assert chart["name"] == r["name"] and r["chart_file"].startswith("bars/")
    keys = [r["pattern_key"] for r in data["results"]]
    assert len(keys) == len(set(keys))
    assert (out.parent / "bars").is_dir() and not (crypto.parent / "bars").exists()


def test_tw_symbols_option_and_failure_ratio(tmp_path, monkeypatch):
    monkeypatch.delenv("GITHUB_EVENT_NAME", raising=False)
    out = tmp_path / "tw" / "scan.json"
    assert build_site_tw.main_tw(tw_args(out, symbols="6488"), client=StubClient(), lister=fake_lister,
                                 fetch=fake_fetch_factory()) == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["requested_symbols"] == 1 and {r["symbol"] for r in data["results"]} == {"6488"}
    before = out.read_text(encoding="utf-8")
    # 2 of 3 stocks fail -> 67% > 20%: exit 1, previous output kept
    assert build_site_tw.main_tw(tw_args(out), client=StubClient(), lister=fake_lister,
                                 fetch=fake_fetch_factory(fail=("2330", "1101"))) == 1
    assert out.read_text(encoding="utf-8") == before


def test_tw_one_exchange_list_failure_is_recorded(tmp_path, monkeypatch):
    def lister(session, exchange, include_etf=False):
        if exchange == "tpex":
            raise SourceFetchError("tpex down")
        return TW_INSTS[exchange]
    out = tmp_path / "tw" / "scan.json"
    assert build_site_tw.main_tw(tw_args(out), client=StubClient(), lister=lister, fetch=fake_fetch_factory()) == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["source_errors"][0]["source"] == "tpex-stock"


def test_next_tw_scan_time_weekdays_0610_utc():
    f = build_site_tw.next_tw_scan_time
    assert f(datetime(2026, 10, 19, 5, 0, tzinfo=timezone.utc)) == datetime(2026, 10, 19, 6, 10, tzinfo=timezone.utc)
    assert f(datetime(2026, 10, 19, 6, 10, tzinfo=timezone.utc)) == datetime(2026, 10, 20, 6, 10, tzinfo=timezone.utc)
    assert f(datetime(2026, 10, 23, 7, 0, tzinfo=timezone.utc)) == datetime(2026, 10, 26, 6, 10, tzinfo=timezone.utc)


def test_crypto_payload_is_marked_crypto():
    payload, _ = build_site.build_payload({}, build_site.merge_config({}), symbols=[], timeframes=["1d"], limit=10)
    assert payload["market"] == "crypto"


# ------------------------------------------------------------------ Discord (TW)

def tw_row(status="forming", **extra):
    r = {"symbol": "2330", "name": "台積電", "exchange": "twse", "market": "stock", "tv_symbol": "TWSE:2330",
         "timeframe": "1d", "status": status, "pattern_score": 66.0, "current_close": 1005.0,
         "current_neckline_price": 1100.5, "distance_to_neckline": 0.0863, "neckline_side": "below",
         "tp1": 1234.567, "tp2": 1300.0, "invalidation_price": 900.0, "breakout_detected": False,
         "drop_left_pct": 0.12, "drop_right_pct": 0.11, "head_rise_pct": 0.25,
         "LS_time": "2026-05-01T01:00:00Z", "H_time": "2026-07-01T01:00:00Z", "RS_time": "2026-10-01T01:00:00Z",
         "RS_confirm_time": None}
    r.update(extra)
    return r


def test_tw_embed_title_fields_and_twd_format():
    e = nd.build_embed({"kind": "new", "row": tw_row()})
    f = {x["name"]: x["value"] for x in e["fields"]}
    assert e["title"] == "台股 2330 台積電 日線 右肩形成中"
    assert f["市場"] == "台股上市"
    assert f["目前價"] == "NT$1,005.00" and f["TP1"] == "NT$1,234.57" and f["失效價（頭部低點）"] == "NT$900.00"
    assert f["頸線價"] == "NT$1,100.50（低於頸線 8.6%）"
    assert "TWSE%3A2330" in e["url"]
    otc = nd.build_embed({"kind": "new", "row": tw_row("watchlist", symbol="6488", name="環球晶", exchange="tpex",
                                                      tv_symbol="TPEX:6488", timeframe="4h")})
    assert otc["title"] == "台股 6488 環球晶 4小時線 右肩已確認"
    assert {x["name"]: x["value"] for x in otc["fields"]}["市場"] == "台股上櫃"


def test_tw_first_run_summary(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(nd.requests, "post", lambda url, json=None, timeout=None: calls.append(json) or Resp(204))
    scan = tmp_path / "scan.json"
    scan.write_text(json.dumps({"market": "tw", "results": [tw_row()]}), encoding="utf-8")
    st = tmp_path / "state_tw.json"
    code = nd.run(scan, st, min_score=50, statuses={"forming", "watchlist", "confirmed"}, notify_all=False,
                  dry_run=False, webhook="https://discord.invalid/x", sleep=lambda s: None, pause=0)
    assert code == 0 and len(calls) == 1
    emb = calls[0]["embeds"][0]
    assert emb["title"] == "台股頭肩底掃描" and emb["description"].startswith("開始追蹤台股")
    assert json.loads(st.read_text(encoding="utf-8"))["version"] == nd.STATE_VERSION
    assert nd.scan_market({"results": [tw_row()]}) == "tw" and nd.scan_market({"results": []}) == "crypto"


# ------------------------------------------------------------------ publish merge

def write(root: Path, rel: str, text: str = "x"):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def tree(root: Path) -> dict[str, str]:
    return {p.relative_to(root).as_posix(): p.read_text(encoding="utf-8") for p in root.rglob("*") if p.is_file()}


def published_site(root):
    write(root, "index.html", "old page")
    write(root, "data/scan.json", "crypto old")
    write(root, "data/bars/a.json", "crypto bar")
    write(root, "data/tw/scan.json", "tw old")
    write(root, "data/tw/bars/t.json", "tw bar")
    write(root, ".git/HEAD", "ref")


def test_merge_tw_publish_keeps_crypto(tmp_path):
    prev, new, out = tmp_path / "prev", tmp_path / "new", tmp_path / "out"
    published_site(prev)
    write(new, "index.html", "new page")
    write(new, "js/app.js", "js")
    write(new, "data/tw/scan.json", "tw new")
    write(new, "data/tw/bars/n.json", "tw new bar")
    rep = mp.merge_tree(new, prev, out, "tw", mp.OWNED["site"])
    assert tree(out) == {"index.html": "new page", "js/app.js": "js", "data/tw/scan.json": "tw new",
                         "data/tw/bars/n.json": "tw new bar", "data/scan.json": "crypto old",
                         "data/bars/a.json": "crypto bar"}
    assert sorted(rep["kept"]) == ["data/bars", "data/scan.json"]


def test_merge_crypto_publish_keeps_tw_and_drops_stale_copies(tmp_path):
    prev, new, out = tmp_path / "prev", tmp_path / "new", tmp_path / "out"
    published_site(prev)
    write(new, "index.html", "new page")
    write(new, "data/scan.json", "crypto new")
    write(new, "data/bars/b.json", "crypto new bar")
    write(new, "data/tw/scan.json", "STALE tw copy in the checkout")
    rep = mp.merge_tree(new, prev, out, "crypto", mp.OWNED["site"])
    t = tree(out)
    assert t["data/scan.json"] == "crypto new" and "data/bars/a.json" not in t and t["data/bars/b.json"] == "crypto new bar"
    assert t["data/tw/scan.json"] == "tw old" and t["data/tw/bars/t.json"] == "tw bar"
    assert rep["dropped"] == ["data/tw"] and ".git/HEAD" not in t


def test_merge_state_branch_both_directions(tmp_path):
    prev, new, out = tmp_path / "prev", tmp_path / "new", tmp_path / "out"
    write(prev, "state.json", "crypto state")
    write(prev, "site/data/scan.json", "crypto scan")
    write(prev, "site/data/bars/a.json", "bar")
    write(new, "state_tw.json", "tw state")
    write(new, "site/data/tw/scan.json", "tw scan")
    mp.merge_tree(new, prev, out, "tw", mp.OWNED["state"])
    assert tree(out) == {"state.json": "crypto state", "site/data/scan.json": "crypto scan",
                         "site/data/bars/a.json": "bar", "state_tw.json": "tw state",
                         "site/data/tw/scan.json": "tw scan"}
    out2, new2 = tmp_path / "out2", tmp_path / "new2"
    write(new2, "state.json", "crypto state 2")
    write(new2, "site/data/scan.json", "crypto scan 2")
    mp.merge_tree(new2, out, out2, "crypto", mp.OWNED["state"])
    t = tree(out2)
    assert t["state.json"] == "crypto state 2" and t["state_tw.json"] == "tw state"
    assert t["site/data/tw/scan.json"] == "tw scan" and "site/data/bars/a.json" not in t


def test_merge_cli_refuses_missing_previous_tree(tmp_path, capsys):
    new = tmp_path / "new"
    write(new, "data/tw/scan.json", "tw")
    args = ["--kind", "site", "--market", "tw", "--new", str(new), "--out", str(tmp_path / "out")]
    assert mp.main(args + ["--prev", str(tmp_path / "missing")]) == 1
    assert "refusing" in capsys.readouterr().err
    assert mp.main(args + ["--prev", str(tmp_path / "missing"), "--allow-missing-prev"]) == 0
    assert tree(tmp_path / "out") == {"data/tw/scan.json": "tw"}


def test_tw_market_config_caps_4h_patterns():  # D33
    from ihs_scanner.config import DEFAULT_CONFIG, market_config
    tw = market_config("tw", {"include_stale": True})
    assert tw["max_pattern_bars_by_timeframe"] == {"4h": 300, "1d": 600, "1w": 200}
    assert tw["include_stale"] is True
    assert market_config("crypto")["max_pattern_bars_by_timeframe"] == DEFAULT_CONFIG["max_pattern_bars_by_timeframe"]
