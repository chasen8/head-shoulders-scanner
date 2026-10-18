"""notify_discord: no network (requests.post is monkeypatched)."""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
import requests

import notify_discord as nd

URL = "https://discord.com/api/webhooks/123456/SECRET-TOKEN-xyz"
NOW = datetime(2026, 10, 18, 12, 7, tzinfo=timezone.utc)


def row(sym, status="watchlist", score=80.0, tf="1d"):
    return {"symbol": sym, "timeframe": tf, "status": status, "pattern_score": score,
            "distance_to_neckline": 0.0123, "neckline_side": "above" if status == "confirmed" else "below",
            "current_neckline_price": 1.2345, "RS_time": "2026-10-01T00:00:00Z",
            "signal_time": "2026-10-02T00:00:00Z", "pattern_id": f"{sym}|{tf}|a|b|c"}


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
    args = dict(min_score=60, statuses={"watchlist", "confirmed"}, notify_all=False, dry_run=False,
                webhook=URL, now=NOW, sleep=lambda s: None, pause=0)
    args.update(kw)
    return nd.run(scan, st, **args)


def embeds(post):
    return [e for m in post.calls for e in m["embeds"]]


def test_new_pattern_notified_and_recorded(tmp_path, post):
    scan, st = setup(tmp_path, [row("AAAUSDT"), row("BBBUSDT", "confirmed")], state={})
    assert go(scan, st) == 0
    titles = [e["title"] for e in embeds(post)]
    assert titles == ["AAAUSDT 日線 觀察中", "BBBUSDT 日線 已突破"] or sorted(titles) == sorted(
        ["AAAUSDT 日線 觀察中", "BBBUSDT 日線 已突破"])
    by = {e["title"]: e for e in embeds(post)}
    assert by["BBBUSDT 日線 已突破"]["color"] == nd.COLOR_CONFIRMED
    assert by["AAAUSDT 日線 觀察中"]["color"] == nd.COLOR_WATCHLIST
    assert by["BBBUSDT 日線 已突破"]["url"] == "https://www.tradingview.com/chart/?symbol=BINANCE:BBBUSDT&interval=1D"
    assert set(nd.load_state(st)) == {"AAAUSDT|1d|a|b|c", "BBBUSDT|1d|a|b|c"}


def test_second_run_same_scan_sends_nothing(tmp_path, post):
    scan, st = setup(tmp_path, [row("AAAUSDT")], state={})
    go(scan, st)
    post.calls.clear()
    assert go(scan, st) == 0
    assert post.calls == []


def test_watchlist_to_confirmed_upgrade(tmp_path, post):
    scan, st = setup(tmp_path, [row("AAAUSDT", "confirmed")],
                     state={"AAAUSDT|1d|a|b|c": {"status": "watchlist", "score": 70, "last_seen": "x",
                                                 "last_notified_at": "y"}})
    go(scan, st)
    assert [e["title"] for e in embeds(post)] == ["AAAUSDT 日線 已突破（由觀察中升級）"]
    assert nd.load_state(st)["AAAUSDT|1d|a|b|c"]["status"] == "confirmed"


def test_below_threshold_not_sent_but_tracked(tmp_path, post):
    scan, st = setup(tmp_path, [row("AAAUSDT", score=59.9)], state={})
    go(scan, st)
    assert post.calls == []
    assert "AAAUSDT|1d|a|b|c" in nd.load_state(st)
    go(scan, st, min_score=50)  # already in state -> still not "new"
    assert post.calls == []


def test_status_filter(tmp_path, post):
    scan, st = setup(tmp_path, [row("AAAUSDT"), row("BBBUSDT", "confirmed")], state={})
    go(scan, st, statuses={"confirmed"})
    assert [e["title"] for e in embeds(post)] == ["BBBUSDT 日線 已突破"]


def test_first_run_summary_only(tmp_path, post):
    scan, st = setup(tmp_path, [row("AAAUSDT"), row("BBBUSDT")])
    go(scan, st)
    assert len(post.calls) == 1
    assert "開始追蹤，目前 2 個型態" in post.calls[0]["embeds"][0]["description"]
    assert len(nd.load_state(st)) == 2
    post.calls.clear()
    go(scan, st)
    assert post.calls == []


def test_first_run_notify_all(tmp_path, post):
    scan, st = setup(tmp_path, [row("AAAUSDT"), row("BBBUSDT")])
    go(scan, st, notify_all=True)
    assert len(embeds(post)) == 2


def test_no_webhook_updates_state(tmp_path, post, capsys):
    scan, st = setup(tmp_path, [row("AAAUSDT")])
    assert go(scan, st, webhook=None) == 0
    out = capsys.readouterr().out
    assert "未設定 webhook，略過通知" in out
    assert post.calls == []
    assert len(nd.load_state(st)) == 1


def test_dry_run_sends_nothing_writes_no_state(tmp_path, post, capsys):
    scan, st = setup(tmp_path, [row("AAAUSDT")], state={})
    go(scan, st, dry_run=True)
    assert post.calls == [] and "AAAUSDT 日線 觀察中" in capsys.readouterr().out
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
    assert nd.load_state(st) == {}  # retried next run
    assert URL not in capsys.readouterr().err


def test_4xx_fails_without_retry(tmp_path, post):
    post.queue.append(Resp(404))
    scan, st = setup(tmp_path, [row("AAAUSDT")], state={})
    assert go(scan, st) == 1
    assert len(post.calls) == 1


def test_embed_chunking_and_summary(tmp_path, post):
    rows = [row(f"S{i:02d}USDT", score=90 - i * 0.1) for i in range(25)]
    scan, st = setup(tmp_path, rows, state={})
    go(scan, st)
    sizes = [len(m["embeds"]) for m in post.calls]
    assert sizes == [10, 10, 1]
    assert max(sizes) <= 10
    assert "另有 5 個" in post.calls[-1]["embeds"][0]["description"]
    assert len(nd.load_state(st)) == 25  # the 5 summarised ones count as notified


def test_embed_numbers_split_of_exact_multiple():
    ev = [{"kind": "new", "row": row(f"S{i}USDT")} for i in range(12)]
    msgs = nd.build_messages(ev)
    assert [len(m["embeds"]) for m in msgs] == [10, 2]


def test_prune_old_missing_patterns(tmp_path, post):
    old = {"status": "watchlist", "score": 70, "last_notified_at": "x"}
    state = {"GONE_OLD": {**old, "last_seen": "2026-09-01T00:00:00Z"},
             "GONE_RECENT": {**old, "last_seen": "2026-10-17T00:00:00Z"}}
    scan, st = setup(tmp_path, [], state=state)
    go(scan, st)
    assert set(nd.load_state(st)) == {"GONE_RECENT"}


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
