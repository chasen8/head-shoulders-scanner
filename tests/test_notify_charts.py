"""D32: chart attachments, multipart upload, unlimited events, test message. No network."""
from __future__ import annotations

import json
import math

import pytest
import requests

import notify_discord as nd
from tests.test_notify import URL, Resp, embeds, entry, go, key, post, row, setup  # noqa: F401 (post = fixture)


def bars_doc(n=80, prov=False, breakout=True):
    t0, step = 1_760_000_000, 86400
    bars = []
    for i in range(n):
        base = 10 + 3 * math.sin(i / 6.0)
        bars.append([t0 + i * step, base, base + 1, base - 1, base + (0.4 if i % 2 else -0.4), 1000 + i])
    idx = [8, 20, 34, 52, n - 1]
    prices = [9.0, 12.0, 7.0, 12.2, 9.5]
    pts = [{"label": lb, "time": bars[i][0], "price": p} for lb, i, p in zip(["LS", "N1", "H", "N2", "RS"], idx, prices)]
    if prov:
        pts[-1]["provisional"] = True
    return {"bars": bars, "points": pts,
            "neckline": [{"time": bars[20][0], "price": 12.0}, {"time": bars[n - 1][0] + 5 * step, "price": 12.3}],
            "breakout": {"time": bars[n - 3][0], "price": 12.5} if breakout else None,
            "levels": [{"key": "tp1", "label": "TP1", "price": 14.0}, {"key": "tp2", "label": "TP2", "price": 16.0},
                       {"key": "invalidation_price", "label": "x", "price": 7.0}],
            "levels_estimated": not breakout, "rs_provisional": prov}


def write_chart(tmp_path, r, doc):
    d = tmp_path / "bars"
    d.mkdir(exist_ok=True)
    name = f"{r['symbol']}.json"
    (d / name).write_text(json.dumps(doc), encoding="utf-8")
    r["chart_file"] = f"bars/{name}"


def png_size(data):
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")


@pytest.mark.parametrize("prov,breakout", [(True, False), (False, True)])
def test_chart_png_size_and_dimensions(prov, breakout):
    from ihs_scanner.chart_image import render_chart
    png = render_chart(row("STARUSDT", "forming", tf="4h", score=83.0), bars_doc(prov=prov, breakout=breakout))
    assert png_size(png) == (1200, 675)
    assert len(png) < 300_000


def test_chart_tiny_price_and_stock():
    from ihs_scanner.chart_image import render_chart
    d = bars_doc()
    scale = 0.0001750 / 10
    d["bars"] = [[b[0]] + [v * scale for v in b[1:5]] + [b[5]] for b in d["bars"]]
    for p in d["points"] + d["neckline"] + d["levels"] + [d["breakout"]]:
        p["price"] *= scale
    assert png_size(render_chart(row("TINYUSDT"), d)) == (1200, 675)
    assert png_size(render_chart(row("2330", exchange="twse", market="stock"), bars_doc())) == (1200, 675)


def test_chart_rejects_empty_bars():
    from ihs_scanner.chart_image import render_chart
    with pytest.raises(ValueError):
        render_chart(row("AAAUSDT"), {"bars": [], "points": []})


def test_multipart_structure_and_attachment_names(tmp_path, post):
    rows = []
    for i in range(3):
        r = row(f"S{i}USDT", "forming", score=90 - i)
        write_chart(tmp_path, r, bars_doc(prov=True, breakout=False))
        rows.append(r)
    scan, st = setup(tmp_path, rows, state={})
    assert go(scan, st) == 0
    assert len(post.calls) == 1
    raw = post.raw[0]
    assert raw["json"] is None and set(raw["data"]) == {"payload_json"}
    files = raw["files"]
    assert [f[0] for f in files] == ["files[0]", "files[1]", "files[2]"]
    names = [f[1][0] for f in files]
    assert all(n.isascii() and n.endswith(".png") for n in names)
    assert names[0].startswith("s0usdt_1d_")
    assert [e["image"]["url"] for e in post.calls[0]["embeds"]] == [f"attachment://{n}" for n in names]
    assert all(f[1][2] == "image/png" and png_size(f[1][1]) == (1200, 675) for f in files)
    assert sum(len(f[1][1]) for f in files) < nd.MAX_ATTACH_BYTES


def test_missing_bars_sends_without_image(tmp_path, post, capsys):
    good, bad = row("GOODUSDT", "forming", score=90), row("BADUSDT", "forming", score=80, chart_file="bars/nope.json")
    write_chart(tmp_path, good, bars_doc())
    scan, st = setup(tmp_path, [good, bad], state={})
    assert go(scan, st) == 0
    es = post.calls[0]["embeds"]
    assert [e["title"] for e in es] == ["GOODUSDT 日線 右肩形成中", "BADUSDT 日線 右肩形成中"]
    assert "image" in es[0] and "image" not in es[1]
    assert len(post.raw[0]["files"]) == 1
    assert "no chart for BADUSDT" in capsys.readouterr().err
    assert set(nd.load_state(st)) == {key("GOODUSDT"), key("BADUSDT")}


def test_no_bars_at_all_uses_plain_json(tmp_path, post):
    scan, st = setup(tmp_path, [row("AAAUSDT", "forming")], state={})
    go(scan, st)
    assert post.raw[0]["json"] is not None and post.raw[0]["files"] is None


