"""Multi-exchange sources (charter D22): symbol filters, OKX paging/bars, dedup priority,
tv_symbol / pattern_id format, partial failure. No network: fake sessions and monkeypatch."""
from __future__ import annotations

import json

import pytest

import build_site
from ihs_scanner import data_binance_futures as bfut
from ihs_scanner import data_gate, data_okx
from ihs_scanner.sources import (IntervalLimiter, Instrument, SourceFetchError, dedupe_key, get_json,
                                 merge_instruments, ohlcv_frame)

from .synth import IHS_BREAK, make_df

H4 = 4 * 3600 * 1000
NOW = 1_800_000_000_000  # ms; a multiple of 4h? 1.8e12 / 14.4e6 = 125000 exactly


def _ms(series):
    return series.dt.as_unit("ms").astype("int64")


class Resp:
    def __init__(self, data, status=200):
        self._d, self.status_code, self.text = data, status, json.dumps(data)

    def json(self):
        return self._d


class FakeSession:
    def __init__(self, handler):
        self.handler, self.calls = handler, []

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, dict(params or {})))
        return self.handler(url, dict(params or {}))


# ---------------------------------------------------------------- symbol filters
def test_okx_spot_filter():
    rows = [
        {"instId": "HMSTR-USDT", "baseCcy": "HMSTR", "quoteCcy": "USDT", "state": "live"},
        {"instId": "BTC-USDC", "baseCcy": "BTC", "quoteCcy": "USDC", "state": "live"},
        {"instId": "OLD-USDT", "baseCcy": "OLD", "quoteCcy": "USDT", "state": "suspend"},
        {"instId": "USDC-USDT", "baseCcy": "USDC", "quoteCcy": "USDT", "state": "live"},
        {"instId": "ETH-USDT", "baseCcy": "ETH", "quoteCcy": "USDT", "state": "live"},
        {"instId": "ETHUP-USDT", "baseCcy": "ETHUP", "quoteCcy": "USDT", "state": "live"},
        {"instId": "JUP-USDT", "baseCcy": "JUP", "quoteCcy": "USDT", "state": "live"},
    ]
    out = data_okx.filter_instruments(rows, "spot")
    assert [i.symbol for i in out] == ["ETHUSDT", "HMSTRUSDT", "JUPUSDT"]
    assert out[1].native == "HMSTR-USDT" and out[1].source == "okx-spot"


def test_okx_perp_filter():
    rows = [
        {"instId": "TRUST-USDT-SWAP", "ctType": "linear", "state": "live"},
        {"instId": "BTC-USD-SWAP", "ctType": "inverse", "state": "live"},
        {"instId": "BTC-USDC-SWAP", "ctType": "linear", "state": "live"},
        {"instId": "DEAD-USDT-SWAP", "ctType": "linear", "state": "preopen"},
    ]
    out = data_okx.filter_instruments(rows, "perp")
    assert [(i.symbol, i.native, i.market) for i in out] == [("TRUSTUSDT", "TRUST-USDT-SWAP", "perp")]


def test_binance_perp_filter():
    info = {"symbols": [
        {"symbol": "AZTECUSDT", "baseAsset": "AZTEC", "quoteAsset": "USDT", "contractType": "PERPETUAL",
         "status": "TRADING"},
        {"symbol": "BTCUSDT_261225", "baseAsset": "BTC", "quoteAsset": "USDT", "contractType": "CURRENT_QUARTER",
         "status": "TRADING"},
        {"symbol": "BTCUSDC", "baseAsset": "BTC", "quoteAsset": "USDC", "contractType": "PERPETUAL",
         "status": "TRADING"},
        {"symbol": "XUSDT", "baseAsset": "X", "quoteAsset": "USDT", "contractType": "PERPETUAL",
         "status": "SETTLING"},
        {"symbol": "USDCUSDT", "baseAsset": "USDC", "quoteAsset": "USDT", "contractType": "PERPETUAL",
         "status": "TRADING"},
        {"symbol": "1000PEPEUSDT", "baseAsset": "1000PEPE", "quoteAsset": "USDT", "contractType": "PERPETUAL",
         "status": "TRADING"},
    ]}
    out = bfut.filter_perpetuals(info)
    assert [i.native for i in out] == ["1000PEPEUSDT", "AZTECUSDT"]
    assert out[1].tv_symbol == "BINANCE:AZTECUSDT.P"
    assert [bfut.kline_weight(n) for n in (50, 100, 499, 500, 1000, 1001)] == [1, 2, 2, 5, 5, 10]


