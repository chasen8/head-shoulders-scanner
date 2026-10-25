"""D45 max-position filter: OKX / Gate limit formulas and units, threshold, D22 dedup interaction (fallback to the next
source), scan.json metadata, unknown values and failed lookups (kept). No network: fake sessions and monkeypatch."""
from __future__ import annotations

import json

import pytest

import build_site
from ihs_scanner import data_gate, data_okx
from ihs_scanner.config import MIN_MAX_POSITION_USDT
from ihs_scanner.sources import Instrument, SourceFetchError, merge_instruments, merge_with_position_limit

from .synth import IHS_BREAK, make_df


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


def _i(base, ex, mk, native=None):
    return Instrument(base=base, symbol=base + "USDT", native=native or base, exchange=ex, market=mk)


def test_default_threshold_is_10000_usdt():
    assert MIN_MAX_POSITION_USDT == 10_000.0
    assert build_site.parse_args([]).min_max_position == 10_000.0


# ---------------------------------------------------------------- OKX
def test_okx_position_value_units():
    inst = {"ctVal": "10", "ctMult": "1", "posLmtAmt": "", "posLmtPct": ""}
    # 500 contracts x 10 coins x 1.5 USDT = 7,500 USDT
    assert data_okx.position_value(inst, 500.0, 1.5) == pytest.approx(7500.0)
    assert data_okx.position_value({**inst, "ctMult": "2"}, 500.0, 1.5) == pytest.approx(15000.0)
    # user-level limit: max(posLmtAmt, oiUsd * posLmtPct %) caps the tier value
    capped = {**inst, "posLmtAmt": "5000", "posLmtPct": "30"}
    assert data_okx.position_value(capped, 500.0, 1.5) == pytest.approx(5000.0)
    assert data_okx.position_value(capped, 500.0, 1.5, oi_usd=20000.0) == pytest.approx(6000.0)
    assert data_okx.position_value(capped, 500.0, 1.5, oi_usd=1e9) == pytest.approx(7500.0)
    for args in ((None, 1.5), (500.0, None), (0.0, 1.5)):
        assert data_okx.position_value(inst, *args) is None
    assert data_okx.position_value({"ctVal": ""}, 500.0, 1.5) is None


def _okx_handler(*, fail_batch_with=None):
    insts = [{"instId": f"C{k}-USDT-SWAP", "instFamily": f"C{k}-USDT", "ctVal": "1", "ctMult": "1",
              "posLmtAmt": "350000", "posLmtPct": "25"} for k in range(7)]
    tiers = {f"C{k}-USDT": [("100", 1), (str(1000 * (k + 1)), 2)] for k in range(7)}  # highest tier = 1000*(k+1)

    def handler(url, params):
        if url.endswith("/public/instruments"):
            return Resp({"code": "0", "data": insts})
        if url.endswith("/public/mark-price"):
            return Resp({"code": "0", "data": [{"instId": r["instId"], "markPx": "2"} for r in insts
                                               if r["instId"] != "C6-USDT-SWAP"]})
        if url.endswith("/public/open-interest"):
            return Resp({"code": "0", "data": [{"instId": r["instId"], "oiUsd": "1000"} for r in insts]})
        if url.endswith("/public/position-tiers"):
            fams = params["instFamily"].split(",")
            assert len(fams) <= data_okx.TIERS_PER_REQUEST and params["tdMode"] == "cross"
            if fail_batch_with and fail_batch_with in fams:
                return Resp({"code": "51000", "msg": "bad", "data": []})
            return Resp({"code": "0", "data": [{"instFamily": f, "maxSz": sz, "tier": str(t)}
                                               for f in fams for sz, t in tiers[f]]})
        raise AssertionError(url)
    return handler


def test_okx_max_position_values_batches_and_takes_highest_tier():
    sess = FakeSession(_okx_handler())
    ids = [f"C{k}-USDT-SWAP" for k in range(7)] + ["NOPE-USDT-SWAP"]
    got = data_okx.max_position_values(sess, ids, sleep=lambda s: None)
    tier_calls = [p for u, p in sess.calls if u.endswith("position-tiers")]
    assert len(tier_calls) == 2  # 7 families, 5 per request
    for k in range(6):
        assert got[f"C{k}-USDT-SWAP"] == pytest.approx(1000 * (k + 1) * 1 * 2)
    assert got["C6-USDT-SWAP"] is None  # no mark price
    assert got["NOPE-USDT-SWAP"] is None  # not listed


def test_okx_failed_tier_batch_leaves_unknown():
    sess = FakeSession(_okx_handler(fail_batch_with="C0-USDT"))
    got = data_okx.max_position_values(sess, [f"C{k}-USDT-SWAP" for k in range(6)], sleep=lambda s: None)
    assert all(got[f"C{k}-USDT-SWAP"] is None for k in range(5))  # the whole first batch failed
    assert got["C5-USDT-SWAP"] == pytest.approx(12000.0)


