"""Compare this scan with the previous state and send Discord notifications for new / broken-out patterns.

The webhook URL is read ONLY from the DISCORD_WEBHOOK_URL environment variable and is never printed,
logged or written to any file (not even inside error messages).

Dedup key (stable while a right shoulder is still forming, whose RS moves with every new low):
    exchange|market|symbol|timeframe|LS_time|H_time        (pattern_id also contains RS_time -> not used)

State file (state.json), version 2:
    {"version": 2, "patterns": {key: {status, score, last_seen, last_notified_at, notified_status}}}
    status = last seen status; notified_status = status of the last notification sent for this key (null = never).

Events (only when pattern_score >= --min-score and the status is in --statuses):
  * forming   (右肩形成中) and never notified before                       -> "new"
  * watchlist (右肩確認) that just became watchlist (new, or forming -> watchlist) -> "table" (D34, see below)
  * confirmed (已突破) and no confirmed notification yet for this key       -> "new" (unseen) / "upgraded"
First run (no state file, or a state older than version 2): one summary message only, every current pattern
becomes the baseline (no per-pattern spam, D20); --notify-all overrides. A message that failed to send leaves
its patterns un-notified, so the next run retries them.

Taiwan stocks (D29): the TW scan (site/data/tw/scan.json, `"market": "tw"`) uses its own state file
(state_tw.json on the scan-state branch) and the same webhook; titles read "台股 2330 台積電 日線 右肩形成中",
prices are shown as NT$ with two decimals, and the first run is again a single summary.

Charts (D32): every event embed carries a PNG of the detected pattern (ihs_scanner/chart_image.py, drawn from
the bars file named by the row's `chart_file`, next to scan.json), uploaded as multipart/form-data
(payload_json + files[n], embed.image.url = attachment://<ascii name>). A chart that cannot be made (bars file
missing, render error) only drops the picture, never the notification. Every event is listed, <= 10 embeds per
message, as many messages as needed (no "N more not listed" summary); NOTIFY_MAX_EVENTS (default 200, 0 = no limit)
is only a safety net: events beyond it are not sent AND not recorded, so the next run picks them up.
--test-message sends one message with the best-scoring pattern and touches no state.

Right-shoulder table (D34): "table" events are NOT sent one by one. Each scan sends ONE table message (per 125 rows):
a PNG table (ihs_scanner/table_image.py, <= 25 rows per picture) plus, when DISCORD_BOT_TOKEN and
DISCORD_MAIN_CHANNEL_ID are set, string selects under it (custom_id "watch_select", <= 25 options each, <= 5 per
message) that put the picked patterns on the watchlist (Cloudflare Worker, bot/). Without the bot the same picture goes
out through the webhook (no select). forming / confirmed events stay one embed each with a chart (webhook).
Watchlist change notifications live in notify_watchlist.py.

Example:
    python notify_discord.py --dry-run
    python notify_discord.py --scan site/data/tw/scan.json --state state_tw.json --dry-run
    DISCORD_WEBHOOK_URL=... python notify_discord.py --min-score 60
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import quote

import requests

from ihs_scanner.keys import pattern_key

ROOT = Path(__file__).resolve().parent
DEFAULT_SCAN = ROOT / "site" / "data" / "scan.json"
DEFAULT_STATE = ROOT / "state.json"
ENV_WEBHOOK = "DISCORD_WEBHOOK_URL"
ENV_MIN_SCORE = "NOTIFY_MIN_SCORE"
ENV_MAX_EVENTS = "NOTIFY_MAX_EVENTS"
ENV_TEST_MESSAGE = "TEST_MESSAGE"
ENV_BOT_TOKEN = "DISCORD_BOT_TOKEN"  # D34
ENV_MAIN_CHANNEL = "DISCORD_MAIN_CHANNEL_ID"
BOT_MESSAGES_URL = "https://discord.com/api/v10/channels/{channel_id}/messages"
BOT_USER_AGENT = "DiscordBot (https://github.com/chasen8/head-shoulders-scanner, 1.0)"
SELECT_ID = "watch_select"
SELECT_MAX_OPTIONS = 25
SELECTS_PER_MESSAGE = 5
TABLE_ROWS_PER_IMAGE = 25  # = SELECT_MAX_OPTIONS: one picture, one select
TABLE_ROWS_PER_MESSAGE = SELECT_MAX_OPTIONS * SELECTS_PER_MESSAGE  # 125
DEFAULT_MIN_SCORE = 50.0  # template forming patterns score ~50-70 (the user's five examples: 53-71)
DEFAULT_STATUSES = "forming,watchlist,confirmed"
STATE_VERSION = 2
MAX_EMBEDS_PER_MESSAGE = 10
DEFAULT_MAX_EVENTS = 200  # safety net only (D32); 0 = unlimited
MAX_ATTACH_BYTES = 7_500_000  # per message; Discord's limit is 8 MB (webhook default)
DEFAULT_PREVIEW_DIR = ROOT / "output" / "notify-preview"
TEST_TEXT = "測試：圖片通知（只送最高分 1 筆，不影響通知狀態）"
PRUNE_DAYS = 14
MAX_RETRIES = 5
MAX_RETRY_WAIT = 60.0
PAUSE_BETWEEN_MESSAGES = 1.0
COLOR_CONFIRMED = 0xE74C3C  # red
COLOR_WATCHLIST = 0x3498DB  # blue
COLOR_FORMING = 0xF39C12  # orange
COLOR_SUMMARY = 0x95A5A6
STATUS_COLOR = {"confirmed": COLOR_CONFIRMED, "watchlist": COLOR_WATCHLIST, "forming": COLOR_FORMING}
TV_INTERVAL = {"4h": "240", "1d": "1D", "1w": "1W"}
TF_LABEL = {"4h": "4小時線", "1d": "日線", "1w": "週線"}
STATUS_LABEL = {"forming": "右肩形成中", "watchlist": "右肩已確認", "confirmed": "已突破", "stale": "已過期"}
EXCHANGE_LABEL = {"binance": "幣安", "okx": "OKX", "gate": "Gate", "twse": "上市", "tpex": "上櫃"}
MARKET_LABEL = {"spot": "現貨", "perp": "永續", "stock": ""}
TW_PREFIX = "台股"


class NotifyError(Exception):
    """Sending failed (message never contains the webhook URL)."""


def _utc_text(now: datetime) -> str:
    return now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_time(text: str | None) -> datetime | None:
    if not text:
        return None
    try:
        return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def notify_key(r: Mapping[str, Any]) -> str:
    """Stable per-pattern key without RS (a forming RS moves with every new low); shared with build_site."""
    return pattern_key(r)


def load_state(path: Path) -> dict[str, Any] | None:
    """Patterns of a version-2 state; None = no usable state (missing, unreadable or an older version),
    which is treated as a first run."""
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        version = data.get("version")
        if not isinstance(version, int) or version < STATE_VERSION:
            return None
        pats = data.get("patterns")
        return pats if isinstance(pats, dict) else None
    except (OSError, ValueError, AttributeError):
        return None


def save_state(path: Path, patterns: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump({"version": STATE_VERSION, "patterns": patterns}, fh, ensure_ascii=False, indent=1,
                  sort_keys=True)
    tmp.replace(path)


def unique_rows(results: list[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    """key -> row; results are sorted best first, so the first row of a key wins."""
    out: dict[str, Mapping[str, Any]] = {}
    for r in results:
        out.setdefault(notify_key(r), r)
    return out


def event_kind(row: Mapping[str, Any], old: Mapping[str, Any] | None, *, min_score: float,
               statuses: set[str]) -> str | None:
    """"new" / "upgraded" / "table" (D34) / None for one row against its previous state entry."""
    status = row.get("status")
    if status not in statuses or float(row.get("pattern_score") or 0) < min_score:
        return None
    notified = old.get("notified_status") if old else None
    if status == "confirmed":
        if notified == "confirmed":
            return None
        return "new" if old is None else "upgraded"
    if status == "watchlist":  # D34: goes into the table, once (new, or forming -> watchlist)
        if notified in ("watchlist", "confirmed"):
            return None
        return "table" if (notified is None or (old or {}).get("status") == "forming") else None
    if status == "forming":
        return "new" if notified is None else None
    return None


def find_events(results: list[Mapping[str, Any]], state: Mapping[str, Any] | None, *,
                min_score: float, statuses: set[str]) -> list[dict[str, Any]]:
    """Events above the score threshold, best score first."""
    prev = state or {}
    events = []
    for key, r in unique_rows(results).items():
        kind = event_kind(r, prev.get(key), min_score=min_score, statuses=statuses)
        if kind:
            events.append({"kind": kind, "key": key, "row": r, "previous": (prev.get(key) or {}).get("status")})
    events.sort(key=lambda e: -float(e["row"].get("pattern_score") or 0))
    return events


def tradingview_url(row: Mapping[str, Any]) -> str:
    tv = row.get("tv_symbol") or f"BINANCE:{row['symbol']}"
    tf = row["timeframe"]
    return f"https://www.tradingview.com/chart/?symbol={quote(str(tv), safe='')}&interval={TV_INTERVAL.get(tf, tf)}"


def _decimals(x: float) -> int:
    """Same rule as the site (site/js/app.js decimalsFor): small coins keep ~4 significant digits."""
    a = abs(x)
    if a >= 1000:
        return 2
    if a >= 1:
        return 3
    if a >= 0.01:
        return 4
    if a == 0:
        return 2
    return min(12, math.ceil(-math.log10(a)) + 3)


def fmt_price(x: Any, ref: Any = None) -> str:
    """Price with digits by magnitude (of `ref` when given, so related prices line up): 0.0001750, 1.234."""
    if x is None:
        return "-"
    try:
        v = float(x)
    except (TypeError, ValueError):
        return "-"
    if not math.isfinite(v):
        return "-"
    d = _decimals(float(ref) if ref not in (None, 0) else v)
    return f"{v:,.{d}f}"


def is_tw_row(row: Mapping[str, Any]) -> bool:
    """Taiwan stock row (D29): market "stock" on TWSE / TPEx."""
    return row.get("market") == "stock" or row.get("exchange") in ("twse", "tpex")


def fmt_twd(x: Any, ref: Any = None) -> str:
    """Taiwan dollars, two decimals: NT$1,005.00 (`ref` accepted for the fmt_price signature, unused)."""
    if x is None:
        return "-"
    try:
        v = float(x)
    except (TypeError, ValueError):
        return "-"
    return f"NT${v:,.2f}" if math.isfinite(v) else "-"


def _pct(x: Any, digits: int = 1) -> str:
    if x is None:
        return "-"
    return f"{float(x) * 100:.{digits}f}%"


def market_label(row: Mapping[str, Any]) -> str:
    ex = row.get("exchange") or "binance"
    mk = row.get("market") or "spot"
    if is_tw_row(row):
        return f"台股{EXCHANGE_LABEL.get(ex, ex)}"
    return f"{EXCHANGE_LABEL.get(ex, ex)} {MARKET_LABEL.get(mk, mk)}"


def embed_title(r: Mapping[str, Any]) -> str:
    tf_status = f"{TF_LABEL.get(r['timeframe'], r['timeframe'])} {STATUS_LABEL.get(r['status'], r['status'])}"
    if is_tw_row(r):
        name = f" {r['name']}" if r.get("name") else ""
        return f"{TW_PREFIX} {r['symbol']}{name} {tf_status}"
    return f"{r['symbol']} {tf_status}"


def build_embed(event: Mapping[str, Any]) -> dict[str, Any]:
    r = event["row"]
    status = r["status"]
    ref = r.get("current_close")
    tw = is_tw_row(r)
    px = fmt_twd if tw else fmt_price
    side = {"above": "高於", "below": "低於"}.get(r.get("neckline_side"), "距")
    dist = r.get("distance_to_neckline")
    neck = px(r.get("current_neckline_price"), ref)
    neck_text = f"{neck}（{side}頸線 {float(dist) * 100:.1f}%）" if dist is not None else neck
    est = "" if r.get("breakout_detected") else "（預估）"
    title = embed_title(r)
    notes = []
    if status == "forming":
        notes.append("右肩尚未確認，會隨新 K 線變動。")
    if event.get("kind") == "upgraded" and event.get("previous") in ("forming", "watchlist", "stale"):
        notes.append(f"由「{STATUS_LABEL[event['previous']]}」轉為已突破。")
    if not r.get("breakout_detected"):
        notes.append("尚未突破：TP2 以目前頸線價預估。")
    fields = [
        ("市場" if tw else "交易所", market_label(r)),
        ("分數", f"{float(r['pattern_score']):.1f}"),
        ("目前價", px(ref)),
        ("頸線價", neck_text),
        ("TP1", px(r.get("tp1"), ref)),  # D28: fixed, not an estimate
        ("TP2" + est, px(r.get("tp2"), ref)),
        ("失效價（頭部低點）", px(r.get("invalidation_price"), ref)),
        ("左／右肩跌幅", f"{_pct(r.get('drop_left_pct'))}／{_pct(r.get('drop_right_pct'))}"),
        ("頭到頸線漲幅", _pct(r.get("head_rise_pct"))),
    ]
    embed = {
        "title": title,
        "url": tradingview_url(r),
        "color": STATUS_COLOR.get(status, COLOR_SUMMARY),
        "fields": [{"name": n, "value": v, "inline": True} for n, v in fields],
    }
    if notes:
        embed["description"] = "".join(notes)
    return embed


@dataclass
class Message:
    """One webhook call: payload (embeds) + PNG attachments (name, bytes) + the event keys it covers."""
    payload: dict[str, Any]
    files: list[tuple[str, bytes]] = field(default_factory=list)
    keys: list[str] = field(default_factory=list)
    table: bool = False  # D34: right-shoulder table (bot channel when configured, else webhook)


def attachment_name(row: Mapping[str, Any], key: str) -> str:
    """ASCII-only file name, e.g. starusdt_4h_1a2b3c4d.png (Discord needs it to match attachment://<name>)."""
    sym = re.sub(r"[^a-z0-9]+", "", str(row.get("symbol", "")).lower()) or "chart"
    tf = re.sub(r"[^a-z0-9]+", "", str(row.get("timeframe", "")).lower())
    return f"{sym[:24]}_{tf}_{hashlib.sha1(key.encode('utf-8')).hexdigest()[:8]}.png"


def make_chart_png(row: Mapping[str, Any], scan_dir: Path | None) -> bytes | None:
    """PNG for one result, or None (logged, never raised): the notification goes out without the picture."""
    label = f"{row.get('symbol')} {row.get('timeframe')}"
    try:
        rel = row.get("chart_file")
        if scan_dir is None or not rel:
            raise FileNotFoundError("no chart_file")
        doc = json.loads((scan_dir / rel).read_text(encoding="utf-8"))
        from ihs_scanner.chart_image import render_chart  # lazy: matplotlib problems must not stop notifying
        return render_chart(row, doc)
    except Exception as exc:  # noqa: BLE001
        print(f"[notify] no chart for {label}: {type(exc).__name__}: {str(exc)[:120]}", file=sys.stderr)
        return None


def build_messages(events: list[Mapping[str, Any]], *, extra_summary: str | None = None, market: str = "crypto",
                   image_for: Callable[[Mapping[str, Any]], bytes | None] | None = None) -> list[Message]:
    """Every event gets its own embed (no cap, D32), <= 10 per message; each embed carries its chart as
    attachment://<name> when image_for(row) returns PNG bytes (<= MAX_ATTACH_BYTES per message)."""
    messages: list[Message] = []
    for i in range(0, len(events), MAX_EMBEDS_PER_MESSAGE):
        msg = Message(payload={"embeds": []})
        total = 0
        for e in events[i:i + MAX_EMBEDS_PER_MESSAGE]:
            key = e.get("key") or notify_key(e["row"])
            embed = build_embed(e)
            png = image_for(e["row"]) if image_for else None
            if png and total + len(png) <= MAX_ATTACH_BYTES:
                name = attachment_name(e["row"], key)
                embed["image"] = {"url": f"attachment://{name}"}
                msg.files.append((name, png))
                total += len(png)
            msg.payload["embeds"].append(embed)
            msg.keys.append(key)
        messages.append(msg)
    if extra_summary:
        title = f"{TW_PREFIX}摘要" if market == "tw" else "摘要"
        messages.append(Message({"embeds": [{"title": title, "description": extra_summary, "color": COLOR_SUMMARY}]}))
    return messages


def table_title(market: str, now: datetime, n: int) -> str:
    """"右肩確認（加密貨幣）2026-10-22 16:00（台北）共 12 筆" -- Taipei time for both markets (D34)."""
    tw = market == "tw"
    t = now.astimezone(timezone(timedelta(hours=8)))
    return f"右肩確認（{'台股' if tw else '加密貨幣'}）{t:%Y-%m-%d %H:%M}（台北）共 {n} 筆"


def select_option(index: int, row: Mapping[str, Any], key: str) -> dict[str, str] | None:
    """One option of the watch_select menu; None (logged) when pattern_key does not fit Discord's 100-char value."""
    if len(key) > 100:
        print(f"[notify] select option skipped (pattern_key {len(key)} chars > 100): "
              f"{row.get('symbol')} {row.get('timeframe')}", file=sys.stderr)
        return None
    name = f" {row['name']}" if is_tw_row(row) and row.get("name") else ""
    label = f"#{index} {row.get('symbol')}{name} {row.get('timeframe')} {float(row.get('pattern_score') or 0):.0f}分"
    return {"label": label[:100], "value": key}


def build_table_messages(events: list[Mapping[str, Any]], *, market: str, now: datetime, with_select: bool,
                         font: str | None = None) -> list[Message]:
    """D34: the "table" events (best score first) as table pictures, 25 rows per picture and 125 rows per message
    (one string select per picture when `with_select`). Discord needs unique custom_ids inside a message, so the
    selects are watch_select, watch_select_2, ... A picture that cannot be drawn falls back to text lines."""
    if not events:
        return []
    from ihs_scanner.table_image import render_table  # lazy, like the chart image
    tw = market == "tw"
    chunks = [events[i:i + TABLE_ROWS_PER_MESSAGE] for i in range(0, len(events), TABLE_ROWS_PER_MESSAGE)]
    messages: list[Message] = []
    number = 0
    for ci, chunk in enumerate(chunks, 1):
        title = table_title(market, now, len(events)) + (f"（{ci}/{len(chunks)}）" if len(chunks) > 1 else "")
        msg = Message(payload={"content": title}, table=True)
        rows_components: list[dict[str, Any]] = []
        fallback: list[str] = []
        for gi in range(0, len(chunk), TABLE_ROWS_PER_IMAGE):
            group = chunk[gi:gi + TABLE_ROWS_PER_IMAGE]
            items = [(number + j + 1, e["row"]) for j, e in enumerate(group)]
            number += len(group)
            part = f"{items[0][0]}-{items[-1][0]} of {len(events)}"
            ascii_title = f"IHS right shoulder confirmed - {'TW stocks' if tw else 'crypto'}  ({part})"
            try:
                png = render_table(items, ascii_title, tw=tw, font=font)
                msg.files.append((f"watch_table_{market}_{len(msg.files) + 1}.png", png))
            except Exception as exc:  # noqa: BLE001 - never lose the notification over a picture
                print(f"[notify] table picture failed ({type(exc).__name__}: {str(exc)[:100]}); text instead",
                      file=sys.stderr)
                fallback += [f"#{n} {r['symbol']} {r['timeframe']} {float(r.get('pattern_score') or 0):.0f}"
                             for n, r in items]
            if with_select:
                options = [o for e, (n, r) in zip(group, items)
                           if (o := select_option(n, r, e.get("key") or notify_key(r)))]
                if options:
                    cid = SELECT_ID if not rows_components else f"{SELECT_ID}_{len(rows_components) + 1}"
                    rows_components.append({"type": 1, "components": [{
                        "type": 3, "custom_id": cid, "placeholder": "選擇要加入關注的標的（可多選）",
                        "min_values": 1, "max_values": len(options), "options": options}]})
        if fallback:
            msg.payload["content"] = (title + "\n```\n" + "\n".join(fallback) + "\n```")[:2000]
        if rows_components:
            msg.payload["components"] = rows_components
        msg.keys = [e.get("key") or notify_key(e["row"]) for e in chunk]
        messages.append(msg)
    return messages


def summary_message(text: str, market: str = "crypto") -> dict[str, Any]:
    title = f"{TW_PREFIX}頭肩底掃描" if market == "tw" else "頭肩底掃描"
    return {"embeds": [{"title": title, "description": text, "color": COLOR_SUMMARY}]}


def scan_market(scan: Mapping[str, Any]) -> str:
    """"tw" for the Taiwan-stock scan (payload `market`, or rows on TWSE / TPEx for older files), else "crypto"."""
    if scan.get("market") in ("tw", "crypto"):
        return scan["market"]
    rows = scan.get("results") or []
    return "tw" if rows and all(is_tw_row(r) for r in rows) else "crypto"


def first_run_text(rows: Mapping[str, Mapping[str, Any]], min_score: float, market: str = "crypto") -> str:
    counts: dict[str, int] = {}
    for r in rows.values():
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    parts = "、".join(f"{STATUS_LABEL.get(s, s)} {counts[s]}" for s in ("forming", "watchlist", "confirmed", "stale")
                     if counts.get(s))
    above = sum(float(r.get("pattern_score") or 0) >= min_score for r in rows.values())
    what = "台股" if market == "tw" else ""
    return (f"開始追蹤{what}，目前 {len(rows)} 個型態" + (f"（{parts}）" if parts else "") +
            f"，其中分數 ≥ {min_score:g} 的 {above} 個。之後只通知新出現的型態與突破。")


def post_message(url: str, payload: Mapping[str, Any], *, files: list[tuple[str, bytes]] | None = None,
                 sleep: Callable[[float], None] = time.sleep, max_retries: int = MAX_RETRIES,
                 headers: Mapping[str, str] | None = None) -> None:
    """POST one webhook / bot payload (JSON, or multipart payload_json + files[n] when `files` is given).
    429 waits retry_after; 5xx / network errors back off; bounded retries. Errors never contain the URL or the
    headers (bot token)."""
    last = "unknown error"
    extra = {"headers": dict(headers)} if headers else {}
    for attempt in range(max_retries + 1):
        try:
            if files:
                resp = requests.post(
                    url, data={"payload_json": json.dumps(payload, ensure_ascii=False)},
                    files=[(f"files[{i}]", (name, data, "image/png")) for i, (name, data) in enumerate(files)],
                    timeout=60, **extra)
            else:
                resp = requests.post(url, json=payload, timeout=20, **extra)
        except requests.RequestException as exc:  # message may embed the URL: keep only the type
            last = type(exc).__name__
            sleep(min(2.0 ** attempt, MAX_RETRY_WAIT))
            continue
        code = resp.status_code
        if 200 <= code < 300:
            print(f"[notify] Discord HTTP {code}" + (f" ({len(files)} attachment(s))" if files else ""))
            return
        if code == 429:
            wait = 1.0
            try:
                wait = float(resp.json().get("retry_after", wait))
            except Exception:
                try:
                    wait = float(resp.headers.get("Retry-After", wait))
                except (TypeError, ValueError):
                    pass
            last = "HTTP 429"
            sleep(min(max(wait, 0.0), MAX_RETRY_WAIT))
            continue
        if code >= 500:
            last = f"HTTP {code}"
            sleep(min(2.0 ** attempt, MAX_RETRY_WAIT))
            continue
        raise NotifyError(f"Discord rejected the message (HTTP {code})")  # 4xx: retrying will not help
    raise NotifyError(f"gave up after {max_retries + 1} attempts ({last})")


def bot_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bot {token}", "User-Agent": BOT_USER_AGENT}


def send_message(url: str, msg: Message, *, sleep: Callable[[float], None], pause: float,
                 headers: Mapping[str, str] | None = None) -> None:
    """Send with its pictures; if Discord rejects that (4xx, e.g. a bad attachment) retry once as plain embeds so
    one bad picture can never block the patterns of this message forever."""
    try:
        post_message(url, msg.payload, files=msg.files or None, sleep=sleep, headers=headers)
        return
    except NotifyError as exc:
        if not msg.files or "gave up" in str(exc) or not msg.payload.get("embeds"):
            raise
        print(f"[notify] {exc}; retrying this message without images", file=sys.stderr)
    plain = dict(msg.payload)
    plain["embeds"] = [{k: v for k, v in e.items() if k != "image"} for e in msg.payload["embeds"]]
    sleep(pause)
    post_message(url, plain, sleep=sleep, headers=headers)


def save_previews(messages: list[Message], preview_dir: Path) -> None:
    """Dry-run: write the PNGs that would be attached and list every message (titles + file names, no URL)."""
    preview_dir.mkdir(parents=True, exist_ok=True)
    for i, m in enumerate(messages, 1):
        embeds = m.payload.get("embeds") or []
        print(f"--- message {i}/{len(messages)} ({len(embeds)} embed(s), {len(m.files)} attachment(s)) ---")
        if m.payload.get("content"):
            first = str(m.payload["content"]).splitlines()[0]
            sels = [c for row in m.payload.get("components") or [] for c in row.get("components", [])]
            print(f"  {first}" + (f"  [{len(sels)} select(s), {sum(len(c['options']) for c in sels)} option(s)]"
                                  if sels else ""))
        names = {name for name, _ in m.files}
        for e in embeds:
            img = (e.get("image") or {}).get("url", "").removeprefix("attachment://")
            print(f"  {e.get('title')}" + (f"  [{img}]" if img in names else "  [no image]"))
        for name, data in m.files:
            (preview_dir / name).write_bytes(data)
    n = sum(len(m.files) for m in messages)
    print(f"[notify] preview images: {n} written to {preview_dir}")


def run_test_message(rows: Mapping[str, Mapping[str, Any]], market: str, *, dry_run: bool, webhook: str | None,
                     image_for: Callable[[Mapping[str, Any]], bytes | None], preview_dir: Path,
                     bot_token: str | None = None, main_channel: str | None = None,
                     now: datetime | None = None) -> int:
    """--test-message: one message with the best-scoring pattern, chart attached; reads and writes no state.
    D34: with the bot configured, also one test table (best 3 right-shoulder-confirmed rows) with the
    watch_select menu in the main channel, so clicking a row can be tried end to end."""
    rc = _run_test_single(rows, market, dry_run=dry_run, webhook=webhook, image_for=image_for, preview_dir=preview_dir)
    if not (bot_token and main_channel):
        return rc
    picks = sorted((r for r in rows.items() if r[1].get("status") == "watchlist"),
                   key=lambda kv: -float(kv[1].get("pattern_score") or 0))[:3]
    if not picks:
        print("[notify] test table: no right-shoulder-confirmed patterns in this scan")
        return rc
    from ihs_scanner.table_image import cjk_font
    msgs = build_table_messages([{"kind": "table", "key": k, "row": r, "previous": None} for k, r in picks],
                                market=market, now=now or datetime.now(timezone.utc), with_select=True,
                                font=cjk_font())
    for m in msgs:
        m.payload["content"] = "測試：" + str(m.payload.get("content", ""))
    if dry_run:
        save_previews(msgs, preview_dir)
        return rc
    try:
        for m in msgs:
            send_message(BOT_MESSAGES_URL.format(channel_id=main_channel), m, sleep=time.sleep,
                         pause=PAUSE_BETWEEN_MESSAGES, headers=bot_headers(bot_token))
    except NotifyError as exc:
        print(f"[notify] test table send failed: {exc}", file=sys.stderr)
        return 1
    print(f"[notify] test table sent by the bot ({len(picks)} row(s), select menu attached)")
    return rc


def _run_test_single(rows: Mapping[str, Mapping[str, Any]], market: str, *, dry_run: bool, webhook: str | None,
                     image_for: Callable[[Mapping[str, Any]], bytes | None], preview_dir: Path) -> int:
    if not rows:
        print("[notify] test message: scan has no patterns", file=sys.stderr)
        return 1
    key, row = max(rows.items(), key=lambda kv: float(kv[1].get("pattern_score") or 0))
    event = {"kind": "new", "key": key, "row": row, "previous": None}
    msg = build_messages([event], market=market, image_for=image_for)[0]
    msg.payload["content"] = TEST_TEXT
    print(f"[notify] test message: {embed_title(row)} (score {float(row.get('pattern_score') or 0):.1f}); "
          f"state untouched")
    if dry_run:
        save_previews([msg], preview_dir)
        print("[notify] dry-run: nothing sent")
        return 0
    if not webhook:
        print("[notify] test message: webhook 未設定", file=sys.stderr)
        return 1
    try:
        send_message(webhook, msg, sleep=time.sleep, pause=PAUSE_BETWEEN_MESSAGES)
    except NotifyError as exc:
        print(f"[notify] send failed: {exc}", file=sys.stderr)
        return 1
    print(f"[notify] test message sent ({len(msg.files)} attachment(s))")
    return 0


def run(scan_path: Path, state_path: Path, *, min_score: float, statuses: set[str], notify_all: bool,
        dry_run: bool, prune_days: int = PRUNE_DAYS, max_events: int = DEFAULT_MAX_EVENTS,
        webhook: str | None = None, now: datetime | None = None,
        sleep: Callable[[float], None] = time.sleep, pause: float = PAUSE_BETWEEN_MESSAGES,
        test_message: bool = False, preview_dir: Path | None = None,
        image_for: Callable[[Mapping[str, Any]], bytes | None] | None = None,
        bot_token: str | None = None, main_channel: str | None = None) -> int:
    now = now or datetime.now(timezone.utc)
    now_text = _utc_text(now)
    scan = json.loads(scan_path.read_text(encoding="utf-8"))
    results = scan.get("results", [])
    market = scan_market(scan)
    rows = unique_rows(results)
    if image_for is None:
        image_for = lambda r: make_chart_png(r, scan_path.parent)  # noqa: E731 - bars files sit next to scan.json
    if test_message:
        return run_test_message(rows, market, dry_run=dry_run, webhook=webhook, image_for=image_for,
                                preview_dir=preview_dir or DEFAULT_PREVIEW_DIR,
                                bot_token=bot_token if main_channel else None, main_channel=main_channel, now=now)
    use_bot = bool(bot_token and main_channel)
    state = load_state(state_path)
    first_run = state is None
    prev = dict(state or {})
    print(f"[notify] market={market}: {len(results)} patterns in scan ({len(rows)} keys), previous state: "
          f"{'none or old version (first run)' if first_run else str(len(prev)) + ' patterns'}; "
          f"min_score={min_score:g}, statuses={','.join(sorted(statuses))}; "
          f"webhook {'已設定' if webhook else '未設定'}, 機器人 {'已設定' if use_bot else '未設定'}"
          f"{'; dry-run' if dry_run else ''}")

    baseline = first_run and not notify_all
    if baseline:
        events: list[dict[str, Any]] = []
        messages = [Message(summary_message(first_run_text(rows, min_score, market), market))]
    else:
        events = find_events(results, None if first_run else prev, min_score=min_score, statuses=statuses)
        if max_events > 0 and len(events) > max_events:
            print(f"[notify] {len(events)} events > {ENV_MAX_EVENTS}={max_events}: sending the best {max_events}; "
                  f"the rest stay un-notified and go out next run")
            events = events[:max_events]
        singles = [e for e in events if e["kind"] != "table"]
        tables = [e for e in events if e["kind"] == "table"]
        messages = build_messages(singles, market=market,
                                  image_for=image_for if (webhook or dry_run) else None)
        if tables:
            from ihs_scanner.table_image import cjk_font
            messages += build_table_messages(tables, market=market, now=now, with_select=use_bot,
                                             font=cjk_font() if (webhook or use_bot or dry_run) else None)
    kinds: dict[str, int] = {}
    for e in events:
        label = "table" if e["kind"] == "table" else e["row"]["status"]
        kinds[label] = kinds.get(label, 0) + 1
    print(f"[notify] events: {len(events)} ({', '.join(f'{k} {v}' for k, v in sorted(kinds.items())) or 'none'}); "
          f"{len(messages)} message(s)")

    if dry_run:
        save_previews(messages, preview_dir or DEFAULT_PREVIEW_DIR)
        print("[notify] dry-run: nothing sent, state not written")
        return 0

    # Which event keys count as notified. Failed sends stay un-notified, so the next run retries them.
    sent_ok = True
    notified: set[str] = set()
    if not webhook:
        print("未設定 webhook，略過通知" + ("（右肩確認表格仍由機器人發送）" if use_bot else ""))
    if not use_bot and any(m.table for m in messages):
        print("[notify] 機器人未設定：右肩確認表格改用 webhook 發送（沒有下拉選單）")
    sent = 0
    for m in messages:
        if m.table and use_bot:
            dest, headers = BOT_MESSAGES_URL.format(channel_id=main_channel), bot_headers(bot_token)
        elif webhook:
            dest, headers = webhook, None
            if m.table:  # no select menu through a webhook
                m = Message({k: v for k, v in m.payload.items() if k != "components"}, m.files, m.keys, True)
        else:
            notified.update(m.keys)  # D20: nothing configured for this message; not re-sent later
            continue
        try:
            if sent:
                sleep(pause)
            send_message(dest, m, sleep=sleep, pause=pause, headers=headers)
        except NotifyError as exc:
            sent_ok = False
            print(f"[notify] send failed: {exc}", file=sys.stderr)
            break
        sent += 1
        notified.update(m.keys)
    if sent_ok and sent:
        print(f"[notify] sent {sent} message(s)")
    if not webhook and not use_bot:
        notified = {e["key"] for e in events}
    unsent_tables = {e["key"] for e in events if e["kind"] == "table" and e["key"] not in notified}

    new_state: dict[str, Any] = {}
    for key, r in rows.items():
        old = prev.get(key) or {}
        entry = {"status": r["status"], "score": float(r.get("pattern_score") or 0), "last_seen": now_text,
                 "last_notified_at": old.get("last_notified_at"), "notified_status": old.get("notified_status")}
        if baseline:
            entry["last_notified_at"], entry["notified_status"] = now_text, r["status"]
        elif key in notified:
            entry["last_notified_at"], entry["notified_status"] = now_text, r["status"]
        elif key in unsent_tables and old:
            entry["status"] = old.get("status", r["status"])  # keep "forming" so the table is retried next run
        new_state[key] = entry
    # patterns gone from the scan: keep for prune_days (so a flicker does not re-notify), then drop
    cutoff = now - timedelta(days=prune_days)
    pruned = 0
    for key, old in prev.items():
        if key in new_state:
            continue
        seen = _parse_time(old.get("last_seen"))
        if seen is None or seen >= cutoff:
            new_state[key] = old
        else:
            pruned += 1
    save_state(state_path, new_state)
    print(f"[notify] state written: {len(new_state)} patterns ({pruned} pruned)")
    return 0 if sent_ok else 1


def parse_args(argv=None) -> argparse.Namespace:
    env_min = os.environ.get(ENV_MIN_SCORE, "").strip()
    p = argparse.ArgumentParser(description="Send new / broken-out IHS patterns to Discord")
    p.add_argument("--scan", default=str(DEFAULT_SCAN), help="scan.json of this run")
    p.add_argument("--state", default=str(DEFAULT_STATE), help="state.json of the previous run (updated)")
    p.add_argument("--min-score", type=float, default=float(env_min) if env_min else DEFAULT_MIN_SCORE,
                   help=f"minimum pattern_score to notify (default {DEFAULT_MIN_SCORE:g}; env {ENV_MIN_SCORE})")
    p.add_argument("--statuses", default=DEFAULT_STATUSES,
                   help="statuses that can trigger a notification (default %(default)s)")
    p.add_argument("--notify-all", action="store_true", help="first run: notify every pattern instead of a summary")
    p.add_argument("--dry-run", action="store_true", help="print what would be sent; send nothing, write no state")
    p.add_argument("--prune-days", type=int, default=PRUNE_DAYS,
                   help="drop state entries not seen for this many days")
    env_max = os.environ.get(ENV_MAX_EVENTS, "").strip()
    p.add_argument("--max-events", type=int, default=int(env_max) if env_max else DEFAULT_MAX_EVENTS,
                   help=f"safety cap on events per run (default {DEFAULT_MAX_EVENTS}, 0 = unlimited; env "
                        f"{ENV_MAX_EVENTS}); events over it are neither sent nor recorded")
    p.add_argument("--test-message", action="store_true",
                   default=os.environ.get(ENV_TEST_MESSAGE, "").strip().lower() == "true",
                   help="send ONE test message (best-scoring pattern + chart), do not touch state "
                        f"(env {ENV_TEST_MESSAGE}=true)")
    p.add_argument("--preview-dir", default=str(DEFAULT_PREVIEW_DIR),
                   help="--dry-run: where the would-be attachments are written (default %(default)s)")
    return p.parse_args(argv)


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass
    args = parse_args(argv)
    scan = Path(args.scan)
    if not scan.exists():
        print(f"error: {scan} not found", file=sys.stderr)
        return 2
    statuses = {s.strip() for s in args.statuses.split(",") if s.strip()}
    webhook = os.environ.get(ENV_WEBHOOK, "").strip() or None
    bot_token = os.environ.get(ENV_BOT_TOKEN, "").strip() or None
    main_channel = os.environ.get(ENV_MAIN_CHANNEL, "").strip() or None
    return run(scan, Path(args.state), min_score=args.min_score, statuses=statuses, notify_all=args.notify_all,
               dry_run=args.dry_run, prune_days=args.prune_days, max_events=args.max_events, webhook=webhook,
               test_message=args.test_message, preview_dir=Path(args.preview_dir), bot_token=bot_token,
               main_channel=main_channel)


if __name__ == "__main__":
    sys.exit(main())
