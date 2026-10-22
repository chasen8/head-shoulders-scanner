"""D39 take-profit tracking: ihs_scanner/tp_tracker.py + notify_tp.py. No network (fetch / requests monkeypatched)."""
from __future__ import annotations

import json
import json as _json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pytest
import requests

import notify_discord as nd
import notify_tp as ntp
from ihs_scanner import tp_tracker as tt
from scripts import merge_published as mp

TOKEN = "BOT-SECRET-TOKEN-abc123"
GUILD = "998877665544332211"
DAY = timedelta(days=1)
T0 = datetime(2026, 10, 1, tzinfo=timezone.utc)  # breakout bar (1d) opens here


def iso(t: datetime) -> str:
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def frame(highs_lows, start=T0, step=DAY):
    """[(high, low), ...] -> common frame from `start`, one bar per step (open = close = midpoint)."""
    rows = []
    for i, (h, lo) in enumerate(highs_lows):
        t = pd.Timestamp(start + i * step)
        mid = (h + lo) / 2
        rows.append({"timestamp": t, "open": mid, "high": h, "low": lo, "close": mid, "volume": 1.0,
                     "close_time": t + pd.Timedelta(step) - pd.Timedelta(milliseconds=1)})
    return pd.DataFrame(rows)


def after(n_bars, step=DAY):
    """`now` just after the n-th bar (from T0) closed."""
    return T0 + n_bars * step + timedelta(minutes=7)


def ihs_row(sym="BTCUSDT", exchange="binance", market="spot", tf="1d", name=None, **extra):
    r = {"symbol": sym, "timeframe": tf, "status": "confirmed", "pattern_score": 80.0, "exchange": exchange,
         "market": market, "tv_symbol": f"{exchange.upper()}:{sym}", "breakout_time": iso(T0),
         "breakout_level": 100.0, "breakout_price": 104.0, "tp1": 120.0, "tp2": 140.0, "invalidation_price": 80.0,
         "signal_time": iso(T0), "pattern_id": f"{exchange}|{market}|{sym}|{tf}|LS|H|RS",
         "pattern_key": f"{exchange}|{market}|{sym}|{tf}|LS|H"}
    if name:
        r["name"] = name
    r.update(extra)
    return r


def tri_row(sym="ETHUSDT", status="breakout_up", **extra):
    r = {"pattern_type": "triangle", "triangle_type": "ascending", "status": status, "symbol": sym,
         "timeframe": "1d", "score": 70.0, "exchange": "okx", "market": "spot", "tv_symbol": f"OKX:{sym}",
         "breakout_time": iso(T0), "breakout_price": 50.0, "target_conservative": 55.0, "target": 60.0,
         "invalidation_price": 45.0, "signal_time": iso(T0), "pattern_id": f"okx|spot|{sym}|1d|tri|S"}
    r.update(extra)
    return r


def write_scan(tmp: Path, rows, market="crypto", tri_rows=()):
    d = tmp / "data"
    d.mkdir(parents=True, exist_ok=True)
    (d / "scan.json").write_text(json.dumps({"market": market, "results": list(rows)}), encoding="utf-8")
    (d / "triangles.json").write_text(json.dumps({"market": market, "results": list(tri_rows)}), encoding="utf-8")
    return d / "scan.json"


class Resp:
    def __init__(self, code=200, body=None, headers=None):
        self.status_code, self._body, self.headers = code, body if body is not None else {}, headers or {}

    def json(self):
        return self._body


CHANNELS = [{"id": "111", "name": "一般", "type": 0}, {"id": "222", "name": "加密止盈", "type": 0},
            {"id": "333", "name": "台股止盈", "type": 0}, {"id": "444", "name": "加密止盈", "type": 2}]


@pytest.fixture
def discord(monkeypatch):
    """Fake Discord: GET guild channels (queue `gets`, default CHANNELS), POST messages (queue `posts`)."""
    state = {"gets": [], "posts": [], "get_calls": [], "post_calls": []}

    def get(url, headers=None, timeout=None):
        state["get_calls"].append({"url": url, "headers": headers})
        return state["gets"].pop(0) if state["gets"] else Resp(200, CHANNELS)

    def post(url, json=None, data=None, files=None, timeout=None, headers=None):
        payload = json if json is not None else _json.loads(data["payload_json"])
        state["post_calls"].append({"url": url, "payload": payload, "files": files, "headers": headers})
        return state["posts"].pop(0) if state["posts"] else Resp(200)

    monkeypatch.setattr(requests, "get", get)
    monkeypatch.setattr(requests, "post", post)
    return state


