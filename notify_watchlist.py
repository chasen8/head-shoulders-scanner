"""Watchlist change notifications (D34): tell the "關注頻道" when a watched pattern changes status.

Flow (docs/watchlist-bot-design.md):
  GET  {WATCHLIST_API_URL}/api/watchlist   (Bearer WATCHLIST_SYNC_KEY)  -> the watched patterns
  compare each one (by pattern_key) with this scan's scan.json -> status changed?
      forming -> watchlist -> confirmed, anything -> stale, pattern gone from the scan -> "gone" (once)
  bot message per change into DISCORD_WATCH_CHANNEL_ID: embed + pattern chart + "取消關注" button
  POST {WATCHLIST_API_URL}/api/watchlist {"update": [{pattern_key, last_status, last_notified_at[, name]}]}

Only the items of this scan's market (crypto / tw, derived from the exchange in pattern_key) are looked at. An item
whose last_status is null is only recorded (first sight, no message). Taiwan names missing from the list (name null)
are filled from the scan row. Without bot settings changes are logged and NOT recorded (so they go out once the bot
exists); without API settings the whole step is skipped. API / Discord failures never fail the workflow (exit 0).

The bot token, sync key and API URL are read from the environment only and never printed.

Example:
    python notify_watchlist.py --scan site/data/scan.json
    python notify_watchlist.py --scan site/data/tw/scan.json --dry-run --items-file items.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

import requests

import notify_discord as nd

ENV_API_URL = "WATCHLIST_API_URL"
ENV_SYNC_KEY = "WATCHLIST_SYNC_KEY"
ENV_WATCH_CHANNEL = "DISCORD_WATCH_CHANNEL_ID"
UNWATCH_PREFIX = "unwatch:"
STATUS_TEXT = {"forming": "右肩形成中", "watchlist": "右肩確認", "confirmed": "已突破", "stale": "已過期",
               "gone": "型態消失"}
GONE_NOTE = "型態消失（作廢或過期）：這次掃描已找不到此型態。"
DEFAULT_PREVIEW_DIR = nd.ROOT / "output" / "notify-preview"
TW_EXCHANGES = ("twse", "tpex")


class ApiError(Exception):
    """Worker API call failed (message never contains the URL or the key)."""


def utc_text(now: datetime) -> str:
    return now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def key_market(pattern_key: str) -> str:
    """"tw" when the exchange part of the key is twse / tpex, else "crypto"."""
    return "tw" if pattern_key.split("|", 1)[0] in TW_EXCHANGES else "crypto"


def unwatch_custom_id(pattern_key: str) -> str:
    """"unwatch:<pattern_key>", or "unwatch:" + sha1(pattern_key)[:16] when that exceeds Discord's 100 chars."""
    cid = UNWATCH_PREFIX + pattern_key
    if len(cid) <= 100:
        return cid
    return UNWATCH_PREFIX + hashlib.sha1(pattern_key.encode("utf-8")).hexdigest()[:16]


def api_endpoint(base: str) -> str:
    base = base.strip().rstrip("/")
    return base if base.endswith("/api/watchlist") else base + "/api/watchlist"


