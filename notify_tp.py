"""Take-profit notifications (charter D39): after a breakout, tell Discord when the price reaches TP1 / TP2.

Tracked: IHS rows with status "confirmed" (scan.json) and upward triangle breakouts, status "breakout_up"
(triangles.json; TP1 = target_conservative, TP2 = target). Rules and limits: ihs_scanner/tp_tracker.py and
docs/current-rules.md. One tracker file per market, kept on the scan-state branch like state.json:
    tp_tracker.json (crypto)   tp_tracker_tw.json (Taiwan stocks)
    {"version": 1, "market": ..., "updated_at": ..., "items": {pattern_id: item}}

Every run:
  1. every confirmed IHS / breakout_up triangle of this scan that is not tracked yet is added (status "open");
  2. for each "open" / "tp1" item, the CLOSED bars since its breakout are fetched (only the tracked symbols: Binance /
     OKX / Gate through the ihs_scanner.data_* fetchers, Taiwan through data_tw.YahooClient) and re-evaluated from
     scratch (tp_tracker.evaluate) -> new events tp1 / tp2 / failed / expired;
  3. tp1 / tp2 events (and failed with --notify-failed / TP_CONFIG["notify_failed"]) are sent by the bot, one embed
     per event (<= 10 embeds per message, the pattern chart attached), to the channel named
     「加密止盈」 / 「台股止盈」 in the server DISCORD_GUILD_ID (GET /guilds/{id}/channels, matched by name), or to
     DISCORD_TP_CHANNEL_CRYPTO / DISCORD_TP_CHANNEL_TW when set;
  4. the tracker is written (atomically). An item whose events could not be delivered (no bot, no channel, send
     failed) keeps its previous state, so the next run finds the same events again and retries ("下次再送").
First run (no tracker file): the current breakouts are added and evaluated, but events that already happened are only
recorded (no burst of old notifications). Events dated before the pattern's signal_time (the pattern was not visible
yet) are also only recorded. A missing channel is a log hint, never a workflow failure (exit 0); exit 1 only when a
send failed. The bot token, guild id and channel ids are read from the environment and never printed.

Example:
    python notify_tp.py --dry-run
    python notify_tp.py --scan site/data/tw/scan.json --tracker tp_tracker_tw.json --dry-run
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
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

import pandas as pd
import requests

import notify_discord as nd
from ihs_scanner import tp_tracker as tt

ROOT = Path(__file__).resolve().parent
DEFAULT_SCAN = ROOT / "site" / "data" / "scan.json"
DEFAULT_TRACKER = {"crypto": "tp_tracker.json", "tw": "tp_tracker_tw.json"}
DEFAULT_PREVIEW_DIR = ROOT / "output" / "notify-preview"
ENV_GUILD = "DISCORD_GUILD_ID"
ENV_CHANNEL = {"crypto": "DISCORD_TP_CHANNEL_CRYPTO", "tw": "DISCORD_TP_CHANNEL_TW"}
CHANNEL_NAME = {"crypto": "加密止盈", "tw": "台股止盈"}
GUILD_CHANNELS_URL = "https://discord.com/api/v10/guilds/{guild_id}/channels"
TEXT_CHANNEL_TYPES = (0, 5)  # text, announcement
TPE = timezone(timedelta(hours=8))
COLOR_TP1 = 0x089981
COLOR_TP2 = 0x22AB94
COLOR_FAILED = 0x787B86
EVENT_TEXT = {"tp1": "TP1 達成", "tp2": "TP2 達成", "failed": "跌破失效價"}
NOTIFY_TYPES = ("tp1", "tp2")
CHANNEL_HINT = ("請在 Discord 伺服器建立文字頻道「{name}」，並確認機器人能看到這個頻道、有「傳送訊息」「嵌入連結」"
                "「附加檔案」權限（或設定 {env} 直接指定頻道 ID）。這次的止盈事件不記錄，下次執行再送。")

Fetcher = Callable[[Mapping[str, Any], int], pd.DataFrame]


# ------------------------------------------------------------------ tracker file

def load_tracker(path: Path) -> dict[str, Any] | None:
    """Items of a tracker file; None = no usable file (missing / unreadable / other version) = first run."""
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("version") != tt.TRACKER_VERSION or not isinstance(data.get("items"), dict):
            return None
        return data["items"]
    except (OSError, ValueError, AttributeError):
        return None


def save_tracker(path: Path, items: Mapping[str, Any], market: str, now: datetime) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump({"version": tt.TRACKER_VERSION, "market": market, "updated_at": tt.utc_text(now), "items": items},
                  fh, ensure_ascii=False, indent=1, sort_keys=True)
    tmp.replace(path)


# ------------------------------------------------------------------ K lines (only the tracked symbols)

def fetch_key(item: Mapping[str, Any]) -> tuple[str, str, str, str]:
    return (str(item.get("exchange")), str(item.get("market")), str(item.get("symbol")), str(item.get("timeframe")))


class CryptoFetcher:
    """item, limit -> the newest `limit` closed bars of the item's exchange / market / symbol / timeframe."""

    def __init__(self, session=None):
        from ihs_scanner import data_gate, data_okx
        from ihs_scanner.data_binance import WeightLimiter
        self.session = session or requests.Session()
        self.binance = WeightLimiter()
        self.okx = data_okx.new_limiter()
        self.gate = data_gate.new_limiter()

    def __call__(self, item: Mapping[str, Any], limit: int) -> pd.DataFrame:
        from ihs_scanner import data_binance, data_binance_futures, data_gate, data_okx
        ex, mk, sym, tf = fetch_key(item)
        base = sym[:-4] if sym.endswith("USDT") and len(sym) > 4 else sym
        if ex == "binance" and mk == "spot":
            return data_binance.fetch_klines(sym, tf, limit, session=self.session, limiter=self.binance)
        if ex == "binance" and mk == "perp":
            return data_binance_futures.fetch_klines(sym, tf, limit, session=self.session)
        if ex == "okx":
            inst = f"{base}-USDT" + ("-SWAP" if mk == "perp" else "")
            return data_okx.fetch_klines(inst, tf, limit, session=self.session, limiter=self.okx)
        if ex == "gate":
            return data_gate.fetch_klines(f"{base}_USDT", tf, limit, session=self.session, limiter=self.gate)
        raise ValueError(f"unknown source {ex}-{mk}")