def fake_fetch(frames):
    calls = []

    def fetch(item, limit):
        calls.append((item["symbol"], item["timeframe"], limit))
        f = frames.get(item["symbol"])
        if isinstance(f, Exception):
            raise f
        return f

    fetch.calls = calls
    return fetch


def run(scan, tracker, frames, now, **kw):
    kw.setdefault("bot_token", TOKEN)
    kw.setdefault("guild_id", GUILD)
    kw.setdefault("sleep", lambda s: None)
    kw.setdefault("pause", 0)
    return ntp.run(scan, tracker, fetch=fake_fetch(frames) if not callable(frames) else frames, now=now, **kw)


def tracker_items(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))["items"]


def seed(tmp_path, rows, *, market="crypto", tri_rows=()):
    """A second-run setup: tracker already exists (empty), so events are notified."""
    scan = write_scan(tmp_path, rows, market, tri_rows)
    tracker = tmp_path / "tp.json"
    ntp.save_tracker(tracker, {}, market, T0)
    return scan, tracker


# ------------------------------------------------------------------ tracker logic (pure)

def test_ihs_item_uses_neckline_as_breakout_price():
    it = tt.ihs_item(ihs_row(), now=T0)
    assert it["breakout_price"] == 100.0 and it["breakout_bar_high"] == 104.0
    assert (it["tp1"], it["tp2"], it["invalidation_price"]) == (120.0, 140.0, 80.0)
    assert it["kind"] == "ihs" and it["status"] == "open" and it["pattern_label"] == "頭肩底"
    assert tt.ihs_item(ihs_row(status="watchlist"), now=T0) is None
    assert tt.ihs_item(ihs_row(tp2=None), now=T0) is None


def test_triangle_breakout_up_tracked_with_conservative_tp1_down_not():
    it = tt.triangle_item(tri_row(), now=T0)
    assert (it["tp1"], it["tp2"], it["breakout_price"]) == (55.0, 60.0, 50.0)
    assert it["kind"] == "triangle" and it["pattern_label"] == "上升三角"
    assert tt.triangle_item(tri_row(status="breakout_down"), now=T0) is None
    assert tt.triangle_item(tri_row(status="forming"), now=T0) is None


def test_tp1_then_tp2_ends_tracking():
    it = tt.ihs_item(ihs_row(), now=T0)
    df = frame([(105, 99), (121, 110), (130, 115), (141, 125), (150, 140)])
    res = tt.evaluate(it, df, now=after(5))
    assert res["status"] == "tp2"
    assert [(e["type"], e["bars"], e["price"]) for e in res["events"]] == [("tp1", 1, 120.0), ("tp2", 3, 140.0)]
    res3 = tt.evaluate(it, df, now=after(2))  # as of bar 1: only TP1
    assert res3["status"] == "tp1" and [e["type"] for e in res3["events"]] == ["tp1"]


def test_tp2_in_one_bar_records_both():
    res = tt.evaluate(tt.ihs_item(ihs_row(), now=T0), frame([(145, 99)]), now=after(1))
    assert res["status"] == "tp2" and [e["type"] for e in res["events"]] == ["tp1", "tp2"]


def test_failed_before_tp1():
    res = tt.evaluate(tt.ihs_item(ihs_row(), now=T0), frame([(105, 95), (110, 79), (130, 100)]), now=after(3))
    assert res["status"] == "failed" and [(e["type"], e["bars"]) for e in res["events"]] == [("failed", 1)]


def test_same_bar_tp_and_invalidation_is_failed():
    """Intrabar order unknown -> the bar is read conservatively as stopped out."""
    res = tt.evaluate(tt.ihs_item(ihs_row(), now=T0), frame([(105, 95), (125, 79)]), now=after(2))
    assert res["status"] == "failed" and [e["type"] for e in res["events"]] == ["failed"]


def test_failure_after_tp1_ends_and_keeps_tp1():
    res = tt.evaluate(tt.ihs_item(ihs_row(), now=T0), frame([(121, 99), (110, 79)]), now=after(2))
    assert res["status"] == "failed" and [e["type"] for e in res["events"]] == ["tp1", "failed"]