def _auth(sync_key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {sync_key}"}


def fetch_items(base: str, sync_key: str) -> list[dict[str, Any]]:
    try:
        resp = requests.get(api_endpoint(base), headers=_auth(sync_key), timeout=20)
    except requests.RequestException as exc:  # message may embed the URL: keep only the type
        raise ApiError(f"GET failed ({type(exc).__name__})") from None
    if resp.status_code != 200:
        raise ApiError(f"GET failed (HTTP {resp.status_code})")
    try:
        items = resp.json().get("items")
    except Exception:  # noqa: BLE001
        raise ApiError("GET returned invalid JSON") from None
    if not isinstance(items, list):
        raise ApiError("GET returned no items list")
    return [i for i in items if isinstance(i, dict) and isinstance(i.get("pattern_key"), str)]


def push_updates(base: str, sync_key: str, updates: list[dict[str, Any]]) -> None:
    try:
        resp = requests.post(api_endpoint(base), json={"update": updates}, headers=_auth(sync_key), timeout=20)
    except requests.RequestException as exc:
        raise ApiError(f"POST failed ({type(exc).__name__})") from None
    if not 200 <= resp.status_code < 300:
        raise ApiError(f"POST failed (HTTP {resp.status_code})")


def plan_changes(items: list[Mapping[str, Any]], rows: Mapping[str, Mapping[str, Any]], market: str
                 ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """-> (changes to notify, silent updates). A change = {item, row|None, previous, current}; a silent update is
    {pattern_key, last_status[, name]} (first sight, or only the Taiwan name filled in)."""
    changes: list[dict[str, Any]] = []
    silent: list[dict[str, Any]] = []
    for item in items:
        key = item["pattern_key"]
        if key_market(key) != market:
            continue
        row = rows.get(key)
        current = row["status"] if row else "gone"
        previous = item.get("last_status")
        name_fix = {"name": row["name"]} if (row and market == "tw" and not item.get("name") and row.get("name")) \
            else {}
        if previous is None:  # first sight: record only
            silent.append({"pattern_key": key, "last_status": current, "last_notified_at": None, **name_fix})
        elif previous != current:
            changes.append({"item": item, "row": row, "previous": previous, "current": current,
                            "name_fix": name_fix})
        elif name_fix:
            silent.append({"pattern_key": key, **name_fix})
    return changes, silent


def change_embed(ch: Mapping[str, Any]) -> dict[str, Any]:
    """Embed for one change: the usual detail fields when the pattern is still in the scan."""
    item, row, prev, cur = ch["item"], ch["row"], ch["previous"], ch["current"]
    arrow = f"{STATUS_TEXT.get(prev, prev)} → {STATUS_TEXT.get(cur, cur)}"
    if row is not None:
        embed = nd.build_embed({"kind": "new", "row": row, "previous": None})
        embed.pop("description", None)
        tw = nd.is_tw_row(row)
        name = f" {row['name']}" if tw and row.get("name") else ""
        symbol, tf = row["symbol"], row["timeframe"]
        embed["title"] = f"⭐ {nd.TW_PREFIX + ' ' if tw else ''}{symbol}{name} {tf} {arrow}"
        if cur == "forming":
            embed["description"] = "右肩尚未確認，會隨新 K 線變動。"
        return embed
    name = f" {item['name']}" if item.get("name") else ""
    prefix = nd.TW_PREFIX + " " if key_market(item["pattern_key"]) == "tw" else ""
    return {"title": f"⭐ {prefix}{item.get('symbol', '?')}{name} {item.get('timeframe', '')} {arrow}",
            "description": GONE_NOTE, "color": nd.COLOR_SUMMARY}


def build_change_message(ch: Mapping[str, Any], scan_dir: Path | None,
                         image_for: Callable[[Mapping[str, Any]], bytes | None] | None = None) -> nd.Message:
    embed = change_embed(ch)
    msg = nd.Message(payload={"embeds": [embed]}, keys=[ch["item"]["pattern_key"]])
    row = ch["row"]
    if row is not None:
        png = (image_for or (lambda r: nd.make_chart_png(r, scan_dir)))(row)
        if png:
            name = nd.attachment_name(row, ch["item"]["pattern_key"])
            embed["image"] = {"url": f"attachment://{name}"}
            msg.files.append((name, png))
    msg.payload["components"] = [{"type": 1, "components": [{
        "type": 2, "style": 4, "label": "取消關注", "custom_id": unwatch_custom_id(ch["item"]["pattern_key"])}]}]
    return msg


def run(scan_path: Path, *, api_url: str | None, sync_key: str | None, bot_token: str | None,
        watch_channel: str | None, dry_run: bool = False, preview_dir: Path | None = None,
        items_file: Path | None = None, now: datetime | None = None,
        sleep: Callable[[float], None] = time.sleep, pause: float = nd.PAUSE_BETWEEN_MESSAGES,
        image_for: Callable[[Mapping[str, Any]], bytes | None] | None = None) -> int:
    """Always 0 unless the scan file is unreadable: API / Discord problems are warnings only."""
    now = now or datetime.now(timezone.utc)
    scan = json.loads(scan_path.read_text(encoding="utf-8"))
    market = nd.scan_market(scan)
    rows = nd.unique_rows(scan.get("results", []))
    have_api = bool(api_url and sync_key)
    have_bot = bool(bot_token and watch_channel)
    print(f"[watch] market={market}: {len(rows)} patterns in scan; API {'已設定' if have_api else '未設定'}, "
          f"機器人/關注頻道 {'已設定' if have_bot else '未設定'}{'; dry-run' if dry_run else ''}")
    if not rows:
        print("[watch] 警告：scan.json 沒有任何型態，略過（避免把全部關注標的誤判為消失）", file=sys.stderr)
        return 0
    try:
        if items_file is not None:
            doc = json.loads(items_file.read_text(encoding="utf-8"))
            items = [i for i in (doc.get("items") if isinstance(doc, dict) else doc) if isinstance(i, dict)]
        elif have_api:
            items = fetch_items(api_url, sync_key)  # type: ignore[arg-type]
        else:
            print("[watch] 未設定 WATCHLIST_API_URL / WATCHLIST_SYNC_KEY，略過關注通知")
            return 0
    except (ApiError, OSError, ValueError) as exc:
        print(f"[watch] 警告：讀取關注清單失敗，略過（{exc if isinstance(exc, ApiError) else type(exc).__name__}）",
              file=sys.stderr)
        return 0

    mine = [i for i in items if key_market(i["pattern_key"]) == market]
    changes, silent = plan_changes(items, rows, market)
    print(f"[watch] 關注清單 {len(items)} 筆，此市場 {len(mine)} 筆；狀態改變 {len(changes)} 筆，"
          f"首次記錄/補名 {len(silent)} 筆")
    messages = [(ch, build_change_message(ch, scan_path.parent, image_for)) for ch in changes]

    if dry_run:
        out = preview_dir or DEFAULT_PREVIEW_DIR
        out.mkdir(parents=True, exist_ok=True)
        for ch, m in messages:
            e = m.payload["embeds"][0]
            print(f"  {e['title']}  [{'image' if m.files else 'no image'}; button {unwatch_custom_id(ch['item']['pattern_key'])[:40]}]")
            for fname, data in m.files:
                (out / f"watch_{fname}").write_bytes(data)
        print(f"[watch] dry-run: nothing sent, API not updated; previews in {out}")
        return 0

    updates = list(silent)
    if messages and not have_bot:
        print("[watch] 未設定 DISCORD_BOT_TOKEN / DISCORD_WATCH_CHANNEL_ID：略過關注通知（不記錄，之後補發）")
    elif messages:
        url = nd.BOT_MESSAGES_URL.format(channel_id=watch_channel)
        headers = nd.bot_headers(bot_token)  # type: ignore[arg-type]
        sent = 0
        for ch, m in messages:
            try:
                if sent:
                    sleep(pause)
                nd.send_message(url, m, sleep=sleep, pause=pause, headers=headers)
            except nd.NotifyError as exc:
                print(f"[watch] 警告：送出失敗，下次重試（{exc}）", file=sys.stderr)
                continue
            sent += 1
            updates.append({"pattern_key": ch["item"]["pattern_key"], "last_status": ch["current"],
                            "last_notified_at": utc_text(now), **ch["name_fix"]})
        print(f"[watch] sent {sent}/{len(messages)} message(s)")
    for ch, _ in messages if not have_bot else []:
        if ch["name_fix"]:  # still fill the name even when the notification cannot go out
            updates.append({"pattern_key": ch["item"]["pattern_key"], **ch["name_fix"]})
    if updates and have_api and items_file is None:
        try:
            push_updates(api_url, sync_key, updates)  # type: ignore[arg-type]
            print(f"[watch] updated {len(updates)} item(s) in the watchlist")
        except ApiError as exc:
            print(f"[watch] 警告：更新關注清單失敗（{exc}）", file=sys.stderr)
    return 0


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Notify the watch channel about status changes of watched patterns")
    p.add_argument("--scan", default=str(nd.DEFAULT_SCAN), help="scan.json of this run")
    p.add_argument("--dry-run", action="store_true", help="send nothing, do not POST; save previews")
    p.add_argument("--preview-dir", default=str(DEFAULT_PREVIEW_DIR))
    p.add_argument("--items-file", default=None, help="local JSON ({items:[...]}) instead of GET (testing)")
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
        print(f"[watch] 警告：{scan} not found，略過", file=sys.stderr)
        return 0

    def env(name: str) -> str | None:
        return os.environ.get(name, "").strip() or None

    try:
        return run(scan, api_url=env(ENV_API_URL), sync_key=env(ENV_SYNC_KEY), bot_token=env(nd.ENV_BOT_TOKEN),
                   watch_channel=env(ENV_WATCH_CHANNEL), dry_run=args.dry_run, preview_dir=Path(args.preview_dir),
                   items_file=Path(args.items_file) if args.items_file else None)
    except Exception as exc:  # noqa: BLE001 - never fail the workflow (D34)
        print(f"[watch] 警告：未預期的錯誤 {type(exc).__name__}，略過", file=sys.stderr)
        return 0


if __name__ == "__main__":
    sys.exit(main())