class TwFetcher:
    """Taiwan stocks through data_tw.YahooClient (cookie + crumb fetched on first use)."""

    def __init__(self, client=None):
        self.client = client

    def __call__(self, item: Mapping[str, Any], limit: int) -> pd.DataFrame:
        from ihs_scanner import data_tw
        from ihs_scanner.sources import Instrument, SourceFetchError
        if self.client is None:
            self.client = data_tw.YahooClient(limiter=data_tw.new_limiter())
            self.client.refresh_crumb(min_age=0)
        ex, _mk, sym, tf = fetch_key(item)
        inst = Instrument(base=sym, symbol=sym, native=sym + data_tw.YAHOO_SUFFIX.get(ex, ".TW"), exchange=ex,
                          market="stock", name=str(item.get("name") or ""))
        frames, errors = data_tw.fetch_series(inst, [tf], limit, self.client)
        if tf not in frames:
            raise SourceFetchError(errors.get(tf, "no bars"))
        return frames[tf]


def fetch_frames(items: list[Mapping[str, Any]], fetch: Fetcher, now: datetime,
                 max_bars: Mapping[str, int]) -> tuple[dict[tuple, pd.DataFrame], dict[tuple, str]]:
    """One fetch per exchange / market / symbol / timeframe (the largest window any of its items needs)."""
    need: dict[tuple, tuple[int, Mapping[str, Any]]] = {}
    for it in items:
        k = fetch_key(it)
        n = tt.bars_needed(it, now, max_bars=max_bars)
        if k not in need or n > need[k][0]:
            need[k] = (n, it)
    frames: dict[tuple, pd.DataFrame] = {}
    errors: dict[tuple, str] = {}
    for k, (n, it) in need.items():
        try:
            frames[k] = fetch(it, n)
        except Exception as exc:  # noqa: BLE001 - one symbol never stops the others
            errors[k] = f"{type(exc).__name__}: {str(exc)[:160]}"
            print(f"[tp] fetch failed {k[2]} {k[3]} ({k[0]}-{k[1]}): {errors[k]}", file=sys.stderr)
    return frames, errors


# ------------------------------------------------------------------ Discord