def test_expired_after_max_bars():
    it = tt.ihs_item(ihs_row(), now=T0)
    df = frame([(105, 95)] * 10)
    res = tt.evaluate(it, df, now=after(10), max_bars={"1d": 5})
    assert res["status"] == "expired" and res["events"][-1]["bars"] == 5
    assert tt.evaluate(it, df, now=after(5), max_bars={"1d": 5})["status"] == "open"  # bars 0..4 only
    assert tt.TP_CONFIG["max_bars"] == {"4h": 180, "1d": 120, "1w": 52}


def test_only_closed_bars_and_truncation():
    """The bar still open at `now` is ignored; as-of-t results only depend on df[:t+1]."""
    it = tt.ihs_item(ihs_row(), now=T0)
    df = frame([(105, 95), (110, 99), (125, 100), (141, 120)])
    open_now = T0 + 2 * DAY + timedelta(hours=3)  # bar 2 (high 125 >= TP1) has not closed yet
    assert tt.evaluate(it, df, now=open_now)["events"] == []
    full = tt.evaluate(it, df, now=after(4))
    for t in range(len(df)):
        part = tt.evaluate(it, df.iloc[:t + 1], now=after(t + 1))
        assert part["events"] == [e for e in full["events"] if e["bars"] <= t]
        assert tt.evaluate(it, df, now=after(t + 1)) == part


def test_bars_before_breakout_are_ignored():
    it = tt.ihs_item(ihs_row(), now=T0)
    df = frame([(130, 70), (105, 95)], start=T0 - DAY)  # the bar before the breakout touched everything
    assert tt.evaluate(it, df, now=after(1))["events"] == []


# ------------------------------------------------------------------ notify_tp run

def test_first_run_records_but_does_not_send(tmp_path, discord):
    scan = write_scan(tmp_path, [ihs_row()])
    tracker = tmp_path / "tp.json"
    assert run(scan, tracker, {"BTCUSDT": frame([(105, 99), (125, 110)])}, after(2)) == 0
    assert discord["post_calls"] == []
    it = tracker_items(tracker)[ihs_row()["pattern_id"]]
    assert it["status"] == "tp1" and it["events"][0]["notify"] is False
    assert it["events"][0]["silent_reason"] == "first_run"


def test_tp1_sent_once_then_tp2(tmp_path, discord):
    scan, tracker = seed(tmp_path, [ihs_row()])
    frames = {"BTCUSDT": frame([(105, 99), (125, 110)])}
    assert run(scan, tracker, frames, after(2)) == 0
    assert len(discord["post_calls"]) == 1
    call = discord["post_calls"][0]
    assert call["url"] == nd.BOT_MESSAGES_URL.format(channel_id="222")  # #加密止盈, text channel
    assert call["headers"]["Authorization"] == f"Bot {TOKEN}"
    emb = call["payload"]["embeds"][0]
    assert emb["title"] == "🎯 BTCUSDT 日線 TP1 達成"
    fields = {f["name"]: f["value"] for f in emb["fields"]}
    assert fields["型態"] == "頭肩底" and fields["從突破價算"] == "+20.0%" and fields["花費"].startswith("1 根 K 線")
    assert fields["TradingView"].startswith("[開啟圖表](https://www.tradingview.com/chart/?symbol=BINANCE%3ABTCUSDT")
    it = tracker_items(tracker)[ihs_row()["pattern_id"]]
    assert it["status"] == "tp1" and it["events"][0]["notified_at"]
    # same bars again: nothing new
    assert run(scan, tracker, frames, after(2)) == 0 and len(discord["post_calls"]) == 1
    # TP2 later: sent, tracking ends
    run(scan, tracker, {"BTCUSDT": frame([(105, 99), (125, 110), (141, 120)])}, after(3))
    assert len(discord["post_calls"]) == 2
    assert discord["post_calls"][1]["payload"]["embeds"][0]["title"] == "🎯 BTCUSDT 日線 TP2 達成"
    it = tracker_items(tracker)[ihs_row()["pattern_id"]]
    assert it["status"] == "tp2" and it["ended_at"] and [e["type"] for e in it["events"]] == ["tp1", "tp2"]
    # finished items are not fetched again
    fetch = fake_fetch({"BTCUSDT": frame([(150, 140)])})
    run(scan, tracker, fetch, after(4))
    assert fetch.calls == []


