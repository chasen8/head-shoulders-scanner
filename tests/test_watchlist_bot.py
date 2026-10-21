"""D34: right-shoulder table (bot select / webhook fallback) and watchlist change notifications. No network."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

import pytest
import requests

import notify_discord as nd
import notify_watchlist as nw
from ihs_scanner.table_image import MAX_ROWS_PER_IMAGE, render_table, table_cells

WEBHOOK = "https://discord.com/api/webhooks/123456/SECRET-HOOK-xyz"
BOT = "BOT-TOKEN-SECRET-abc.def"
SYNC = "SYNC-KEY-SECRET-123"
API = "https://watch.example-worker.workers.dev"
MAIN, WATCH = "1111", "2222"
NOW = datetime(2026, 10, 22, 8, 0, tzinfo=timezone.utc)
PNG = b"\x89PNG\r\n\x1a\n"


def row(sym, status="watchlist", score=80.0, tf="4h", exchange="binance", market="spot", **extra):
    r = {"symbol": sym, "timeframe": tf, "status": status, "pattern_score": score, "exchange": exchange,
         "market": market, "tv_symbol": f"BINANCE:{sym}", "distance_to_neckline": 0.0123,
         "neckline_side": "below", "current_close": 1.2, "current_neckline_price": 1.2345,
         "LS_time": "2026-08-01T00:00:00Z", "H_time": "2026-09-01T00:00:00Z", "RS_time": "2026-10-01T00:00:00Z",
         "breakout_detected": status == "confirmed", "tp1": 1.6, "tp2": 1.75, "invalidation_price": 0.8,
         "drop_left_pct": 0.24, "drop_right_pct": 0.22, "head_rise_pct": 0.45, "neckline_slope_pct_per_bar": -0.001}
    r.update(extra)
    return r


def tw_row(code, name, status="watchlist", score=78.0, **extra):
    return row(code, status, score, tf="1d", exchange="twse", market="stock", name=name, tv_symbol=f"TWSE:{code}",
               **extra)


def key_of(r):
    return nd.notify_key(r)


class Resp:
    def __init__(self, code=200, body=None):
        self.status_code, self._body, self.headers = code, body or {}, {}

    def json(self):
        return self._body


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in (nd.ENV_TEST_MESSAGE, nd.ENV_MAX_EVENTS, nd.ENV_MIN_SCORE, nd.ENV_WEBHOOK, nd.ENV_BOT_TOKEN,
                 nd.ENV_MAIN_CHANNEL, nw.ENV_WATCH_CHANNEL, nw.ENV_API_URL, nw.ENV_SYNC_KEY):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def net(monkeypatch):
    """Records every requests.post / requests.get; `queue` feeds responses, `items` is the GET watchlist."""
    class Net:
        posts, gets, queue = [], [], []
        items: list = []
        get_code = 200

    n = Net()
    n.posts, n.gets, n.queue, n.items = [], [], [], []

    def post(url, json=None, data=None, files=None, timeout=None, headers=None):
        payload = json if json is not None else __import__("json").loads(data["payload_json"])
        n.posts.append({"url": url, "payload": payload, "files": files, "headers": headers or {}})
        return n.queue.pop(0) if n.queue else Resp(200)

    def get(url, headers=None, timeout=None):
        n.gets.append({"url": url, "headers": headers or {}})
        return Resp(n.get_code, {"items": n.items})

    monkeypatch.setattr(requests, "post", post)
    monkeypatch.setattr(requests, "get", get)
    return n


def write_scan(tmp_path, rows, market=None):
    doc = {"results": rows}
    if market:
        doc["market"] = market
    p = tmp_path / "scan.json"
    p.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    st = tmp_path / "state.json"
    nd.save_state(st, {})
    return p, st


def go(scan, st, **kw):
    args = dict(min_score=50, statuses={"forming", "watchlist", "confirmed"}, notify_all=False, dry_run=False,
                webhook=WEBHOOK, now=NOW, sleep=lambda s: None, pause=0, image_for=lambda r: PNG,
                preview_dir=None)
    args.update(kw)
    return nd.run(scan, st, **args)


def tables(net):
    return [p for p in net.posts if str(p["payload"].get("content", "")).startswith("右肩確認")]


# ---------------------------------------------------------------- table: events

def test_watchlist_goes_into_table_not_one_by_one_forming_confirmed_stay(tmp_path, net):
    rows = [row("AAAUSDT", "forming"), row("BBBUSDT"), row("CCCUSDT", "confirmed"), row("DDDUSDT", score=90)]
    scan, st = write_scan(tmp_path, rows)
    assert go(scan, st, bot_token=BOT, main_channel=MAIN) == 0
    singles = [p for p in net.posts if p["url"] == WEBHOOK]
    assert sorted(e["title"] for p in singles for e in p["payload"]["embeds"]) == \
        ["AAAUSDT 4小時線 右肩形成中", "CCCUSDT 4小時線 已突破"]
    (t,) = tables(net)
    assert t["url"] == f"https://discord.com/api/v10/channels/{MAIN}/messages"
    assert t["payload"]["content"] == "右肩確認（加密貨幣）2026-10-22 08:00 共 2 筆"
    assert t["headers"]["Authorization"] == f"Bot {BOT}"
    assert len(t["files"]) == 1 and t["files"][0][1][2] == "image/png"
    opts = t["payload"]["components"][0]["components"][0]
    assert opts["type"] == 3 and opts["custom_id"] == "watch_select"
    assert [o["label"] for o in opts["options"]] == ["#1 DDDUSDT 4h 90分", "#2 BBBUSDT 4h 80分"]  # best first
    assert opts["options"][0]["value"] == key_of(row("DDDUSDT"))
    assert opts["min_values"] == 1 and opts["max_values"] == 2
    state = nd.load_state(st)
    assert state[key_of(row("BBBUSDT"))]["notified_status"] == "watchlist"


def test_no_watchlist_events_no_table(tmp_path, net):
    scan, st = write_scan(tmp_path, [row("AAAUSDT", "forming")])
    go(scan, st, bot_token=BOT, main_channel=MAIN)
    assert tables(net) == []


def test_tw_table_title_label_and_taipei_time(tmp_path, net):
    scan, st = write_scan(tmp_path, [tw_row("2330", "台積電", score=78), tw_row("2317", None, score=70)], "tw")
    go(scan, st, bot_token=BOT, main_channel=MAIN)
    (t,) = tables(net)
    assert t["payload"]["content"] == "右肩確認（台股）2026-10-22 16:00 共 2 筆"  # UTC 08:00 = Taipei 16:00
    labels = [o["label"] for o in t["payload"]["components"][0]["components"][0]["options"]]
    assert labels == ["#1 2330 台積電 1d 78分", "#2 2317 1d 70分"]


def test_no_bot_falls_back_to_webhook_without_select(tmp_path, net, capsys):
    scan, st = write_scan(tmp_path, [row("BBBUSDT")])
    go(scan, st)
    (t,) = tables(net)
    assert t["url"] == WEBHOOK and "components" not in t["payload"] and len(t["files"]) == 1
    assert "Authorization" not in t["headers"]
    assert "機器人未設定" in capsys.readouterr().out
    assert nd.load_state(st)[key_of(row("BBBUSDT"))]["notified_status"] == "watchlist"


def test_bot_set_but_no_webhook_still_sends_table(tmp_path, net):
    scan, st = write_scan(tmp_path, [row("AAAUSDT", "forming"), row("BBBUSDT")])
    assert go(scan, st, webhook=None, bot_token=BOT, main_channel=MAIN) == 0
    assert len(net.posts) == 1 and len(tables(net)) == 1
    assert nd.load_state(st)[key_of(row("AAAUSDT", "forming"))]["notified_status"] == "forming"  # D20


def test_nothing_configured_records_state(tmp_path, net):
    scan, st = write_scan(tmp_path, [row("BBBUSDT")])
    assert go(scan, st, webhook=None) == 0
    assert net.posts == []
    assert nd.load_state(st)[key_of(row("BBBUSDT"))]["notified_status"] == "watchlist"


def test_failed_table_is_retried_even_for_forming_to_watchlist(tmp_path, net):
    scan, st = write_scan(tmp_path, [row("BBBUSDT", "forming")])
    go(scan, st, bot_token=BOT, main_channel=MAIN)  # notified as forming
    saved = nd.load_state(st)
    scan, _ = write_scan(tmp_path, [row("BBBUSDT")])
    nd.save_state(st, saved)
    net.queue.extend([Resp(500)] * 10)
    assert go(scan, st, bot_token=BOT, main_channel=MAIN) == 1
    assert nd.load_state(st)[key_of(row("BBBUSDT"))]["status"] == "forming"  # kept so the table is retried
    net.queue.clear()
    net.posts.clear()
    assert go(scan, st, bot_token=BOT, main_channel=MAIN) == 0
    assert len(tables(net)) == 1


# ---------------------------------------------------------------- table: select limits

def many(n, prefix="S"):
    return [row(f"{prefix}{i:03d}USDT", score=99 - i * 0.05) for i in range(n)]


def test_60_rows_three_selects_three_pictures(tmp_path, net):
    scan, st = write_scan(tmp_path, many(60))
    go(scan, st, bot_token=BOT, main_channel=MAIN)
    (t,) = tables(net)
    comps = [r["components"][0] for r in t["payload"]["components"]]
    assert [len(c["options"]) for c in comps] == [25, 25, 10]
    assert [c["max_values"] for c in comps] == [25, 25, 10]
    assert len({c["custom_id"] for c in comps}) == 3 and comps[0]["custom_id"] == "watch_select"
    assert all(c["custom_id"].startswith("watch_select") for c in comps)
    assert len(t["files"]) == 3 and "共 60 筆" in t["payload"]["content"]
    assert [o["label"].split()[0] for c in comps for o in c["options"]] == [f"#{i}" for i in range(1, 61)]


def test_130_rows_split_into_two_messages(tmp_path, net):
    scan, st = write_scan(tmp_path, many(130))
    go(scan, st, bot_token=BOT, main_channel=MAIN)
    ts = tables(net)
    assert len(ts) == 2
    assert [len(r["components"]) for r in (ts[0]["payload"], ts[1]["payload"])] == [5, 1]
    assert ts[0]["payload"]["content"].endswith("共 130 筆（1/2）") and ts[1]["payload"]["content"].endswith("（2/2）")
    assert sum(len(c["components"][0]["options"]) for t in ts for c in t["payload"]["components"]) == 130
    assert all(len(c["components"][0]["options"]) <= 25 for t in ts for c in t["payload"]["components"])
    assert len(nd.load_state(st)) == 130


def test_option_label_and_value_limits(tmp_path, net, capsys):
    long_sym = "L" * 30
    rows = [row("OKUSDT"), row(long_sym + "USDT"), row("XUSDT")]
    rows[2]["LS_time"] = "2026-08-01T00:00:00Z" + "x" * 120  # pattern_key > 100 chars -> option skipped, logged
    scan, st = write_scan(tmp_path, rows)
    go(scan, st, bot_token=BOT, main_channel=MAIN)
    (t,) = tables(net)
    options = t["payload"]["components"][0]["components"][0]["options"]
    assert len(options) == 2 and all(len(o["label"]) <= 100 and len(o["value"]) <= 100 for o in options)
    assert "select option skipped" in capsys.readouterr().err
    assert t["payload"]["components"][0]["components"][0]["max_values"] == 2
    assert len(nd.load_state(st)) == 3  # still recorded as notified (the picture lists it)


# ---------------------------------------------------------------- table picture

def test_table_image_png_and_row_limit():
    items = [(i + 1, row(f"S{i}USDT", score=90 - i)) for i in range(MAX_ROWS_PER_IMAGE)]
    png = render_table(items, "test")
    assert png.startswith(PNG) and len(png) > 5000
    with pytest.raises(ValueError):
        render_table([], "empty")
    cells = table_cells(3, row("STARUSDT", score=83.4), tw=False, with_name=False)
    assert cells[:4] == ["3", "STARUSDT", "4h", "83.4"] and cells[5] == "-1.2%"  # below the neckline
    tw = table_cells(1, tw_row("2330", "台積電"), tw=True, with_name=True)
    assert tw[1] == "2330 台積電"
    assert table_cells(1, tw_row("2330", "台積電"), tw=True, with_name=False)[1] == "2330"


def test_table_picture_failure_falls_back_to_text(tmp_path, net, monkeypatch):
    import ihs_scanner.table_image as ti
    monkeypatch.setattr(ti, "render_table", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    scan, st = write_scan(tmp_path, [row("BBBUSDT")])
    assert go(scan, st, bot_token=BOT, main_channel=MAIN) == 0
    (t,) = tables(net)
    assert t["files"] is None and "BBBUSDT" in t["payload"]["content"]


# ---------------------------------------------------------------- secrets / dry-run

def test_secrets_never_printed(tmp_path, monkeypatch, capsys):
    def boom(url, **kw):
        raise requests.ConnectionError(f"cannot reach {url} with {kw.get('headers')}")

    monkeypatch.setattr(requests, "post", boom)
    scan, st = write_scan(tmp_path, [row("AAAUSDT", "forming"), row("BBBUSDT")])
    assert go(scan, st, bot_token=BOT, main_channel=MAIN, sleep=lambda s: None) == 1
    cap = capsys.readouterr()
    for secret in (WEBHOOK, "SECRET-HOOK", BOT, "BOT-TOKEN"):
        assert secret not in cap.out + cap.err
    assert secret not in st.read_text(encoding="utf-8")


def test_dry_run_sends_nothing_saves_table_preview(tmp_path, net, capsys):
    scan, st = write_scan(tmp_path, [row("BBBUSDT")])
    prev = tmp_path / "prev"
    assert go(scan, st, dry_run=True, bot_token=BOT, main_channel=MAIN, preview_dir=prev) == 0
    assert net.posts == [] and nd.load_state(st) == {}
    assert (prev / "watch_table_crypto_1.png").read_bytes().startswith(PNG)
    out = capsys.readouterr().out
    assert "右肩確認（加密貨幣）" in out and "select(s)" in out
    assert BOT not in out


def test_main_reads_bot_env(tmp_path, net, monkeypatch):
    scan, st = write_scan(tmp_path, [row("BBBUSDT")])
    monkeypatch.setenv(nd.ENV_BOT_TOKEN, BOT)
    monkeypatch.setenv(nd.ENV_MAIN_CHANNEL, MAIN)
    assert nd.main(["--scan", str(scan), "--state", str(st), "--min-score", "10"]) == 0
    assert net.posts[0]["url"].endswith(f"/channels/{MAIN}/messages")


# ---------------------------------------------------------------- watchlist notifications

def item(r, last_status, name=None, **extra):
    d = {"pattern_key": key_of(r), "market": "tw" if nd.is_tw_row(r) else "crypto", "symbol": r["symbol"],
         "name": name, "timeframe": r["timeframe"], "last_status": last_status, "last_notified_at": None}
    d.update(extra)
    return d


def run_watch(tmp_path, rows, items, net, *, market=None, **kw):
    scan, _ = write_scan(tmp_path, rows, market)
    net.items = items
    args = dict(api_url=API, sync_key=SYNC, bot_token=BOT, watch_channel=WATCH, now=NOW, sleep=lambda s: None,
                pause=0, image_for=lambda r: PNG)
    args.update(kw)
    return nw.run(scan, **args)


def updates_of(net):
    posts = [p for p in net.posts if p["url"].endswith("/api/watchlist")]
    return posts[-1]["payload"]["update"] if posts else []


def discord_posts(net):
    return [p for p in net.posts if "/channels/" in p["url"]]


@pytest.mark.parametrize("before,after,arrow", [
    ("forming", "watchlist", "右肩形成中 → 右肩確認"),
    ("watchlist", "confirmed", "右肩確認 → 已突破"),
    ("forming", "stale", "右肩形成中 → 已過期"),
    ("confirmed", "stale", "已突破 → 已過期")])
def test_transitions_notify_and_update(tmp_path, net, before, after, arrow):
    r = row("STARUSDT", after, score=83)
    assert run_watch(tmp_path, [r], [item(r, before)], net) == 0
    (p,) = discord_posts(net)
    assert p["url"] == f"https://discord.com/api/v10/channels/{WATCH}/messages"
    emb = p["payload"]["embeds"][0]
    assert emb["title"] == f"⭐ STARUSDT 4h {arrow}"
    assert {f["name"] for f in emb["fields"]} >= {"目前價", "頸線價", "TP1", "失效價（頭部低點）"}
    assert p["headers"]["Authorization"] == f"Bot {BOT}" and len(p["files"]) == 1
    (u,) = updates_of(net)
    assert u["pattern_key"] == key_of(r) and u["last_status"] == after and u["last_notified_at"] == "2026-10-22T08:00:00Z"
    api_post = [x for x in net.posts if x["url"].endswith("/api/watchlist")][0]
    assert api_post["headers"] == {"Authorization": f"Bearer {SYNC}"}
    assert net.gets[0]["headers"] == {"Authorization": f"Bearer {SYNC}"}


def test_same_status_nothing(tmp_path, net):
    r = row("STARUSDT", "watchlist")
    run_watch(tmp_path, [r], [item(r, "watchlist")], net)
    assert net.posts == []


def test_gone_notified_once(tmp_path, net):
    r = row("STARUSDT", "watchlist")
    other = row("OTHERUSDT", "forming")
    run_watch(tmp_path, [other], [item(r, "watchlist")], net)
    (p,) = discord_posts(net)
    emb = p["payload"]["embeds"][0]
    assert emb["title"] == "⭐ STARUSDT 4h 右肩確認 → 型態消失" and "作廢或過期" in emb["description"]
    assert p["files"] is None
    assert updates_of(net)[0]["last_status"] == "gone"
    net.posts.clear()
    run_watch(tmp_path, [other], [item(r, "gone")], net)  # next scan: already gone, no repeat
    assert net.posts == []


def test_first_sight_only_recorded(tmp_path, net):
    r = row("STARUSDT", "watchlist")
    run_watch(tmp_path, [r], [item(r, None)], net)
    assert discord_posts(net) == []
    (u,) = updates_of(net)
    assert u["last_status"] == "watchlist" and u["last_notified_at"] is None


def test_market_filter(tmp_path, net):
    c = row("STARUSDT", "confirmed")
    t = tw_row("2330", "台積電", "confirmed")
    # crypto scan: the TW item is ignored even though its status "changed"
    run_watch(tmp_path, [c], [item(c, "watchlist"), item(t, "watchlist")], net)
    assert len(discord_posts(net)) == 1 and [u["pattern_key"] for u in updates_of(net)] == [key_of(c)]
    net.posts.clear()
    # tw scan: only the TW item
    run_watch(tmp_path, [t], [item(c, "watchlist"), item(t, "watchlist")], net, market="tw")
    (p,) = discord_posts(net)
    assert p["payload"]["embeds"][0]["title"] == "⭐ 台股 2330 台積電 1d 右肩確認 → 已突破"
    assert [u["pattern_key"] for u in updates_of(net)] == [key_of(t)]


def test_tw_name_filled_in(tmp_path, net):
    t = tw_row("2330", "台積電", "confirmed")
    run_watch(tmp_path, [t], [item(t, "watchlist", name=None)], net, market="tw")
    assert updates_of(net)[0]["name"] == "台積電"
    net.posts.clear()
    t2 = tw_row("2317", "鴻海", "watchlist")  # no change but name missing -> silent update
    run_watch(tmp_path, [t2], [item(t2, "watchlist", name=None)], net, market="tw")
    assert discord_posts(net) == [] and updates_of(net) == [{"pattern_key": key_of(t2), "name": "鴻海"}]


def test_unwatch_button_custom_id_and_sha1():
    short = "binance|spot|STARUSDT|4h|2026-09-01T00:00:00Z|2026-09-20T08:00:00Z"
    assert nw.unwatch_custom_id(short) == "unwatch:" + short
    long_key = "binance|spot|" + "X" * 100
    cid = nw.unwatch_custom_id(long_key)
    assert cid == "unwatch:" + hashlib.sha1(long_key.encode("utf-8")).hexdigest()[:16] and len(cid) <= 100
    edge = "a" * (100 - len("unwatch:"))
    assert nw.unwatch_custom_id(edge) == "unwatch:" + edge  # exactly 100 chars stays
    r = row("STARUSDT", "confirmed")
    msg = nw.build_change_message({"item": item(r, "watchlist"), "row": r, "previous": "watchlist",
                                   "current": "confirmed", "name_fix": {}}, None, lambda r_: None)
    btn = msg.payload["components"][0]["components"][0]
    assert btn["type"] == 2 and btn["label"] == "取消關注" and btn["custom_id"] == "unwatch:" + key_of(r)
    assert msg.files == [] and "image" not in msg.payload["embeds"][0]  # no bars -> no picture


def test_no_bot_config_skips_notification_and_does_not_record(tmp_path, net, capsys):
    r = row("STARUSDT", "confirmed")
    new = row("NEWUSDT", "watchlist")
    run_watch(tmp_path, [r, new], [item(r, "watchlist"), item(new, None)], net, bot_token=None)
    assert discord_posts(net) == []
    assert [u["pattern_key"] for u in updates_of(net)] == [key_of(new)]  # only the first-sight record
    assert "略過關注通知" in capsys.readouterr().out


def test_no_api_config_skips(tmp_path, net, capsys):
    r = row("STARUSDT", "confirmed")
    assert run_watch(tmp_path, [r], [item(r, "watchlist")], net, api_url=None) == 0
    assert net.posts == [] and net.gets == []
    assert "略過關注通知" in capsys.readouterr().out


def test_api_failures_do_not_fail(tmp_path, net, capsys, monkeypatch):
    r = row("STARUSDT", "confirmed")
    net.get_code = 500
    assert run_watch(tmp_path, [r], [item(r, "watchlist")], net) == 0
    assert net.posts == []
    net.get_code = 200
    net.queue.extend([Resp(204), Resp(500)])  # Discord ok, POST /api/watchlist fails
    assert run_watch(tmp_path, [r], [item(r, "watchlist")], net) == 0
    cap = capsys.readouterr()
    assert "警告" in cap.err
    for secret in (SYNC, BOT, API, "workers.dev"):
        assert secret not in cap.out + cap.err


def test_api_network_error_hides_url_and_key(tmp_path, monkeypatch, capsys):
    def boom(url, **kw):
        raise requests.ConnectionError(f"cannot reach {url} {kw.get('headers')}")

    monkeypatch.setattr(requests, "get", boom)
    scan, _ = write_scan(tmp_path, [row("STARUSDT", "confirmed")])
    assert nw.run(scan, api_url=API, sync_key=SYNC, bot_token=BOT, watch_channel=WATCH) == 0
    cap = capsys.readouterr()
    for secret in (SYNC, BOT, "workers.dev"):
        assert secret not in cap.out + cap.err


def test_discord_failure_keeps_change_unrecorded(tmp_path, net):
    r = row("STARUSDT", "confirmed")
    net.queue.extend([Resp(403), Resp(403)])  # with picture, then the retry without it
    assert run_watch(tmp_path, [r], [item(r, "watchlist")], net) == 0
    assert updates_of(net) == []  # retried next scan


def test_dry_run_no_post_saves_preview(tmp_path, net, capsys):
    r = row("STARUSDT", "confirmed")
    prev = tmp_path / "prev"
    assert run_watch(tmp_path, [r], [item(r, "watchlist")], net, dry_run=True, preview_dir=prev) == 0
    assert net.posts == []  # neither Discord nor POST /api/watchlist
    assert len(net.gets) == 1
    assert list(prev.glob("watch_*.png")) and "STARUSDT" in capsys.readouterr().out


def test_main_env_and_masking(tmp_path, net, monkeypatch, capsys):
    r = row("STARUSDT", "confirmed")
    scan, _ = write_scan(tmp_path, [r])
    net.items = [item(r, "watchlist")]
    monkeypatch.setenv(nw.ENV_API_URL, API + "/")
    monkeypatch.setenv(nw.ENV_SYNC_KEY, SYNC)
    monkeypatch.setenv(nd.ENV_BOT_TOKEN, BOT)
    monkeypatch.setenv(nw.ENV_WATCH_CHANNEL, WATCH)
    assert nw.main(["--scan", str(scan)]) == 0
    assert net.gets[0]["url"] == API + "/api/watchlist"
    cap = capsys.readouterr()
    for secret in (SYNC, BOT, API):
        assert secret not in cap.out + cap.err
    monkeypatch.delenv(nw.ENV_API_URL)
    assert nw.main(["--scan", str(scan)]) == 0
    assert nw.main(["--scan", str(tmp_path / "missing.json")]) == 0


def test_empty_scan_does_not_mark_everything_gone(tmp_path, net):
    r = row("STARUSDT", "watchlist")
    run_watch(tmp_path, [], [item(r, "watchlist")], net)
    assert net.posts == []
