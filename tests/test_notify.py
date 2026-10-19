"""notify_discord: no network (requests.post is monkeypatched)."""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
import requests

import notify_discord as nd

URL = "https://discord.com/api/webhooks/123456/SECRET-TOKEN-xyz"
NOW = datetime(2026, 10, 18, 12, 7, tzinfo=timezone.utc)


def row(sym, status="watchlist", score=80.0, tf="1d", rs="2026-10-01T00:00:00Z", exchange="okx", market="perp",
        **extra):
    r = {"symbol": sym, "timeframe": tf, "status": status, "pattern_score": score,
         "exchange": exchange, "market": market, "tv_symbol": f"{exchange.upper()}:{sym}.P",
         "distance_to_neckline": 0.0123, "neckline_side": "above" if status == "confirmed" else "below",
         "current_close": 1.2, "current_neckline_price": 1.2345,
         "LS_time": "2026-08-01T00:00:00Z", "H_time": "2026-09-01T00:00:00Z", "RS_time": rs,
         "RS_confirm_time": None if status == "forming" else "2026-10-06T00:00:00Z",
         "breakout_detected": status == "confirmed", "tp1": 1.6, "tp2": 1.75, "invalidation_price": 0.8,
         "drop_left_pct": 0.241, "drop_right_pct": 0.228, "head_rise_pct": 0.452,
         "signal_time": "2026-10-02T00:00:00Z",
         "pattern_id": f"{exchange}|{market}|{sym}|{tf}|2026-08-01T00:00:00Z|2026-09-01T00:00:00Z|{rs}"}
    r.update(extra)
    return r


def key(sym, tf="1d", exchange="okx", market="perp"):
    return f"{exchange}|{market}|{sym}|{tf}|2026-08-01T00:00:00Z|2026-09-01T00:00:00Z"


def entry(status, notified=None, last_seen="2026-10-18T08:07:00Z"):
    return {"status": status, "score": 70.0, "last_seen": last_seen, "last_notified_at": "x" if notified else None,
            "notified_status": notified}


class Resp:
    def __init__(self, code=204, body=None, headers=None):
        self.status_code, self._body, self.headers = code, body or {}, headers or {}

    def json(self):
        return self._body


@pytest.fixture
def post(monkeypatch):
    calls, queue = [], []

    def fake(url, json=None, timeout=None):
        calls.append(json)
        return queue.pop(0) if queue else Resp()

    monkeypatch.setattr(nd.requests, "post", fake)
    fake.calls, fake.queue = calls, queue
    return fake


def setup(tmp_path, rows, state=None):
    scan = tmp_path / "scan.json"
    scan.write_text(json.dumps({"results": rows}), encoding="utf-8")
    st = tmp_path / "state.json"
    if state is not None:
        nd.save_state(st, state)
    return scan, st


def go(scan, st, **kw):
    args = dict(min_score=50, statuses={"forming", "watchlist", "confirmed"}, notify_all=False, dry_run=False,
                webhook=URL, now=NOW, sleep=lambda s: None, pause=0)
    args.update(kw)
    return nd.run(scan, st, **args)


def embeds(post):
    return [e for m in post.calls for e in m["embeds"]]


def titles(post):
    return sorted(e["title"] for e in embeds(post))


def test_key_excludes_rs():
    a, b = row("AAAUSDT", "forming", rs="2026-10-01T00:00:00Z"), row("AAAUSDT", "forming", rs="2026-10-03T00:00:00Z")
    assert a["pattern_id"] != b["pattern_id"]
    assert nd.notify_key(a) == nd.notify_key(b) == key("AAAUSDT")


def test_new_patterns_notified_and_recorded(tmp_path, post):
    rows = [row("AAAUSDT", "forming"), row("BBBUSDT"), row("CCCUSDT", "confirmed")]
    scan, st = setup(tmp_path, rows, state={})
    assert go(scan, st) == 0
    assert titles(post) == ["AAAUSDT 日線 右肩形成中", "BBBUSDT 日線 右肩已確認", "CCCUSDT 日線 已突破"]
    by = {e["title"]: e for e in embeds(post)}
    assert by["AAAUSDT 日線 右肩形成中"]["color"] == nd.COLOR_FORMING
    assert by["BBBUSDT 日線 右肩已確認"]["color"] == nd.COLOR_WATCHLIST
    assert by["CCCUSDT 日線 已突破"]["color"] == nd.COLOR_CONFIRMED
    assert by["CCCUSDT 日線 已突破"]["url"] == \
        "https://www.tradingview.com/chart/?symbol=OKX%3ACCCUSDT.P&interval=1D"
    state = nd.load_state(st)
    assert set(state) == {key("AAAUSDT"), key("BBBUSDT"), key("CCCUSDT")}
    assert state[key("AAAUSDT")]["notified_status"] == "forming"
    assert state[key("AAAUSDT")]["last_notified_at"] == "2026-10-18T12:07:00Z"
    assert json.loads(st.read_text(encoding="utf-8"))["version"] == 2