def test_missing_channel_keeps_state_and_retries(tmp_path, discord, capsys):
    scan, tracker = seed(tmp_path, [ihs_row()])
    frames = {"BTCUSDT": frame([(105, 99), (125, 110)])}
    discord["gets"].append(Resp(200, [{"id": "1", "name": "一般", "type": 0}]))
    assert run(scan, tracker, frames, after(2)) == 0
    out = capsys.readouterr().out
    assert "找不到文字頻道「加密止盈」" in out and "請在 Discord 伺服器建立文字頻道「加密止盈」" in out
    assert discord["post_calls"] == []
    it = tracker_items(tracker)[ihs_row()["pattern_id"]]
    assert it["status"] == "open" and it["events"] == []  # unchanged -> found again next run
    assert run(scan, tracker, frames, after(2)) == 0  # channel exists now
    assert len(discord["post_calls"]) == 1 and tracker_items(tracker)[ihs_row()["pattern_id"]]["status"] == "tp1"


def test_no_guild_and_no_token_only_hint(tmp_path, discord, capsys):
    scan, tracker = seed(tmp_path, [ihs_row()])
    assert run(scan, tracker, {"BTCUSDT": frame([(125, 99)])}, after(1), bot_token=None, guild_id=None) == 0
    assert discord["get_calls"] == [] and discord["post_calls"] == []
    assert "機器人未設定" in capsys.readouterr().out
    assert tracker_items(tracker)[ihs_row()["pattern_id"]]["status"] == "open"


def test_channel_override_skips_lookup(tmp_path, discord):
    scan, tracker = seed(tmp_path, [ihs_row()])
    run(scan, tracker, {"BTCUSDT": frame([(125, 99)])}, after(1), channel_override="555")
    assert discord["get_calls"] == []
    assert discord["post_calls"][0]["url"] == nd.BOT_MESSAGES_URL.format(channel_id="555")


def test_channel_name_match_ignores_case_hash_and_voice():
    assert ntp._norm(" #加密止盈 ") == ntp._norm("加密止盈")


def test_tw_market_split(tmp_path, discord):
    rows = [ihs_row("2330", exchange="twse", market="stock", name="台積電", tv_symbol="TWSE:2330",
                    pattern_id="twse|stock|2330|1d|LS|H|RS"),
            ihs_row("BTCUSDT")]  # a crypto row in a TW scan is never added to the TW tracker
    scan, tracker = seed(tmp_path, rows, market="tw")
    run(scan, tracker, {"2330": frame([(125, 99)]), "BTCUSDT": frame([(125, 99)])}, after(1))
    assert list(tracker_items(tracker)) == ["twse|stock|2330|1d|LS|H|RS"]
    call = discord["post_calls"][0]
    assert call["url"] == nd.BOT_MESSAGES_URL.format(channel_id="333")  # #台股止盈
    emb = call["payload"]["embeds"][0]
    assert emb["title"] == "🎯 台股 2330 台積電 日線 TP1 達成"
    fields = {f["name"]: f["value"] for f in emb["fields"]}
    assert fields["達成價"] == "NT$120.00" and fields["市場"] == "台股上市"


def test_triangle_breakout_up_notified(tmp_path, discord):
    scan, tracker = seed(tmp_path, [], tri_rows=[tri_row(), tri_row("SOLUSDT", status="breakout_down",
                                                                    pattern_id="okx|spot|SOLUSDT|1d|tri|S")])
    run(scan, tracker, {"ETHUSDT": frame([(51, 49), (56, 50)])}, after(2))
    assert list(tracker_items(tracker)) == ["okx|spot|ETHUSDT|1d|tri|S"]
    emb = discord["post_calls"][0]["payload"]["embeds"][0]
    assert emb["title"] == "🎯 ETHUSDT 日線 TP1 達成"
    fields = {f["name"]: f["value"] for f in emb["fields"]}
    assert fields["型態"] == "上升三角" and fields["從突破價算"] == "+10.0%"


def test_batches_of_ten_and_429(tmp_path, discord):
    rows = [ihs_row(f"C{i:02d}USDT", pattern_id=f"binance|spot|C{i:02d}USDT|1d|x") for i in range(12)]
    scan, tracker = seed(tmp_path, rows)
    discord["posts"].extend([Resp(429, {"retry_after": 0.01}), Resp(200)])
    waits = []
    rc = run(scan, tracker, {r["symbol"]: frame([(125, 99)]) for r in rows}, after(1), sleep=waits.append)
    assert rc == 0
    sizes = [len(c["payload"]["embeds"]) for c in discord["post_calls"]]
    assert sizes == [10, 10, 2]  # first try 429, retried, then the second message
    assert 0.01 in waits
    assert all(it["status"] == "tp1" for it in tracker_items(tracker).values())


