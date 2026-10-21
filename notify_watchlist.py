"""Watchlist change notifications (D34, per user since D35): tell each watcher when a watched pattern changes status.

Flow (docs/watchlist-bot-design.md):
  GET  {WATCHLIST_API_URL}/api/watchlist   (Bearer WATCHLIST_SYNC_KEY)
       -> {"users": [{user_id, username, notify: "channel"|"dm", items: [...]}]}
  compare every user's items (by pattern_key) with this scan's scan.json -> status changed?
      forming -> watchlist -> confirmed, anything -> stale, pattern gone from the scan -> "gone" (once)
  the same change of the same pattern is grouped over all its watchers:
      notify "channel": ONE message in DISCORD_WATCH_CHANNEL_ID that @-mentions all of them
                        (allowed_mentions: only those users, never @everyone / roles)
      notify "dm":      POST /users/@me/channels {recipient_id} -> DM channel -> the message there;
                        a 403 (the user does not accept DMs from server members) falls back to the watch channel
                        with an @-mention and a "私訊失敗" note
      every message: embed + pattern chart + "取消關注" button (the Worker removes it from the clicker's list only)
  POST {WATCHLIST_API_URL}/api/watchlist {"update": [{user_id, pattern_key, last_status, last_notified_at[, name]}]}

Only the items of this scan's market (crypto / tw, derived from the exchange in pattern_key) are looked at. An item
whose last_status is null is only recorded (first sight, no message). Taiwan names missing from a list (name null)
are filled from the scan row. A change that could not be delivered to a user is not recorded for that user (it goes
out next scan); without the bot token nothing is sent or recorded; without API settings the whole step is skipped.
API / Discord failures never fail the workflow (exit 0).

The bot token, sync key and API URL are read from the environment only and never printed.

Example:
    python notify_watchlist.py --scan site/data/scan.json
    python notify_watchlist.py --scan site/data/tw/scan.json --dry-run --users-file users.json
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
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
DM_CHANNEL_URL = "https://discord.com/api/v10/users/@me/channels"
STATUS_TEXT = {"forming": "右肩形成中", "watchlist": "右肩確認", "confirmed": "已突破", "stale": "已過期",
               "gone": "型態消失"}
GONE_NOTE = "型態消失（作廢或過期）：這次掃描已找不到此型態。"
DM_FAILED_NOTE = "私訊失敗，改在這裡通知（請到伺服器的隱私設定允許成員私訊，或用 /notify mode:channel）"
DEFAULT_PREVIEW_DIR = nd.ROOT / "output" / "notify-preview"
TW_EXCHANGES = ("twse", "tpex")
MENTIONS_PER_MESSAGE = 50  # Discord allows <= 100 users in allowed_mentions; 50 mentions keep content < 2000 chars
USER_ID = re.compile(r"^\d{5,25}$")


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


def clean_users(raw: Any) -> list[dict[str, Any]]:
    """Keep well-formed users ({user_id, notify, items}) and their well-formed items."""
    out = []
    for u in raw if isinstance(raw, list) else []:
        if not isinstance(u, dict) or not isinstance(u.get("user_id"), str) or not USER_ID.match(u["user_id"]):
            continue
        items = [i for i in u.get("items") or [] if isinstance(i, dict) and isinstance(i.get("pattern_key"), str)]
        out.append({"user_id": u["user_id"], "username": u.get("username"),
                    "notify": "dm" if u.get("notify") == "dm" else "channel", "items": items})
    return out


def fetch_users(base: str, sync_key: str) -> list[dict[str, Any]]:
    try:
        resp = requests.get(api_endpoint(base), headers=_auth(sync_key), timeout=20)
    except requests.RequestException as exc:  # message may embed the URL: keep only the type
        raise ApiError(f"GET failed ({type(exc).__name__})") from None
    if resp.status_code != 200:
        raise ApiError(f"GET failed (HTTP {resp.status_code})")
    try:
        users = resp.json().get("users")
    except Exception:  # noqa: BLE001
        raise ApiError("GET returned invalid JSON") from None
    if not isinstance(users, list):
        raise ApiError("GET returned no users list")
    return clean_users(users)


def push_updates(base: str, sync_key: str, updates: list[dict[str, Any]]) -> None:
    try:
        resp = requests.post(api_endpoint(base), json={"update": updates}, headers=_auth(sync_key), timeout=20)
    except requests.RequestException as exc:
        raise ApiError(f"POST failed ({type(exc).__name__})") from None
    if not 200 <= resp.status_code < 300:
        raise ApiError(f"POST failed (HTTP {resp.status_code})")


def plan_changes(users: list[Mapping[str, Any]], rows: Mapping[str, Mapping[str, Any]], market: str
                 ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """-> (changes to notify, silent updates).

    A change = {key, item, row|None, previous, current, watchers: [{user_id, notify, name_fix}]}: one per
    (pattern_key, previous, current), so watchers whose last_status differs get their own change. A silent update is
    {user_id, pattern_key, last_status[, name]} (first sight) or {user_id, pattern_key, name} (name filled in)."""
    groups: dict[tuple[str, str, str], dict[str, Any]] = {}
    silent: list[dict[str, Any]] = []
    for user in users:
        uid = user["user_id"]
        for item in user["items"]:
            key = item["pattern_key"]
            if key_market(key) != market:
                continue
            row = rows.get(key)
            current = row["status"] if row else "gone"
            previous = item.get("last_status")
            name_fix = {"name": row["name"]} if (row and market == "tw" and not item.get("name") and row.get("name")) \
                else {}
            if previous is None:  # first sight: record only
                silent.append({"user_id": uid, "pattern_key": key, "last_status": current, "last_notified_at": None,
                               **name_fix})
            elif previous != current:
                g = groups.setdefault((key, previous, current), {
                    "key": key, "item": item, "row": row, "previous": previous, "current": current, "watchers": []})
                if not g["item"].get("name") and item.get("name"):
                    g["item"] = item
                g["watchers"].append({"user_id": uid, "notify": user["notify"], "name_fix": name_fix})
            elif name_fix:
                silent.append({"user_id": uid, "pattern_key": key, **name_fix})
    return list(groups.values()), silent


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
    """The message body shared by every recipient (no content / mentions yet; see addressed())."""
    embed = change_embed(ch)
    msg = nd.Message(payload={"embeds": [embed], "allowed_mentions": {"parse": []}}, keys=[ch["item"]["pattern_key"]])
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


def addressed(base: nd.Message, mention: list[str], dm_failed: list[str]) -> nd.Message:
    """A copy of `base` for the watch channel that @-mentions exactly these users (and nobody else)."""
    msg = nd.Message(payload=copy.deepcopy(base.payload), files=list(base.files), keys=list(base.keys))
    ids = mention + [u for u in dm_failed if u not in mention]
    content = " ".join(f"<@{u}>" for u in mention)
    if dm_failed:
        content = (content + "\n" if content else "") + " ".join(f"<@{u}>" for u in dm_failed) + " " + DM_FAILED_NOTE
    msg.payload["content"] = content[:2000]
    msg.payload["allowed_mentions"] = {"parse": [], "users": ids[:100]}
    return msg


def open_dm(user_id: str, headers: Mapping[str, str]) -> str:
    """DM channel id for user_id; NotifyError(status) on failure (never contains the token)."""
    try:
        resp = requests.post(DM_CHANNEL_URL, json={"recipient_id": user_id}, headers=dict(headers), timeout=20)
    except requests.RequestException as exc:
        raise nd.NotifyError(f"open DM failed ({type(exc).__name__})") from None
    if not 200 <= resp.status_code < 300:
        raise nd.NotifyError(f"open DM failed (HTTP {resp.status_code})", resp.status_code)
    try:
        cid = resp.json().get("id")
    except Exception:  # noqa: BLE001
        cid = None
    if not isinstance(cid, str) or not cid:
        raise nd.NotifyError("open DM returned no channel id")
    return cid


def _chunks(seq: list[str], n: int) -> list[list[str]]:
    return [seq[i:i + n] for i in range(0, len(seq), n)]


def deliver(ch: Mapping[str, Any], base: nd.Message, *, bot_token: str, watch_channel: str | None,
            sleep: Callable[[float], None], pause: float) -> set[str]:
    """Send one change to all its watchers. -> user ids that got it (only they are recorded)."""
    headers = nd.bot_headers(bot_token)
    done: set[str] = set()
    channel_users = [w["user_id"] for w in ch["watchers"] if w["notify"] != "dm"]
    dm_failed: list[str] = []
    first = True

    def send(url: str, msg: nd.Message) -> None:
        nonlocal first
        if not first:
            sleep(pause)
        first = False
        nd.send_message(url, msg, sleep=sleep, pause=pause, headers=headers)

    for w in ch["watchers"]:
        if w["notify"] != "dm":
            continue
        uid = w["user_id"]
        try:
            dm = open_dm(uid, headers)
            send(nd.BOT_MESSAGES_URL.format(channel_id=dm), base)
            done.add(uid)
        except nd.NotifyError as exc:
            if exc.status == 403:  # DMs from server members are off: fall back to an @ in the watch channel
                print(f"[watch] 私訊被拒（HTTP 403），改在關注頻道 @ 對方", file=sys.stderr)
                dm_failed.append(uid)
            else:
                print(f"[watch] 警告：私訊送出失敗，下次重試（{exc}）", file=sys.stderr)

    if channel_users or dm_failed:
        if not watch_channel:
            print(f"[watch] 未設定 DISCORD_WATCH_CHANNEL_ID：{len(channel_users) + len(dm_failed)} 位頻道通知對象略過"
                  "（不記錄，之後補發）", file=sys.stderr)
            return done
        url = nd.BOT_MESSAGES_URL.format(channel_id=watch_channel)
        batches = [(c, []) for c in _chunks(channel_users, MENTIONS_PER_MESSAGE)]
        batches += [([], c) for c in _chunks(dm_failed, MENTIONS_PER_MESSAGE)]
        if len(batches) == 2 and batches[0][0] and not batches[1][0] \
                and len(batches[0][0]) + len(batches[1][1]) <= MENTIONS_PER_MESSAGE:
            batches = [(batches[0][0], batches[1][1])]  # the usual case: channel users + DM failures, one message
        for mention, failed in batches:
            try:
                send(url, addressed(base, mention, failed))
            except nd.NotifyError as exc:
                print(f"[watch] 警告：送出失敗，下次重試（{exc}）", file=sys.stderr)
                continue
            done.update(mention)
            done.update(failed)
    return done


def load_users_file(path: Path) -> list[dict[str, Any]]:
    doc = json.loads(path.read_text(encoding="utf-8"))
    return clean_users(doc.get("users") if isinstance(doc, dict) else doc)


def run(scan_path: Path, *, api_url: str | None, sync_key: str | None, bot_token: str | None,
        watch_channel: str | None, dry_run: bool = False, preview_dir: Path | None = None,
        users_file: Path | None = None, now: datetime | None = None,
        sleep: Callable[[float], None] = time.sleep, pause: float = nd.PAUSE_BETWEEN_MESSAGES,
        image_for: Callable[[Mapping[str, Any]], bytes | None] | None = None) -> int:
    """Always 0 unless the scan file is unreadable: API / Discord problems are warnings only."""
    now = now or datetime.now(timezone.utc)
    scan = json.loads(scan_path.read_text(encoding="utf-8"))
    market = nd.scan_market(scan)
    rows = nd.unique_rows(scan.get("results", []))
    have_api = bool(api_url and sync_key)
    print(f"[watch] market={market}: {len(rows)} patterns in scan; API {'已設定' if have_api else '未設定'}, "
          f"機器人 {'已設定' if bot_token else '未設定'}, 關注頻道 {'已設定' if watch_channel else '未設定'}"
          f"{'; dry-run' if dry_run else ''}")
    if not rows:
        print("[watch] 警告：scan.json 沒有任何型態，略過（避免把全部關注標的誤判為消失）", file=sys.stderr)
        return 0
    try:
        if users_file is not None:
            users = load_users_file(users_file)
        elif have_api:
            users = fetch_users(api_url, sync_key)  # type: ignore[arg-type]
        else:
            print("[watch] 未設定 WATCHLIST_API_URL / WATCHLIST_SYNC_KEY，略過關注通知")
            return 0
    except (ApiError, OSError, ValueError) as exc:
        print(f"[watch] 警告：讀取關注清單失敗，略過（{exc if isinstance(exc, ApiError) else type(exc).__name__}）",
              file=sys.stderr)
        return 0

    n_items = sum(len(u["items"]) for u in users)
    changes, silent = plan_changes(users, rows, market)
    print(f"[watch] {len(users)} 位使用者、關注共 {n_items} 筆；狀態改變 {len(changes)} 組"
          f"（{sum(len(c['watchers']) for c in changes)} 人次），首次記錄/補名 {len(silent)} 筆")
    messages = [(ch, build_change_message(ch, scan_path.parent, image_for)) for ch in changes]

    if dry_run:
        out = preview_dir or DEFAULT_PREVIEW_DIR
        out.mkdir(parents=True, exist_ok=True)
        for ch, m in messages:
            e = m.payload["embeds"][0]
            ch_n = sum(w["notify"] != "dm" for w in ch["watchers"])
            print(f"  {e['title']}  [頻道 @ {ch_n} 人、私訊 {len(ch['watchers']) - ch_n} 人; "
                  f"{'image' if m.files else 'no image'}; button {unwatch_custom_id(ch['key'])[:40]}]")
            for fname, data in m.files:
                (out / f"watch_{fname}").write_bytes(data)
        print(f"[watch] dry-run: nothing sent, API not updated; previews in {out}")
        return 0

    updates = list(silent)
    if messages and not bot_token:
        print("[watch] 未設定 DISCORD_BOT_TOKEN：略過關注通知（不記錄，之後補發）")
        for ch, _ in messages:
            for w in ch["watchers"]:
                if w["name_fix"]:  # still fill the name even when the notification cannot go out
                    updates.append({"user_id": w["user_id"], "pattern_key": ch["key"], **w["name_fix"]})
    elif messages:
        sent = 0
        for i, (ch, m) in enumerate(messages):
            if i:
                sleep(pause)
            done = deliver(ch, m, bot_token=bot_token, watch_channel=watch_channel, sleep=sleep, pause=pause)
            sent += len(done)
            for w in ch["watchers"]:
                if w["user_id"] in done:
                    updates.append({"user_id": w["user_id"], "pattern_key": ch["key"], "last_status": ch["current"],
                                    "last_notified_at": utc_text(now), **w["name_fix"]})
                elif w["name_fix"]:
                    updates.append({"user_id": w["user_id"], "pattern_key": ch["key"], **w["name_fix"]})
        print(f"[watch] notified {sent}/{sum(len(c['watchers']) for c, _ in messages)} watcher(s) "
              f"for {len(messages)} change(s)")
    if updates and have_api and users_file is None:
        try:
            push_updates(api_url, sync_key, updates)  # type: ignore[arg-type]
            print(f"[watch] updated {len(updates)} item(s) in the watchlists")
        except ApiError as exc:
            print(f"[watch] 警告：更新關注清單失敗（{exc}）", file=sys.stderr)
    return 0


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Notify watchers about status changes of the patterns they watch")
    p.add_argument("--scan", default=str(nd.DEFAULT_SCAN), help="scan.json of this run")
    p.add_argument("--dry-run", action="store_true", help="send nothing, do not POST; save previews")
    p.add_argument("--preview-dir", default=str(DEFAULT_PREVIEW_DIR))
    p.add_argument("--users-file", default=None, help="local JSON ({users:[...]}) instead of GET (testing)")
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
                   users_file=Path(args.users_file) if args.users_file else None)
    except Exception as exc:  # noqa: BLE001 - never fail the workflow (D34)
        print(f"[watch] 警告：未預期的錯誤 {type(exc).__name__}，略過", file=sys.stderr)
        return 0


if __name__ == "__main__":
    sys.exit(main())