def test_embed_fields():
    e = nd.build_embed({"kind": "new", "row": row("GRAMUSDT", "forming", current_close=0.000175,
                                                  current_neckline_price=0.0002031, tp1=0.0002612,
                                                  tp2=0.00029484, invalidation_price=0.0001399)})
    f = {x["name"]: x["value"] for x in e["fields"]}
    assert e["title"] == "GRAMUSDT 日線 右肩形成中"
    assert f["交易所"] == "OKX 永續"
    assert f["目前價"] == "0.0001750"
    assert f["頸線價"] == "0.0002031（低於頸線 1.2%）"
    assert f["TP1"] == "0.0002612" and f["TP2（預估）"] == "0.0002948"  # D28: TP1 is never an estimate
    assert f["失效價（頭部低點）"] == "0.0001399"
    assert f["左／右肩跌幅"] == "24.1%／22.8%" and f["頭到頸線漲幅"] == "45.2%"
    assert "右肩尚未確認" in e["description"]
    c = nd.build_embed({"kind": "upgraded", "previous": "forming", "row": row("XUSDT", "confirmed", tf="4h")})
    f = {x["name"]: x["value"] for x in c["fields"]}
    assert "TP1" in f and "TP1（預估）" not in f
    assert "由「右肩形成中」轉為已突破" in c["description"]
    assert c["url"].endswith("&interval=240")


@pytest.mark.parametrize("value,expected", [
    (0.000175, "0.0001750"), (0.00000123, "0.000001230"), (0.0123, "0.0123"), (1.23456, "1.235"),
    (65432.1, "65,432.10"), (None, "-"), (0.0, "0.00")])
def test_fmt_price(value, expected):
    assert nd.fmt_price(value) == expected


def test_binance_spot_tv_url_fallback():
    r = row("BTCUSDT", exchange="binance", market="spot", tf="1w")
    r.pop("tv_symbol")
    assert nd.tradingview_url(r) == "https://www.tradingview.com/chart/?symbol=BINANCE%3ABTCUSDT&interval=1W"
    assert nd.market_label(r) == "幣安 現貨"


def test_second_run_same_scan_sends_nothing(tmp_path, post):
    scan, st = setup(tmp_path, [row("AAAUSDT", "forming")], state={})
    go(scan, st)
    post.calls.clear()
    assert go(scan, st) == 0
    assert post.calls == []


def test_forming_rs_moves_no_resend(tmp_path, post):
    scan, st = setup(tmp_path, [row("AAAUSDT", "forming", rs="2026-10-01T00:00:00Z")], state={})
    go(scan, st)
    post.calls.clear()
    scan, _ = setup(tmp_path, [row("AAAUSDT", "forming", rs="2026-10-04T00:00:00Z", score=66)])
    go(scan, st)
    assert post.calls == []


def test_forming_to_watchlist_not_notified(tmp_path, post):
    scan, st = setup(tmp_path, [row("AAAUSDT", "watchlist")], state={key("AAAUSDT"): entry("forming", "forming")})
    go(scan, st)
    assert post.calls == []
    s = nd.load_state(st)[key("AAAUSDT")]
    assert s["status"] == "watchlist" and s["notified_status"] == "forming"


@pytest.mark.parametrize("before", ["forming", "watchlist"])
def test_to_confirmed_is_notified(tmp_path, post, before):
    scan, st = setup(tmp_path, [row("AAAUSDT", "confirmed")], state={key("AAAUSDT"): entry(before, before)})
    go(scan, st)
    assert [e["title"] for e in embeds(post)] == ["AAAUSDT 日線 已突破"]
    assert nd.load_state(st)[key("AAAUSDT")]["notified_status"] == "confirmed"
    post.calls.clear()
    go(scan, st)  # still confirmed next run: no repeat
    assert post.calls == []