def test_send_failure_leaves_unsent_items_for_retry(tmp_path, discord):
    rows = [ihs_row(f"C{i:02d}USDT", pattern_id=f"binance|spot|C{i:02d}USDT|1d|x") for i in range(12)]
    scan, tracker = seed(tmp_path, rows)
    discord["posts"].extend([Resp(200), Resp(403)])
    assert run(scan, tracker, {r["symbol"]: frame([(125, 99)]) for r in rows}, after(1)) == 1
    st = [it["status"] for it in tracker_items(tracker).values()]
    assert st.count("tp1") == 10 and st.count("open") == 2


def test_failed_not_notified_by_default(tmp_path, discord):
    scan, tracker = seed(tmp_path, [ihs_row()])
    run(scan, tracker, {"BTCUSDT": frame([(105, 79)])}, after(1))
    assert discord["post_calls"] == []
    it = tracker_items(tracker)[ihs_row()["pattern_id"]]
    assert it["status"] == "failed" and it["events"][0]["silent_reason"] == "not_notified"


def test_failed_notified_when_enabled(tmp_path, discord):
    scan, tracker = seed(tmp_path, [ihs_row()])
    run(scan, tracker, {"BTCUSDT": frame([(105, 79)])}, after(1), notify_failed=True)
    assert discord["post_calls"][0]["payload"]["embeds"][0]["title"] == "⚠️ BTCUSDT 日線 跌破失效價"


def test_expired_is_silent(tmp_path, discord):
    scan, tracker = seed(tmp_path, [ihs_row()])
    run(scan, tracker, {"BTCUSDT": frame([(105, 95)] * 6)}, after(6), config={"max_bars": {"1d": 5}})
    assert discord["post_calls"] == [] and tracker_items(tracker)[ihs_row()["pattern_id"]]["status"] == "expired"


def test_event_before_signal_time_only_recorded(tmp_path, discord):
    scan, tracker = seed(tmp_path, [ihs_row(signal_time=iso(T0 + 2 * DAY))])
    run(scan, tracker, {"BTCUSDT": frame([(125, 99), (110, 100), (112, 100)])}, after(3))
    assert discord["post_calls"] == []
    assert tracker_items(tracker)[ihs_row()["pattern_id"]]["events"][0]["silent_reason"] == "before_signal"


def test_fetch_failure_leaves_item_unchanged(tmp_path, discord):
    scan, tracker = seed(tmp_path, [ihs_row()])
    assert run(scan, tracker, {"BTCUSDT": RuntimeError("boom")}, after(1)) == 0
    it = tracker_items(tracker)[ihs_row()["pattern_id"]]
    assert it["status"] == "open" and it["last_checked_at"] is None


def test_dry_run_sends_nothing_writes_nothing(tmp_path, discord, capsys):
    scan, tracker = seed(tmp_path, [ihs_row()])
    before = tracker.read_text(encoding="utf-8")
    assert run(scan, tracker, {"BTCUSDT": frame([(125, 99)])}, after(1), dry_run=True,
               preview_dir=tmp_path / "prev") == 0
    assert discord["post_calls"] == [] and tracker.read_text(encoding="utf-8") == before
    out = capsys.readouterr().out
    assert "🎯 BTCUSDT 日線 TP1 達成" in out and "nothing sent, tracker not written" in out
    assert not (tmp_path / "tp_new.json").exists()


def test_secrets_never_printed(tmp_path, discord, capsys, monkeypatch):
    scan, tracker = seed(tmp_path, [ihs_row()])

    def boom(url, headers=None, timeout=None):
        raise requests.ConnectionError(f"failed {url} {headers}")

    monkeypatch.setattr(requests, "get", boom)
    monkeypatch.setattr(requests, "post", lambda *a, **k: (_ for _ in ()).throw(
        requests.ConnectionError(f"post failed {k.get('headers')}")))
    run(scan, tracker, {"BTCUSDT": frame([(125, 99)])}, after(1))
    run(scan, tracker, {"BTCUSDT": frame([(125, 99)])}, after(1), channel_override="555")
    out = capsys.readouterr()
    text = out.out + out.err
    assert TOKEN not in text and GUILD not in text and "555" not in text
    assert ntp.mask(f"x {TOKEN} y {GUILD}", TOKEN, GUILD) == "x *** y ***"