def test_more_than_ten_events_split_in_messages_no_cap(tmp_path, post):
    rows = [row(f"S{i:02d}USDT", "forming", score=99 - i * 0.1) for i in range(35)]
    scan, st = setup(tmp_path, rows, state={})
    waits = []
    assert go(scan, st, pause=1.0, sleep=waits.append) == 0
    assert [len(m["embeds"]) for m in post.calls] == [10, 10, 10, 5]
    assert waits == [1.0, 1.0, 1.0]  # >= 1 s between messages
    assert "未列出" not in json.dumps(post.calls, ensure_ascii=False)
    assert all(v["notified_status"] == "forming" for v in nd.load_state(st).values())


def test_safety_cap_leaves_rest_unnotified(tmp_path, post):
    rows = [row(f"S{i:02d}USDT", "forming", score=99 - i) for i in range(12)]
    scan, st = setup(tmp_path, rows, state={})
    assert go(scan, st, max_events=5) == 0
    assert len(embeds(post)) == 5
    assert sum(v["notified_status"] == "forming" for v in nd.load_state(st).values()) == 5
    post.calls.clear()
    go(scan, st, max_events=5)
    assert len(embeds(post)) == 5  # the next run continues with the rest


def test_second_message_failure_keeps_unsent_unnotified(tmp_path, post):
    rows = [row(f"S{i:02d}USDT", "forming", score=99 - i * 0.1) for i in range(15)]
    scan, st = setup(tmp_path, rows, state={})
    post.queue.extend([Resp(204), Resp(404)])
    assert go(scan, st) == 1
    assert sum(v["notified_status"] == "forming" for v in nd.load_state(st).values()) == 10


def test_multipart_429_retry_and_resend_files(tmp_path, post):
    r = row("AAAUSDT")
    write_chart(tmp_path, r, bars_doc())
    post.queue.extend([Resp(429, {"retry_after": 1.5}), Resp(200)])
    waits = []
    scan, st = setup(tmp_path, [r], state={})
    assert go(scan, st, sleep=waits.append) == 0
    assert waits[0] == 1.5 and len(post.raw) == 2
    assert post.raw[0]["files"][0][1][0] == post.raw[1]["files"][0][1][0]


def test_rejected_attachment_falls_back_to_plain_embeds(tmp_path, post):
    r = row("AAAUSDT", "forming")
    write_chart(tmp_path, r, bars_doc())
    post.queue.extend([Resp(400), Resp(204)])
    scan, st = setup(tmp_path, [r], state={})
    assert go(scan, st) == 0
    assert post.raw[1]["files"] is None and "image" not in post.calls[1]["embeds"][0]
    assert nd.load_state(st)[key("AAAUSDT")]["notified_status"] == "forming"


def test_multipart_error_never_contains_url(tmp_path, monkeypatch, capsys):
    def boom(url, **kw):
        raise requests.ConnectionError(f"cannot reach {url}")

    monkeypatch.setattr(nd.requests, "post", boom)
    r = row("AAAUSDT")
    write_chart(tmp_path, r, bars_doc())
    scan, st = setup(tmp_path, [r], state={})
    assert go(scan, st) == 1
    cap = capsys.readouterr()
    assert "SECRET-TOKEN" not in cap.out + cap.err and "discord.com" not in cap.out + cap.err


def test_dry_run_saves_preview_images(tmp_path, post, capsys):
    r = row("AAAUSDT", "forming")
    write_chart(tmp_path, r, bars_doc(prov=True, breakout=False))
    scan, st = setup(tmp_path, [r], state={})
    prev = tmp_path / "prev"
    go(scan, st, dry_run=True, preview_dir=prev)
    out = capsys.readouterr().out
    pngs = list(prev.glob("*.png"))
    assert len(pngs) == 1 and pngs[0].name in out and "AAAUSDT 日線 右肩形成中" in out
    assert post.calls == [] and nd.load_state(st) == {}
    assert "discord.com" not in out and "SECRET" not in out


def test_test_message_mode_leaves_state_alone(tmp_path, post):
    hi, lo = row("HIGHUSDT", "forming", score=91), row("LOWUSDT", score=60)
    write_chart(tmp_path, hi, bars_doc(prov=True, breakout=False))
    scan, st = setup(tmp_path, [lo, hi], state={key("OLDUSDT"): entry("watchlist", "watchlist")})
    before = st.read_text(encoding="utf-8")
    assert go(scan, st, test_message=True) == 0
    assert len(post.calls) == 1 and len(post.calls[0]["embeds"]) == 1
    assert post.calls[0]["embeds"][0]["title"] == "HIGHUSDT 日線 右肩形成中"
    assert "測試" in post.calls[0]["content"] and len(post.raw[0]["files"]) == 1
    assert st.read_text(encoding="utf-8") == before


def test_test_message_without_state_file_writes_none(tmp_path, post):
    scan, st = setup(tmp_path, [row("AAAUSDT")])
    assert go(scan, st, test_message=True) == 0
    assert not st.exists()


def test_test_message_env_and_main(tmp_path, monkeypatch, post):
    scan, st = setup(tmp_path, [row("AAAUSDT")])
    monkeypatch.setenv(nd.ENV_WEBHOOK, URL)
    monkeypatch.setenv(nd.ENV_TEST_MESSAGE, "true")
    assert nd.main(["--scan", str(scan), "--state", str(st)]) == 0
    assert len(post.calls) == 1 and not st.exists()


def test_attachment_name_ascii():
    n = nd.attachment_name(row("1000SATS-USDT"), "k|v")
    assert n.isascii() and n.endswith(".png") and "/" not in n
    assert nd.attachment_name(row("2330", exchange="twse"), "a").startswith("2330_1d_")