def resolve_channel(market: str, *, token: str | None, guild_id: str | None, override: str | None = None,
                    sleep: Callable[[float], None] = time.sleep) -> tuple[str | None, str]:
    """-> (channel id or None, log text). Name match: the text channel called CHANNEL_NAME[market] (case and a
    leading # ignored). Never prints the token or the ids."""
    name = CHANNEL_NAME[market]
    if override:
        return override.strip(), f"頻道由 {ENV_CHANNEL[market]} 指定"
    if not token:
        return None, "機器人未設定（DISCORD_BOT_TOKEN）"
    if not guild_id:
        return None, f"未設定 {ENV_GUILD}，無法用名稱找頻道「{name}」"
    last = "unknown error"
    for attempt in range(4):
        try:
            resp = requests.get(GUILD_CHANNELS_URL.format(guild_id=guild_id), headers=nd.bot_headers(token),
                                timeout=20)
        except requests.RequestException as exc:  # message may embed the URL: keep only the type
            last = type(exc).__name__
            sleep(min(2.0 ** attempt, 30.0))
            continue
        code = resp.status_code
        if code == 429:
            wait = 1.0
            try:
                wait = float(resp.json().get("retry_after", wait))
            except Exception:  # noqa: BLE001
                pass
            last = "HTTP 429"
            sleep(min(max(wait, 0.0), 30.0))
            continue
        if code >= 500:
            last = f"HTTP {code}"
            sleep(min(2.0 ** attempt, 30.0))
            continue
        if code != 200:
            return None, f"讀不到伺服器的頻道清單（HTTP {code}；機器人是否在這個伺服器？）"
        try:
            channels = resp.json()
        except Exception:  # noqa: BLE001
            return None, "伺服器頻道清單格式不對"
        want = _norm(name)
        for ch in channels if isinstance(channels, list) else []:
            if isinstance(ch, dict) and ch.get("type") in TEXT_CHANNEL_TYPES and _norm(ch.get("name")) == want \
                    and ch.get("id"):
                return str(ch["id"]), f"找到頻道 #{name}"
        return None, f"伺服器裡找不到文字頻道「{name}」"
    return None, f"讀不到伺服器的頻道清單（{last}）"


def _norm(text: Any) -> str:
    return str(text or "").strip().lstrip("#").strip().lower()


def time_text(iso: str | None, timeframe: str) -> str:
    """Taipei time: 4h with the hour, 1d / 1w the date only."""
    t = tt.parse_utc(iso)
    if t is None:
        return "-"
    local = t.tz_convert(TPE)
    return local.strftime("%Y-%m-%d %H:%M") if timeframe == "4h" else local.strftime("%Y-%m-%d")


def span_text(item: Mapping[str, Any], event: Mapping[str, Any]) -> str:
    """"3 根 K 線（約 0.5 天）" counted from the breakout bar (0 = the breakout bar itself)."""
    k = int(event.get("bars") or 0)
    if k == 0:
        return "突破當根"
    t0, t1 = tt.parse_utc(item.get("breakout_time")), tt.parse_utc(event.get("time"))
    days = (t1 - t0).total_seconds() / 86400 if t0 is not None and t1 is not None else None
    if days is None:
        return f"{k} 根 K 線"
    return f"{k} 根 K 線（約 {days:.1f} 天）" if days < 10 else f"{k} 根 K 線（約 {days:.0f} 天）"


def event_title(item: Mapping[str, Any], etype: str) -> str:
    """"🎯 BTCUSDT 日線 TP1 達成" / "🎯 台股 2330 台積電 日線 TP2 達成"."""
    icon = "🎯" if etype in NOTIFY_TYPES else "⚠️"
    tf = nd.TF_LABEL.get(item["timeframe"], item["timeframe"])
    what = EVENT_TEXT.get(etype, etype)
    if tt.is_tw_row(item):
        name = f" {item['name']}" if item.get("name") else ""
        return f"{icon} {nd.TW_PREFIX} {item['symbol']}{name} {tf} {what}"
    return f"{icon} {item['symbol']} {tf} {what}"