def test_below_threshold_tracked_then_notified_once_above(tmp_path, post):
    scan, st = setup(tmp_path, [row("AAAUSDT", "forming", score=49.9)], state={})
    go(scan, st)
    assert post.calls == []
    s = nd.load_state(st)[key("AAAUSDT")]
    assert s["status"] == "forming" and s["notified_status"] is None
    scan, _ = setup(tmp_path, [row("AAAUSDT", "forming", score=55)])
    go(scan, st)
    assert titles(post) == ["AAAUSDT 日線 右肩形成中"]
    post.calls.clear()
    go(scan, st)
    assert post.calls == []


def test_default_min_score_is_50(monkeypatch):
    monkeypatch.delenv(nd.ENV_MIN_SCORE, raising=False)
    assert nd.parse_args([]).min_score == 50
    assert nd.parse_args([]).statuses == "forming,watchlist,confirmed"
    monkeypatch.setenv(nd.ENV_MIN_SCORE, "65")
    assert nd.parse_args([]).min_score == 65


def test_status_filter(tmp_path, post):
    scan, st = setup(tmp_path, [row("AAAUSDT", "forming"), row("BBBUSDT", "confirmed")], state={})
    go(scan, st, statuses={"confirmed"})
    assert titles(post) == ["BBBUSDT 日線 已突破"]


def test_first_run_summary_only(tmp_path, post):
    scan, st = setup(tmp_path, [row("AAAUSDT", "forming"), row("BBBUSDT"), row("CCCUSDT", "confirmed", score=40)])
    go(scan, st)
    assert len(post.calls) == 1
    text = post.calls[0]["embeds"][0]["description"]
    assert "開始追蹤，目前 3 個型態" in text and "右肩形成中 1" in text and "分數 ≥ 50 的 2 個" in text
    state = nd.load_state(st)
    assert len(state) == 3 and state[key("AAAUSDT")]["notified_status"] == "forming"
    post.calls.clear()
    go(scan, st)
    assert post.calls == []


def test_old_version_state_is_first_run(tmp_path, post):
    scan, st = setup(tmp_path, [row("AAAUSDT", "forming"), row("BBBUSDT", "confirmed")])
    st.write_text(json.dumps({"version": 1, "patterns": {"binance|spot|X|1d|a|b|c": {"status": "watchlist"}}}),
                  encoding="utf-8")
    go(scan, st)
    assert len(post.calls) == 1 and "開始追蹤" in post.calls[0]["embeds"][0]["description"]
    data = json.loads(st.read_text(encoding="utf-8"))
    assert data["version"] == 2 and set(data["patterns"]) == {key("AAAUSDT"), key("BBBUSDT")}
    st.write_text(json.dumps({"patterns": {}}), encoding="utf-8")  # no version at all
    post.calls.clear()
    go(scan, st)
    assert "開始追蹤" in post.calls[0]["embeds"][0]["description"]


def test_first_run_notify_all(tmp_path, post):
    scan, st = setup(tmp_path, [row("AAAUSDT", "forming"), row("BBBUSDT")])
    go(scan, st, notify_all=True)
    assert len(embeds(post)) == 2


def test_no_webhook_updates_state(tmp_path, post, capsys):
    scan, st = setup(tmp_path, [row("AAAUSDT", "forming")], state={})
    assert go(scan, st, webhook=None) == 0
    out = capsys.readouterr().out
    assert "未設定 webhook，略過通知" in out
    assert post.calls == []
    assert nd.load_state(st)[key("AAAUSDT")]["notified_status"] == "forming"  # D20: not re-sent later


def test_dry_run_sends_nothing_writes_no_state(tmp_path, post, capsys):
    scan, st = setup(tmp_path, [row("AAAUSDT", "forming")], state={})
    go(scan, st, dry_run=True)
    assert post.calls == [] and "AAAUSDT 日線 右肩形成中" in capsys.readouterr().out
    assert nd.load_state(st) == {}


def test_429_retry_after_then_success(tmp_path, post):
    waits = []
    post.queue.extend([Resp(429, {"retry_after": 2.5}), Resp(429, {"retry_after": 1}), Resp(204)])
    scan, st = setup(tmp_path, [row("AAAUSDT")], state={})
    assert go(scan, st, sleep=waits.append) == 0
    assert waits[:2] == [2.5, 1.0]
    assert len(post.calls) == 3