def test_gate_filter():
    rows = [
        {"name": "TRUST_USDT", "status": "trading", "type": "direct", "in_delisting": False},
        {"name": "AZTEC_USDT", "status": "trading", "type": "direct"},
        {"name": "GONE_USDT", "status": "trading", "type": "direct", "in_delisting": True},
        {"name": "HALT_USDT", "status": "delisting", "type": "direct"},
        {"name": "BTC_USD", "status": "trading", "type": "inverse"},
    ]
    out = data_gate.filter_contracts(rows)
    assert [(i.symbol, i.native) for i in out] == [("AZTECUSDT", "AZTEC_USDT"), ("TRUSTUSDT", "TRUST_USDT")]
    assert out[0].tv_symbol == "GATEIO:AZTECUSDT.P"


# ---------------------------------------------------------------- OKX paging and bars
def _okx_handler(total, step=H4, newest_open=NOW - H4, open_last=False):
    """Serves `total` candles newest-first; honours limit and `after` (strictly older than)."""
    series = [newest_open - i * step for i in range(total)]

    def handler(url, params):
        assert url.endswith("/api/v5/market/candles")
        lim, after = int(params["limit"]), params.get("after")
        older = [t for t in series if after is None or t < int(after)]
        page = older[:min(lim, 300)]
        data = [[str(t), "1", "2", "0.5", "1.5", "10", "0", "0", "1"] for t in page]
        if open_last and after is None and data:
            data[0][8] = "0"  # newest candle still forming
        return Resp({"code": "0", "data": data})
    return handler


def test_okx_pagination_and_bar_alignment():
    sess = FakeSession(_okx_handler(2000))
    df = data_okx.fetch_klines("TRUST-USDT-SWAP", "4h", 700, session=sess, now_ms=NOW)
    assert len(df) == 700
    # 701 requested (limit+1) -> 300 + 300 + 101; each page continues from the oldest ts of the previous
    assert [c[1]["limit"] for c in sess.calls] == [300, 300, 101]
    assert sess.calls[0][1]["bar"] == "4H" and "after" not in sess.calls[0][1]
    assert int(sess.calls[1][1]["after"]) == NOW - H4 - 299 * H4
    ts = _ms(df["timestamp"])
    assert ts.is_monotonic_increasing and ts.is_unique and (ts.diff().dropna() == H4).all()
    assert ts.iloc[-1] == NOW - H4 and (_ms(df["close_time"])).iloc[-1] == NOW - 1


def test_okx_bar_names_and_short_history():
    sess = FakeSession(_okx_handler(40, step=7 * 86400000, newest_open=NOW - 7 * 86400000))
    df = data_okx.fetch_klines("BTC-USDT", "1w", 500, session=sess, now_ms=NOW)
    assert len(df) == 40 and len(sess.calls) == 1  # short page -> stop, no extra request
    assert sess.calls[0][1]["bar"] == "1Wutc"
    assert {"4h": "4H", "1d": "1Dutc", "1w": "1Wutc"} == data_okx.BARS


def test_okx_drops_unconfirmed_and_open_bars():
    sess = FakeSession(_okx_handler(10, open_last=True))
    df = data_okx.fetch_klines("BTC-USDT", "4h", 50, session=sess, now_ms=NOW)
    assert len(df) == 9 and int(df["timestamp"].dt.as_unit("ms").astype("int64").iloc[-1]) == NOW - 2 * H4
    # confirm=1 but close_time not yet passed is dropped by the time check as well
    df2 = data_okx.fetch_klines("BTC-USDT", "4h", 50, session=FakeSession(_okx_handler(10)), now_ms=NOW - 1)
    assert int(df2["timestamp"].dt.as_unit("ms").astype("int64").iloc[-1]) == NOW - 2 * H4


def test_okx_error_code_raises():
    sess = FakeSession(lambda u, p: Resp({"code": "51001", "msg": "Instrument ID does not exist", "data": []}))
    with pytest.raises(SourceFetchError, match="51001"):
        data_okx.fetch_klines("NOPE-USDT", "4h", 10, session=sess, now_ms=NOW)