def build_embed(item: Mapping[str, Any], event: Mapping[str, Any]) -> dict[str, Any]:
    tw = tt.is_tw_row(item)
    ref = item.get("breakout_price")
    px = nd.fmt_twd if tw else nd.fmt_price
    etype = event["type"]
    bp = float(item["breakout_price"])
    gain = float(event["price"]) / bp - 1 if bp else None
    url = nd.tradingview_url(item)
    fields = [
        ("型態", str(item.get("pattern_label") or "")),
        ("市場" if tw else "交易所", nd.market_label(item)),
        ("突破時間（台北）／突破價", f"{time_text(item.get('breakout_time'), item['timeframe'])}／{px(bp, ref)}"),
        ("達成價" if etype in NOTIFY_TYPES else "失效價", px(event.get("price"), ref)),
        ("從突破價算", "-" if gain is None else f"{gain * 100:+.1f}%"),
        ("花費", span_text(item, event)),
        ("達成 K 線（台北）", time_text(event.get("time"), item["timeframe"])),
        ("TP1", px(item.get("tp1"), ref)),
        ("TP2", px(item.get("tp2"), ref)),
        ("失效價", px(item.get("invalidation_price"), ref)),
        ("TradingView", f"[開啟圖表]({url})"),
    ]
    note = {"tp1": "繼續追蹤 TP2。", "tp2": "追蹤結束。", "failed": "TP 前跌破失效價，追蹤結束。"}.get(etype, "")
    if etype == "failed" and item.get("status") == "tp1":
        note = "TP1 之後跌破失效價，追蹤結束。"
    return {"title": event_title(item, etype)[:256], "url": url, "description": note,
            "color": {"tp1": COLOR_TP1, "tp2": COLOR_TP2}.get(etype, COLOR_FAILED),
            "fields": [{"name": n, "value": v, "inline": True} for n, v in fields]}


def attachment_name(item: Mapping[str, Any]) -> str:
    """ASCII-only: tp-btcusdt_1d_1a2b3c4d.png."""
    sym = re.sub(r"[^a-z0-9]+", "", str(item.get("symbol", "")).lower()) or "chart"
    tf = re.sub(r"[^a-z0-9]+", "", str(item.get("timeframe", "")).lower())
    return f"tp-{sym[:24]}_{tf}_{hashlib.sha1(str(item['pattern_id']).encode('utf-8')).hexdigest()[:8]}.png"


def frame_bars(frame: pd.DataFrame, now: datetime) -> list[list[float]]:
    df = frame[frame["close_time"] < pd.Timestamp(now).tz_convert("UTC")]
    return [[int(pd.Timestamp(t).timestamp()), float(o), float(h), float(lo), float(c), float(v)]
            for t, o, h, lo, c, v in zip(df["timestamp"], df["open"], df["high"], df["low"], df["close"],
                                         df["volume"])]


def chart_doc(item: Mapping[str, Any], *, scan_dir: Path | None, frame: pd.DataFrame | None,
              now: datetime) -> dict[str, Any] | None:
    """The bars file of this scan when it still exists (same pattern_id), else the stored skeleton + the fetched
    bars (only when they reach back to the chart start). None = no picture."""
    rel = item.get("chart_file")
    if scan_dir is not None and rel and (scan_dir / rel).is_file():
        try:
            doc = json.loads((scan_dir / rel).read_text(encoding="utf-8"))
            if doc.get("pattern_id") == item["pattern_id"] and doc.get("bars"):
                doc = dict(doc)
                if frame is not None and not frame.empty:  # a bar that closed after the build: append it
                    last = doc["bars"][-1][0]
                    doc["bars"] = list(doc["bars"]) + [b for b in frame_bars(frame, now) if b[0] > last]
                return doc
        except (OSError, ValueError, TypeError, IndexError):
            pass
    skel = item.get("chart")
    if not isinstance(skel, Mapping) or frame is None or frame.empty:
        return None
    bars = frame_bars(frame, now)
    start = tt.chart_start_time(skel)
    if not bars or start is None or bars[0][0] > start.timestamp():
        return None
    doc = dict(skel)
    doc["bars"] = bars
    return doc