def test_retries_are_bounded_and_failure_not_recorded(tmp_path, post, capsys):
    post.queue.extend([Resp(429, {"retry_after": 0})] * 50)
    scan, st = setup(tmp_path, [row("AAAUSDT")], state={})
    assert go(scan, st) == 1
    assert len(post.calls) == nd.MAX_RETRIES + 1
    assert nd.load_state(st)[key("AAAUSDT")]["notified_status"] is None  # retried next run
    assert URL not in capsys.readouterr().err
    post.calls.clear()
    post.queue.clear()
    assert go(scan, st) == 0
    assert titles(post) == ["AAAUSDT 日線 右肩已確認"]


def test_failed_upgrade_is_retried(tmp_path, post):
    post.queue.append(Resp(404))
    scan, st = setup(tmp_path, [row("AAAUSDT", "confirmed")], state={key("AAAUSDT"): entry("forming", "forming")})
    assert go(scan, st) == 1
    assert nd.load_state(st)[key("AAAUSDT")]["notified_status"] == "forming"
    post.calls.clear()
    go(scan, st)
    assert titles(post) == ["AAAUSDT 日線 已突破"]


def test_4xx_fails_without_retry(tmp_path, post):
    post.queue.append(Resp(404))
    scan, st = setup(tmp_path, [row("AAAUSDT")], state={})
    assert go(scan, st) == 1
    assert len(post.calls) == 1


def test_embed_chunking_and_summary(tmp_path, post):
    rows = [row(f"S{i:02d}USDT", "forming", score=90 - i * 0.1) for i in range(25)]
    scan, st = setup(tmp_path, rows, state={})
    go(scan, st)
    sizes = [len(m["embeds"]) for m in post.calls]
    assert sizes == [10, 10, 1]
    assert "另有 5 個" in post.calls[-1]["embeds"][0]["description"]
    state = nd.load_state(st)
    assert len(state) == 25 and all(v["notified_status"] == "forming" for v in state.values())


def test_embed_numbers_split_of_exact_multiple():
    ev = [{"kind": "new", "row": row(f"S{i}USDT")} for i in range(12)]
    msgs = nd.build_messages(ev)
    assert [len(m["embeds"]) for m in msgs] == [10, 2]


def test_prune_old_missing_patterns(tmp_path, post):
    state = {"GONE_OLD": entry("watchlist", "watchlist", "2026-09-01T00:00:00Z"),
             "GONE_RECENT": entry("watchlist", "watchlist", "2026-10-17T00:00:00Z")}
    scan, st = setup(tmp_path, [], state=state)
    go(scan, st)
    assert set(nd.load_state(st)) == {"GONE_RECENT"}


def test_flicker_does_not_renotify(tmp_path, post):
    scan, st = setup(tmp_path, [row("AAAUSDT", "forming")], state={})
    go(scan, st)
    scan, _ = setup(tmp_path, [])
    go(scan, st)  # gone for one run (e.g. a rule failed on the new bar)
    scan, _ = setup(tmp_path, [row("AAAUSDT", "forming", rs="2026-10-05T00:00:00Z")])
    post.calls.clear()
    go(scan, st)
    assert post.calls == []


def test_url_never_printed(tmp_path, monkeypatch, capsys):
    def boom(url, json=None, timeout=None):
        raise requests.ConnectionError(f"cannot reach {url}")

    monkeypatch.setattr(nd.requests, "post", boom)
    scan, st = setup(tmp_path, [row("AAAUSDT")], state={})
    assert go(scan, st) == 1
    cap = capsys.readouterr()
    assert "SECRET-TOKEN" not in cap.out + cap.err and "discord.com/api" not in cap.out + cap.err
    assert "已設定" in cap.out
    assert "SECRET-TOKEN" not in st.read_text(encoding="utf-8") if st.exists() else True


def test_main_reads_env_and_never_echoes(tmp_path, monkeypatch, capsys, post):
    scan, st = setup(tmp_path, [row("AAAUSDT")], state={})
    monkeypatch.setenv(nd.ENV_WEBHOOK, URL)
    assert nd.main(["--scan", str(scan), "--state", str(st), "--min-score", "10"]) == 0
    cap = capsys.readouterr()
    assert "SECRET-TOKEN" not in cap.out + cap.err and len(post.calls) == 1
    monkeypatch.delenv(nd.ENV_WEBHOOK)
    assert nd.main(["--scan", str(scan), "--state", str(st)]) == 0
    assert "未設定" in capsys.readouterr().out