def test_gate_fetch_single_request_and_closed_only():
    rows = [{"t": (NOW - (5 - i) * H4) // 1000, "o": "1", "h": "2", "l": "0.5", "c": "1.5", "v": 7}
            for i in range(5)]  # last one opens at NOW - H4 ... first at NOW - 5*H4
    sess = FakeSession(lambda u, p: Resp(rows))
    df = data_gate.fetch_klines("TRUST_USDT", "4h", 3, session=sess, now_ms=NOW - 1)
    assert sess.calls[0][1] == {"contract": "TRUST_USDT", "interval": "4h", "limit": 4}
    assert len(df) == 3
    assert int(df["timestamp"].dt.as_unit("ms").astype("int64").iloc[-1]) == NOW - 2 * H4  # bar opened NOW-H4 closes at NOW


def test_ohlcv_frame_close_time():
    df = ohlcv_frame([[NOW - H4, 1, 2, 0.5, 1.5, 3]], "4h", now_ms=NOW)
    assert len(df) == 1 and int(df["close_time"].dt.as_unit("ms").astype("int64").iloc[0]) == NOW - 1


# ---------------------------------------------------------------- throttling / retry
def test_get_json_backs_off_on_429_then_succeeds():
    seq = iter([Resp({}, 429), Resp({}, 503), Resp({"ok": 1})])
    sleeps = []
    out = get_json(FakeSession(lambda u, p: next(seq)), "http://x", None, sleep=sleeps.append)
    assert out == {"ok": 1} and sleeps == [1.0, 2.0]


def test_get_json_gives_up_and_rate_limit_code():
    with pytest.raises(SourceFetchError, match="429"):
        get_json(FakeSession(lambda u, p: Resp({}, 429)), "http://x", None, retries=1, sleep=lambda s: None)
    seq = iter([Resp({"code": "50011"}), Resp({"code": "0"})])
    out = get_json(FakeSession(lambda u, p: next(seq)), "http://x", None, sleep=lambda s: None,
                   rate_limited=lambda d: d["code"] == "50011")
    assert out == {"code": "0"}


def test_interval_limiter_spaces_requests():
    t = [0.0]
    slept = []

    def sleep(s):
        slept.append(s)
        t[0] += s

    lim = IntervalLimiter(0.5, clock=lambda: t[0], sleep=sleep)
    for _ in range(3):
        lim.acquire()
    assert slept == [0.5, 0.5] and lim.requests == 3


# ---------------------------------------------------------------- dedup, tv_symbol
def _i(base, ex, mk):
    return Instrument(base=base, symbol=base + "USDT", native=base, exchange=ex, market=mk)


def test_dedupe_key_contract_multipliers():
    assert dedupe_key("1000PEPE") == dedupe_key("PEPE") == "PEPE"
    assert dedupe_key("1MBABYDOGE") == "BABYDOGE"
    assert dedupe_key("1INCH") == "1INCH" and dedupe_key("1000") == "1000"


def test_merge_priority_order():
    by_source = {
        "gate-perp": [_i("AAA", "gate", "perp"), _i("DDD", "gate", "perp"), _i("EEE", "gate", "perp")],
        "okx-perp": [_i("AAA", "okx", "perp"), _i("CCC", "okx", "perp"), _i("1000PEPE", "okx", "perp")],
        "okx-spot": [_i("AAA", "okx", "spot"), _i("BBB", "okx", "spot"), _i("PEPE", "okx", "spot")],
        "binance-perp": [_i("BBB", "binance", "perp"), _i("FFF", "binance", "perp")],
        "binance-spot": [_i("AAA", "binance", "spot")],
    }
    got = {i.base: i.source for i in merge_instruments(by_source)}
    assert got == {"AAA": "binance-spot", "BBB": "binance-perp", "FFF": "binance-perp", "PEPE": "okx-spot",
                   "CCC": "okx-perp", "DDD": "gate-perp", "EEE": "gate-perp"}
    assert [i.source for i in merge_instruments(by_source)][0] == "binance-spot"


def test_tv_symbols():
    assert _i("HMSTR", "binance", "spot").tv_symbol == "BINANCE:HMSTRUSDT"
    assert _i("TRUST", "binance", "perp").tv_symbol == "BINANCE:TRUSTUSDT.P"
    assert _i("GRAM", "okx", "spot").tv_symbol == "OKX:GRAMUSDT"
    assert _i("TRUST", "okx", "perp").tv_symbol == "OKX:TRUSTUSDT.P"
    assert _i("TRUST", "gate", "perp").tv_symbol == "GATEIO:TRUSTUSDT.P"


def test_parse_sources():
    assert build_site.parse_sources(None) == list(build_site.DEFAULT_SOURCES)
    assert "binance-perp" not in build_site.DEFAULT_SOURCES
    assert build_site.parse_sources("all") == list(build_site.ALL_SOURCES)
    assert build_site.parse_sources("gate-perp,binance-spot") == ["binance-spot", "gate-perp"]
    with pytest.raises(ValueError):
        build_site.parse_sources("kraken-spot")


# ---------------------------------------------------------------- build_site end to end (fake sources)
def _patch_sources(monkeypatch, *, fail_okx_list=False, bad_symbols=()):
    monkeypatch.setattr(build_site, "list_usdt_symbols", lambda **k: ["AAAUSDT", "BBBUSDT"])
    monkeypatch.setattr(build_site.data_okx, "list_instruments", lambda sess, market, **k: (
        (_ for _ in ()).throw(SourceFetchError("okx down")) if fail_okx_list else
        [_i("BBB", "okx", market), _i("OKXC", "okx", market)] if market == "spot" else [_i("PPP", "okx", "perp")]))
    monkeypatch.setattr(build_site.data_gate, "list_instruments", lambda sess, **k: [
        _i("PPP", "gate", "perp"), _i("GATEONLY", "gate", "perp")])

    def fetch(symbol, timeframe, limit=1000, **k):
        if symbol in bad_symbols:
            raise SourceFetchError("HTTP 500")
        return make_df(IHS_BREAK, timeframe=timeframe)

    monkeypatch.setattr(build_site, "fetch_klines", fetch)
    monkeypatch.setattr(build_site.data_okx, "fetch_klines", lambda nid, tf, limit=1000, **k: fetch(nid, tf))
    monkeypatch.setattr(build_site.data_gate, "fetch_klines", lambda nid, tf, limit=1000, **k: fetch(nid, tf))
    # D45 max-position lookups: no network; every perp has a large limit
    monkeypatch.setattr(build_site.data_okx, "max_position_values", lambda sess, ids, **k: {i: 1e9 for i in ids})
    monkeypatch.setattr(build_site.data_gate, "max_position_values", lambda sess, ids, **k: {i: 1e9 for i in ids})


def test_build_all_sources_dedupes_and_tags(tmp_path, monkeypatch):
    _patch_sources(monkeypatch)
    out = tmp_path / "scan.json"
    assert build_site.main(["--timeframes", "4h", "--out", str(out)]) == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    by_sym = {r["symbol"]: r for r in data["results"]}
    # BBB: Binance spot beats OKX spot; PPP: OKX perp beats Gate perp
    assert data["settings"]["symbols"] == ["AAAUSDT", "BBBUSDT", "OKXCUSDT", "PPPUSDT", "GATEONLYUSDT"]
    sc = data["source_counts"]
    assert sc["binance-spot"]["symbols"] == 2 and sc["okx-spot"] == {
        "listed": 2, "symbols": 1, "scanned_symbols": 1, "series_scanned": 1, "failed_series": 0}
    assert sc["okx-perp"]["symbols"] == 1 and sc["gate-perp"]["listed"] == 2 and sc["gate-perp"]["symbols"] == 1
    assert data["settings"]["sources"] == ["binance-spot", "okx-spot", "okx-perp", "gate-perp"]
    for sym, (ex, mk, tv) in {"AAAUSDT": ("binance", "spot", "BINANCE:AAAUSDT"),
                              "OKXCUSDT": ("okx", "spot", "OKX:OKXCUSDT"),
                              "PPPUSDT": ("okx", "perp", "OKX:PPPUSDT.P"),
                              "GATEONLYUSDT": ("gate", "perp", "GATEIO:GATEONLYUSDT.P")}.items():
        r = by_sym[sym]
        assert (r["exchange"], r["market"], r["tv_symbol"]) == (ex, mk, tv)
        assert r["pattern_id"].startswith(f"{ex}|{mk}|{sym}|4h|")
        assert r["chart_file"].startswith(f"bars/{ex}_{mk}_{sym}_4h_")
        chart = json.loads((out.parent / r["chart_file"]).read_text(encoding="utf-8"))
        assert chart["pattern_id"] == r["pattern_id"] and chart["exchange"] == ex and chart["market"] == mk
    assert len({r["pattern_id"] for r in data["results"]}) == len(data["results"])


def test_partial_failure_records_exchange_market(tmp_path, monkeypatch):
    out = tmp_path / "scan.json"
    _patch_sources(monkeypatch, bad_symbols={"GATEONLY"})
    code = build_site.main(["--timeframes", "4h,1d,1w", "--out", str(out)])
    assert code == 0  # 3 of 15 series = 20% is allowed
    data = json.loads(out.read_text(encoding="utf-8"))
    assert {(f["symbol"], f["exchange"], f["market"], f["stage"]) for f in data["failures"]} == {
        ("GATEONLYUSDT", "gate", "perp", "fetch")}
    assert data["source_counts"]["gate-perp"]["failed_series"] == 3


def test_non_primary_source_list_failure_is_not_fatal(tmp_path, monkeypatch):
    _patch_sources(monkeypatch, fail_okx_list=True)
    out = tmp_path / "scan.json"
    assert build_site.main(["--timeframes", "4h", "--out", str(out)]) == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    assert {e["source"] for e in data["source_errors"]} == {"okx-spot", "okx-perp"}
    assert "OKXCUSDT" not in data["settings"]["symbols"] and "GATEONLYUSDT" in data["settings"]["symbols"]


def test_sources_option_restricts(tmp_path, monkeypatch):
    _patch_sources(monkeypatch)
    out = tmp_path / "scan.json"
    assert build_site.main(["--sources", "okx-perp,gate-perp", "--timeframes", "4h", "--out", str(out)]) == 0
    assert json.loads(out.read_text(encoding="utf-8"))["settings"]["symbols"] == ["PPPUSDT", "GATEONLYUSDT"]
    assert build_site.main(["--sources", "bogus", "--out", str(out)]) == 2