def make_png(item: Mapping[str, Any], events: list[Mapping[str, Any]], *, scan_dir: Path | None,
             frame: pd.DataFrame | None, now: datetime) -> bytes | None:
    """Chart with a star on every event bar, or None (logged, never raised): the event goes out without it."""
    try:
        doc = chart_doc(item, scan_dir=scan_dir, frame=frame, now=now)
        if doc is None:
            raise FileNotFoundError("bars file gone and no usable skeleton")
        doc["marks"] = [{"time": tt.parse_utc(e["time"]).timestamp(), "price": float(e["price"]),
                         "label": str(e["type"]).upper()} for e in events if e["type"] in NOTIFY_TYPES]
        status = " + ".join(f"{e['type'].upper()} hit" for e in events if e["type"] in NOTIFY_TYPES) or "breakout"
        row = {"symbol": item.get("symbol"), "timeframe": item.get("timeframe"), "status": status,
               "pattern_score": item.get("score"), "exchange": item.get("exchange"), "market": item.get("market"),
               "triangle_type": item.get("triangle_type")}
        from ihs_scanner.chart_image import render_chart, render_triangle_chart  # lazy, like notify_discord
        return (render_triangle_chart if item.get("kind") == "triangle" else render_chart)(row, doc)
    except Exception as exc:  # noqa: BLE001
        print(f"[tp] no chart for {item.get('symbol')} {item.get('timeframe')}: {type(exc).__name__}: "
              f"{str(exc)[:120]}", file=sys.stderr)
        return None


def build_messages(batches: list[tuple[Mapping[str, Any], list[Mapping[str, Any]], bytes | None]]
                   ) -> list[nd.Message]:
    """(item, events, png) -> messages of <= 10 embeds; an item's events always share one message (its picture on
    the last one) so an item is either fully delivered or not at all. msg.keys = the pattern_ids it covers."""
    messages: list[nd.Message] = []
    cur = nd.Message(payload={"embeds": []})
    size = 0
    for item, events, png in batches:
        embeds = [build_embed(item, e) for e in events][:nd.MAX_EMBEDS_PER_MESSAGE]
        extra = len(png) if png else 0
        if cur.payload["embeds"] and (len(cur.payload["embeds"]) + len(embeds) > nd.MAX_EMBEDS_PER_MESSAGE
                                      or size + extra > nd.MAX_ATTACH_BYTES):
            messages.append(cur)
            cur, size = nd.Message(payload={"embeds": []}), 0
        if png and size + extra <= nd.MAX_ATTACH_BYTES:
            name = attachment_name(item)
            embeds[-1]["image"] = {"url": f"attachment://{name}"}
            cur.files.append((name, png))
            size += extra
        cur.payload["embeds"].extend(embeds)
        cur.keys.append(str(item["pattern_id"]))
    if cur.payload["embeds"]:
        messages.append(cur)
    return messages


# ------------------------------------------------------------------ run