# ---------------------------------------------------------------- Gate
def test_gate_position_value_is_usdt_risk_limit_max():
    row = {"name": "SKYAI_USDT", "risk_limit_base": "10000", "risk_limit_max": "2500000", "quanto_multiplier": "10",
           "mark_price": "0.036"}
    assert data_gate.position_value(row) == 2_500_000.0  # USDT notional: not x quanto_multiplier x price
    assert data_gate.position_value({"risk_limit_max": None}) is None
    assert data_gate.position_value({"risk_limit_max": "0"}) is None


def test_gate_max_position_values_one_call():
    rows = [{"name": "AAA_USDT", "risk_limit_max": "5000"}, {"name": "BBB_USDT", "risk_limit_max": "50000"}]
    sess = FakeSession(lambda url, params: Resp(rows))
    got = data_gate.max_position_values(sess, ["AAA_USDT", "BBB_USDT", "CCC_USDT"])
    assert got == {"AAA_USDT": 5000.0, "BBB_USDT": 50000.0, "CCC_USDT": None} and len(sess.calls) == 1
    with pytest.raises(SourceFetchError):
        data_gate.max_position_values(FakeSession(lambda u, p: Resp({"label": "x"})), ["AAA_USDT"])


# ---------------------------------------------------------------- merge + filter
BY_SOURCE = {
    "binance-spot": [_i("AAA", "binance", "spot")],
    "okx-spot": [_i("TINY", "okx", "spot")],                       # spot: never filtered
    "okx-perp": [_i("AAA", "okx", "perp"), _i("SMALL", "okx", "perp"), _i("BOTH", "okx", "perp"),
                 _i("UNK", "okx", "perp"), _i("BIG", "okx", "perp")],
    "gate-perp": [_i("SMALL", "gate", "perp"), _i("BOTH", "gate", "perp"), _i("GONLY", "gate", "perp")],
}
LIMITS = {"okx-perp": {"SMALL": 9_999.99, "BOTH": 3_000.0, "UNK": None, "BIG": 10_000.0},
          "gate-perp": {"SMALL": 2_500_000.0, "BOTH": 4_000.0, "GONLY": 50_000.0}}


def _fns(calls):
    def make(key):
        def fn(insts):
            calls.setdefault(key, []).extend(i.native for i in insts)
            return {n: v for n, v in LIMITS[key].items()}
        return fn
    return {k: make(k) for k in LIMITS}


def test_filter_threshold_fallback_and_lookup_scope():
    calls = {}
    pf = merge_with_position_limit(BY_SOURCE, _fns(calls), 10_000.0)
    got = {i.base: i.source for i in pf.merged}
    # SMALL: OKX limit < 10k -> Gate (2.5M) supplies it; BOTH: excluded on both; UNK: unknown -> kept;
    # BIG: exactly 10,000 is kept (>=); spot TINY never looked up
    assert got == {"AAA": "binance-spot", "TINY": "okx-spot", "SMALL": "gate-perp", "UNK": "okx-perp",
                   "BIG": "okx-perp", "GONLY": "gate-perp"}
    assert calls["okx-perp"] == ["BIG", "BOTH", "SMALL", "UNK"]  # AAA already taken by binance-spot: not looked up
    assert calls["gate-perp"] == ["BOTH", "GONLY", "SMALL"]
    ex = {(e["source"], e["symbol"]): e for e in pf.excluded}
    assert set(ex) == {("okx-perp", "SMALLUSDT"), ("okx-perp", "BOTHUSDT"), ("gate-perp", "BOTHUSDT")}
    assert ex[("okx-perp", "SMALLUSDT")]["replaced_by"] == "gate-perp"
    assert ex[("okx-perp", "BOTHUSDT")]["replaced_by"] is None
    assert ex[("okx-perp", "SMALLUSDT")]["max_position_usdt"] == 9999.99
    assert pf.checked == {"okx-perp": 4, "gate-perp": 3} and pf.unknown == {"okx-perp": 1}


def test_filter_off_equals_merge_instruments():
    calls = {}
    for pf in (merge_with_position_limit(BY_SOURCE, _fns(calls), 0.0), merge_with_position_limit(BY_SOURCE, {}, 1e4)):
        assert pf.merged == merge_instruments(BY_SOURCE) and not pf.excluded
    assert calls == {}


def test_failed_lookup_keeps_everything_and_records():
    def boom(insts):
        raise SourceFetchError("HTTP 500")
    pf = merge_with_position_limit(BY_SOURCE, {"okx-perp": boom}, 1e4, errors=(SourceFetchError,))
    assert {i.base: i.source for i in pf.merged} == {i.base: i.source for i in merge_instruments(BY_SOURCE)}
    assert pf.errors and pf.errors[0]["source"] == "okx-perp" and "HTTP 500" in pf.errors[0]["error"]
    assert pf.unknown == {"okx-perp": 4}