def test_main_reads_env_and_never_echoes(tmp_path, discord, capsys, monkeypatch):
    scan = write_scan(tmp_path, [])
    monkeypatch.setenv("DISCORD_BOT_TOKEN", TOKEN)
    monkeypatch.setenv("DISCORD_GUILD_ID", GUILD)
    assert ntp.main(["--scan", str(scan), "--tracker", str(tmp_path / "t.json"), "--dry-run"]) == 0
    out = capsys.readouterr()
    assert TOKEN not in out.out + out.err and GUILD not in out.out + out.err
    assert "找到頻道 #加密止盈" in out.out and not (tmp_path / "t.json").exists()


def test_chart_from_skeleton_when_bars_file_is_gone(tmp_path):
    bars = [[int((T0 + (i - 10) * DAY).timestamp()), 100, 101, 99, 100, 1] for i in range(10)]
    doc = {"pattern_id": "p", "bars": bars,
           "points": [{"label": n, "time": bars[i][0], "price": p}
                      for n, i, p in (("LS", 1, 90), ("N1", 3, 100), ("H", 5, 80), ("N2", 7, 100), ("RS", 8, 90))],
           "neckline": [{"time": bars[1][0], "price": 100}, {"time": bars[9][0], "price": 100}],
           "breakout": {"time": int(T0.timestamp()), "price": 104}, "levels": []}
    item = tt.ihs_item(ihs_row(pattern_id="p", chart_file="bars/gone.json"), chart=doc, now=T0)
    assert "bars" not in item["chart"]
    fetched = frame([(100, 99)] * 3, start=T0 - 12 * DAY) .pipe(lambda f: pd.concat(
        [f, frame([(105, 99), (125, 110)])], ignore_index=True))
    got = ntp.chart_doc(item, scan_dir=tmp_path, frame=fetched, now=after(2))
    assert got is not None and len(got["bars"]) == 5 and got["points"] == doc["points"]
    png = ntp.make_png(item, [{"type": "tp1", "time": iso(T0 + DAY), "price": 120.0}], scan_dir=tmp_path,
                       frame=fetched, now=after(2))
    assert png and png[:8] == b"\x89PNG\r\n\x1a\n"
    # bars that do not reach back to the chart start -> no picture
    assert ntp.chart_doc(item, scan_dir=tmp_path, frame=frame([(105, 99)]), now=after(1)) is None


def test_prune_finished_items():
    old = {"a": {"status": "tp2", "ended_at": iso(T0 - 100 * DAY)}, "b": {"status": "tp2", "ended_at": iso(T0)},
           "c": {"status": "open", "ended_at": None}}
    assert sorted(tt.prune(old, T0, 90)) == ["b", "c"]


# ------------------------------------------------------------------ publish keeps the trackers

def _write(root: Path, rel: str, text: str = "x"):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _tree(root: Path):
    return {p.relative_to(root).as_posix(): p.read_text(encoding="utf-8") for p in root.rglob("*") if p.is_file()}


def test_state_merge_keeps_both_trackers(tmp_path):
    prev, new, out = tmp_path / "prev", tmp_path / "new", tmp_path / "out"
    _write(prev, "state.json", "crypto state")
    _write(prev, "tp_tracker.json", "crypto tp old")
    _write(prev, "tp_tracker_tw.json", "tw tp")
    _write(new, "state.json", "crypto state 2")
    _write(new, "tp_tracker.json", "crypto tp new")
    _write(new, "tp_tracker_tw.json", "STALE tw copy")
    mp.merge_tree(new, prev, out, "crypto", mp.OWNED["state"], mp.KEEP_IF_ABSENT["state"]["crypto"])
    t = _tree(out)
    assert t["tp_tracker.json"] == "crypto tp new" and t["tp_tracker_tw.json"] == "tw tp"
    # this run has no tracker (step skipped / crashed): the published one is kept, not deleted
    new2, out2 = tmp_path / "new2", tmp_path / "out2"
    _write(new2, "state_tw.json", "tw state")
    mp.merge_tree(new2, out, out2, "tw", mp.OWNED["state"], mp.KEEP_IF_ABSENT["state"]["tw"])
    t2 = _tree(out2)
    assert t2["tp_tracker_tw.json"] == "tw tp" and t2["tp_tracker.json"] == "crypto tp new"


def test_site_merge_keeps_tw_excel(tmp_path):
    prev, new, out = tmp_path / "prev", tmp_path / "new", tmp_path / "out"
    _write(prev, "data/tw/rs_confirmed.xlsx", "tw xlsx")
    _write(new, "data/scan.json", "crypto")
    mp.merge_tree(new, prev, out, "crypto", mp.OWNED["site"])
    assert _tree(out)["data/tw/rs_confirmed.xlsx"] == "tw xlsx"