def _load_json(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def run(scan_path: Path, tracker_path: Path, *, triangles_path: Path | None = None, dry_run: bool = False,
        notify_failed: bool | None = None, fetch: Fetcher | None = None, now: datetime | None = None,
        bot_token: str | None = None, guild_id: str | None = None, channel_override: str | None = None,
        preview_dir: Path | None = None, sleep: Callable[[float], None] = time.sleep,
        pause: float = nd.PAUSE_BETWEEN_MESSAGES, config: Mapping[str, Any] | None = None) -> int:
    cfg = {**tt.TP_CONFIG, **(config or {})}
    if notify_failed is not None:
        cfg["notify_failed"] = bool(notify_failed)
    max_bars = cfg["max_bars"]
    now = now or datetime.now(timezone.utc)
    scan = _load_json(scan_path)
    if scan is None:
        print(f"[tp] {scan_path} unreadable; nothing done", file=sys.stderr)
        return 0
    market = nd.scan_market(scan)
    scan_dir = scan_path.parent
    tri_path = triangles_path if triangles_path is not None else scan_dir / "triangles.json"
    triangles = _load_json(tri_path) if tri_path.exists() else None
    if fetch is None:
        fetch = TwFetcher() if market == "tw" else CryptoFetcher()

    stored = load_tracker(tracker_path)
    first_run = stored is None
    items: dict[str, dict[str, Any]] = {k: dict(v) for k, v in (stored or {}).items()}
    candidates_rows = [r for r in (scan.get("results") or []) if r.get("status") == "confirmed"] + \
                      [r for r in ((triangles or {}).get("results") or []) if r.get("status") == "breakout_up"]
    charts = {}
    for r in candidates_rows:
        rel = r.get("chart_file")
        if rel and rel not in charts and r.get("pattern_id") not in items:
            doc = _load_json(scan_dir / rel)
            if doc is not None:
                charts[rel] = doc
    added = 0
    for cand in tt.new_items(scan, triangles, charts=charts, now=now):
        if (market == "tw") != tt.is_tw_row(cand):
            continue  # market split: a crypto tracker never gets a stock and vice versa
        pid = cand["pattern_id"]
        if pid in items:
            if items[pid].get("status") in tt.OPEN_STATUSES and cand.get("chart_file"):
                items[pid]["chart_file"] = cand["chart_file"]
            continue
        items[pid] = cand
        added += 1

    work = [pid for pid, it in items.items() if it.get("status") in tt.OPEN_STATUSES]
    frames, fetch_errors = fetch_frames([items[p] for p in work], fetch, now, max_bars)
    updates: dict[str, tuple[dict[str, Any], list[dict[str, Any]]]] = {}
    for pid in work:
        it = items[pid]
        frame = frames.get(fetch_key(it))
        if frame is None:
            continue
        res = tt.evaluate(it, frame, now=now, max_bars=max_bars)
        done = tt.recorded_types(it)
        fresh = []
        for e in res["events"]:
            if e["type"] in done:
                continue
            e = dict(e)
            reason = None
            if first_run:
                reason = "first_run"
            elif e["type"] in NOTIFY_TYPES:
                sig = tt.parse_utc(it.get("signal_time"))
                if sig is not None and tt.parse_utc(e["time"]) < sig:
                    reason = "before_signal"
            elif not (e["type"] == "failed" and cfg["notify_failed"]):
                reason = "not_notified"
            e["notify"] = reason is None
            e["silent_reason"] = reason
            fresh.append(e)
        updates[pid] = (res, fresh)

    to_send = [(pid, [e for e in fresh if e["notify"]]) for pid, (_res, fresh) in updates.items()]
    to_send = [(pid, evs) for pid, evs in to_send if evs]
    kinds: dict[str, int] = {}
    for _res, fresh in updates.values():
        for e in fresh:
            kinds[e["type"]] = kinds.get(e["type"], 0) + 1
    print(f"[tp] market={market}: tracker {'none (first run: events are only recorded)' if first_run else len(stored)}"
          f" -> {len(items)} items (+{added} new); checked {len(work)} open, fetch failures {len(fetch_errors)}; "
          f"new events: {', '.join(f'{k} {v}' for k, v in sorted(kinds.items())) or 'none'}; "
          f"to notify: {sum(len(e) for _, e in to_send)} event(s) of {len(to_send)} pattern(s)"
          f"{'; dry-run' if dry_run else ''}")

    messages = build_messages([(items[pid], evs,
                                make_png(items[pid], evs, scan_dir=scan_dir, frame=frames.get(fetch_key(items[pid])),
                                         now=now))
                               for pid, evs in to_send]) if to_send else []

    channel, why = (None, "沒有要送的事件")
    if to_send or dry_run:
        channel, why = resolve_channel(market, token=bot_token, guild_id=guild_id, override=channel_override,
                                       sleep=sleep)
        print(f"[tp] Discord 頻道「{CHANNEL_NAME[market]}」：{why}")
        if channel is None and bot_token:
            print("[tp] " + CHANNEL_HINT.format(name=CHANNEL_NAME[market], env=ENV_CHANNEL[market]))

    if dry_run:
        if messages:
            pdir = preview_dir or DEFAULT_PREVIEW_DIR
            nd.save_previews(messages, pdir)
            (pdir / "tp-preview.json").write_text(json.dumps([m.payload for m in messages], ensure_ascii=False,
                                                              indent=1), encoding="utf-8")
        print(f"[tp] dry-run: {len(messages)} message(s) would go to "
              f"{'#' + CHANNEL_NAME[market] if channel else '(no channel)'}; nothing sent, tracker not written")
        return 0

    delivered: set[str] = set()
    rc = 0
    if messages and channel:
        url = nd.BOT_MESSAGES_URL.format(channel_id=channel)
        for i, m in enumerate(messages):
            try:
                if i:
                    sleep(pause)
                nd.send_message(url, m, sleep=sleep, pause=pause, headers=nd.bot_headers(bot_token or ""))
            except nd.NotifyError as exc:
                rc = 1
                hint = "（機器人沒有這個頻道的傳送權限？）" if exc.status in (403, 404) else ""
                print(f"[tp] send failed: {exc}{hint}; the rest is retried next run", file=sys.stderr)
                break
            delivered.update(m.keys)
        print(f"[tp] sent {len([m for m in messages if set(m.keys) <= delivered])} message(s) to "
              f"#{CHANNEL_NAME[market]}")

    pending = 0
    now_text = tt.utc_text(now)
    for pid, (res, fresh) in updates.items():
        if any(e["notify"] for e in fresh) and pid not in delivered:
            pending += 1
            continue  # unchanged: the same events are found again next run
        it = items[pid]
        for e in fresh:
            e["notified_at"] = now_text if e["notify"] else None
            it.setdefault("events", []).append(e)
        it["status"] = res["status"]
        it["bars_since_breakout"] = res["bars_since_breakout"]
        it["last_bar_time"] = res["last_bar_time"]
        if res.get("last_close") is not None:  # D41: the track-record page's return of an open item
            it["last_close"] = res["last_close"]
        it["last_checked_at"] = now_text
        if res["status"] in tt.END_STATUSES and not it.get("ended_at"):
            it["ended_at"] = now_text
    items = tt.prune(items, now, int(cfg["prune_days"]))
    save_tracker(tracker_path, items, market, now)
    try:  # D41: the track-record page (site/data[/tw]/track_record.json); never fails the run
        from ihs_scanner import track_record
        track_record.write_from_tracker(tracker_path, scan_dir / "track_record.json", market, now)
    except Exception as exc:  # noqa: BLE001
        print(f"[tp] track_record.json not written: {type(exc).__name__}: {str(exc)[:120]}", file=sys.stderr)
    print(f"[tp] tracker written: {len(items)} items {tt.summarize(items.values())}"
          f"{f'; {pending} pattern(s) left unchanged for retry' if pending else ''}")
    return rc


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Send TP1 / TP2 hits of broken-out patterns to Discord (D39)")
    p.add_argument("--scan", default=str(DEFAULT_SCAN), help="scan.json of this run (market taken from it)")
    p.add_argument("--triangles", default=None, help="triangles.json (default: next to --scan)")
    p.add_argument("--tracker", default=None,
                   help="tracker file (default tp_tracker.json / tp_tracker_tw.json by the scan's market)")
    p.add_argument("--dry-run", action="store_true", help="send nothing, write no tracker; save previews")
    p.add_argument("--notify-failed", action="store_true", default=None,
                   help="also notify failures (low <= invalidation); default TP_CONFIG notify_failed=False")
    p.add_argument("--preview-dir", default=str(DEFAULT_PREVIEW_DIR))
    return p.parse_args(argv)


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    args = parse_args(argv)
    scan = Path(args.scan)
    if not scan.exists():
        print(f"[tp] {scan} not found; nothing done", file=sys.stderr)
        return 0
    market = nd.scan_market(_load_json(scan) or {})
    tracker = Path(args.tracker) if args.tracker else Path(DEFAULT_TRACKER[market])
    env = lambda k: os.environ.get(k, "").strip() or None  # noqa: E731
    try:
        return run(scan, tracker, triangles_path=Path(args.triangles) if args.triangles else None,
                   dry_run=args.dry_run, notify_failed=args.notify_failed, bot_token=env(nd.ENV_BOT_TOKEN),
                   guild_id=env(ENV_GUILD), channel_override=env(ENV_CHANNEL[market]),
                   preview_dir=Path(args.preview_dir))
    except Exception as exc:  # noqa: BLE001 - the tracker file is untouched (written only at the very end)
        print(f"[tp] failed: {type(exc).__name__}: {mask(str(exc)[:200], env(nd.ENV_BOT_TOKEN), env(ENV_GUILD))}",
              file=sys.stderr)
        return 1


def mask(text: str, *secrets: str | None) -> str:
    """Replace every secret value inside `text` with ***."""
    for s in secrets:
        if s:
            text = text.replace(s, "***")
    return text


if __name__ == "__main__":
    sys.exit(main())