# ---------------------------------------------------------------- build_site end to end
def _patch(monkeypatch, okx_limits, gate_limits):
    monkeypatch.setattr(build_site, "list_usdt_symbols", lambda **k: ["AAAUSDT"])
    monkeypatch.setattr(build_site.data_okx, "list_instruments", lambda sess, market, **k: (
        [_i("OSPOT", "okx", "spot")] if market == "spot" else
        [_i("PPP", "okx", "perp", "PPP-USDT-SWAP"), _i("QQQ", "okx", "perp", "QQQ-USDT-SWAP")]))
    monkeypatch.setattr(build_site.data_gate, "list_instruments", lambda sess, **k: [
        _i("PPP", "gate", "perp", "PPP_USDT"), _i("SKYAI", "gate", "perp", "SKYAI_USDT")])
    seen = {}

    def okx_caps(sess, ids, **k):
        seen["okx"] = list(ids)
        return {i: okx_limits.get(i) for i in ids}

    def gate_caps(sess, ids, **k):
        seen["gate"] = list(ids)
        return {i: gate_limits.get(i) for i in ids}

    monkeypatch.setattr(build_site.data_okx, "max_position_values", okx_caps)
    monkeypatch.setattr(build_site.data_gate, "max_position_values", gate_caps)
    fetched = []

    def fetch(symbol, timeframe, limit=1000, **k):
        fetched.append(symbol)
        return make_df(IHS_BREAK, timeframe=timeframe)
    monkeypatch.setattr(build_site, "fetch_klines", fetch)
    monkeypatch.setattr(build_site.data_okx, "fetch_klines", lambda nid, tf, limit=1000, **k: fetch(nid, tf))
    monkeypatch.setattr(build_site.data_gate, "fetch_klines", lambda nid, tf, limit=1000, **k: fetch(nid, tf))
    return seen, fetched


def test_build_excludes_before_fetch_and_records_metadata(tmp_path, monkeypatch, capsys):
    seen, fetched = _patch(monkeypatch, {"PPP-USDT-SWAP": 8_000.0, "QQQ-USDT-SWAP": None},
                           {"PPP_USDT": 4_000.0, "SKYAI_USDT": 2_500_000.0})
    out = tmp_path / "scan.json"
    assert build_site.main(["--timeframes", "4h", "--out", str(out)]) == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["settings"]["symbols"] == ["AAAUSDT", "OSPOTUSDT", "QQQUSDT", "SKYAIUSDT"]
    assert "PPP-USDT-SWAP" not in fetched and "PPP_USDT" not in fetched  # no K line request for excluded coins
    assert seen == {"okx": ["PPP-USDT-SWAP", "QQQ-USDT-SWAP"], "gate": ["PPP_USDT", "SKYAI_USDT"]}
    sc = data["source_counts"]
    assert sc["okx-perp"]["excluded_max_position"] == 1 and sc["okx-perp"]["max_position_unknown"] == 1
    assert sc["gate-perp"]["excluded_max_position"] == 1 and sc["gate-perp"]["max_position_unknown"] == 0
    assert "excluded_max_position" not in sc["binance-spot"] and "excluded_max_position" not in sc["okx-spot"]
    mpf = data["max_position_filter"]
    assert mpf["min_usdt"] == 10_000.0 and mpf["applied_sources"] == ["okx-perp", "gate-perp"]
    assert set(mpf["not_applied"]) == {"binance-spot", "okx-spot"}
    assert [(e["source"], e["symbol"], e["replaced_by"]) for e in mpf["excluded"]] == [
        ("okx-perp", "PPPUSDT", None), ("gate-perp", "PPPUSDT", None)]
    assert {r["symbol"] for r in data["results"]} <= set(data["settings"]["symbols"])
    log = capsys.readouterr().out
    assert "[max position] < 10,000 USDT: okx-perp: 2 checked, 1 excluded, 1 unknown (kept)" in log


def test_build_fallback_to_gate_and_threshold_option(tmp_path, monkeypatch):
    _patch(monkeypatch, {"PPP-USDT-SWAP": 8_000.0}, {"PPP_USDT": 60_000.0, "SKYAI_USDT": 2_500_000.0})
    out = tmp_path / "scan.json"
    assert build_site.main(["--timeframes", "4h", "--out", str(out)]) == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    ppp = [r for r in data["results"] if r["symbol"] == "PPPUSDT"]
    assert ppp and all(r["exchange"] == "gate" for r in ppp)
    assert data["max_position_filter"]["excluded"][0]["replaced_by"] == "gate-perp"
    # --min-max-position 0 switches the filter off: OKX keeps PPP, nothing is looked up
    assert build_site.main(["--timeframes", "4h", "--min-max-position", "0", "--out", str(out)]) == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    assert {r["exchange"] for r in data["results"] if r["symbol"] == "PPPUSDT"} == {"okx"}
    assert data["max_position_filter"]["excluded"] == [] and data["max_position_filter"]["applied_sources"] == []


def test_build_lookup_failure_keeps_all(tmp_path, monkeypatch):
    _patch(monkeypatch, {}, {})

    def boom(sess, ids, **k):
        raise SourceFetchError("okx tiers down")
    monkeypatch.setattr(build_site.data_okx, "max_position_values", boom)
    out = tmp_path / "scan.json"
    assert build_site.main(["--timeframes", "4h", "--out", str(out)]) == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    assert "PPPUSDT" in data["settings"]["symbols"] and data["source_errors"] == []
    assert data["max_position_filter"]["errors"][0]["source"] == "okx-perp"
