"""Compare this scan with the previous state and send new / upgraded patterns to a Discord webhook.

The webhook URL is read ONLY from the DISCORD_WEBHOOK_URL environment variable and is never printed,
logged or written to any file (not even inside error messages).

State file (state.json): {"version": 1, "patterns": {pattern_id: {status, score, last_notified_at, last_seen}}}

Events (a pattern is notified when its score >= --min-score):
  * new:      pattern_id not in state, status in --statuses
  * upgraded: state status was watchlist, now confirmed
First run (no state file): by default only one summary message is sent (no per-pattern spam);
--notify-all overrides. Patterns whose message failed to send are NOT recorded, so the next run retries.

Example:
    python notify_discord.py --dry-run
    DISCORD_WEBHOOK_URL=... python notify_discord.py --min-score 70
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

import requests

ROOT = Path(__file__).resolve().parent
DEFAULT_SCAN = ROOT / "site" / "data" / "scan.json"
DEFAULT_STATE = ROOT / "state.json"
ENV_WEBHOOK = "DISCORD_WEBHOOK_URL"
ENV_MIN_SCORE = "NOTIFY_MIN_SCORE"
STATE_VERSION = 1
MAX_EMBEDS_PER_MESSAGE = 10
MAX_DETAILED = 20
PRUNE_DAYS = 14
MAX_RETRIES = 5
MAX_RETRY_WAIT = 60.0
PAUSE_BETWEEN_MESSAGES = 1.0
COLOR_CONFIRMED = 0xE74C3C  # red
COLOR_WATCHLIST = 0x3498DB  # blue
COLOR_SUMMARY = 0x95A5A6
TV_INTERVAL = {"4h": "240", "1d": "1D", "1w": "1W"}
TF_LABEL = {"4h": "4小時線", "1d": "日線", "1w": "週線"}
STATUS_LABEL = {"confirmed": "已突破", "watchlist": "觀察中"}


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


def load_state(path: Path) -> dict[str, Any] | None:
    """None = no usable state (first run)."""
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
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


def find_events(results: list[Mapping[str, Any]], state: Mapping[str, Any] | None, *,
                min_score: float, statuses: set[str]) -> list[dict[str, Any]]:
    """New or watchlist->confirmed patterns above the score threshold, best score first."""
    prev = state or {}
    events = []
    for r in results:
        if float(r.get("pattern_score") or 0) < min_score:
            continue
        old = prev.get(r["pattern_id"])
        if old is None:
            if r["status"] in statuses:
                events.append({"kind": "new", "row": r})
        elif old.get("status") == "watchlist" and r["status"] == "confirmed" and "confirmed" in statuses:
            events.append({"kind": "upgraded", "row": r})
    events.sort(key=lambda e: -float(e["row"].get("pattern_score") or 0))
    return events


def tradingview_url(symbol: str, timeframe: str) -> str:
    return (f"https://www.tradingview.com/chart/?symbol=BINANCE:{symbol}"
            f"&interval={TV_INTERVAL.get(timeframe, timeframe)}")


def _fmt_price(x: Any) -> str:
    if x is None:
        return "-"
    return f"{float(x):.8g}"


def build_embed(event: Mapping[str, Any]) -> dict[str, Any]:
    r = event["row"]
    status = r["status"]
    side = {"above": "高於", "below": "低於"}.get(r.get("neckline_side"), "距")
    dist = r.get("distance_to_neckline")
    dist_text = f"{side}頸線 {float(dist) * 100:.1f}%" if dist is not None else "-"
    title = f"{r['symbol']} {TF_LABEL.get(r['timeframe'], r['timeframe'])} {STATUS_LABEL.get(status, status)}"
    if event.get("kind") == "upgraded":
        title += "（由觀察中升級）"
    fields = [
        ("分數", f"{float(r['pattern_score']):.1f}"),
        ("距頸線", dist_text),
        ("頸線價", _fmt_price(r.get("current_neckline_price"))),
        ("右肩時間", str(r.get("RS_time") or "-")),
        ("signal_time", str(r.get("signal_time") or "-")),
    ]
    return {
        "title": title,
        "url": tradingview_url(r["symbol"], r["timeframe"]),
        "color": COLOR_CONFIRMED if status == "confirmed" else COLOR_WATCHLIST,
        "fields": [{"name": n, "value": v, "inline": True} for n, v in fields],
    }


def build_messages(events: list[Mapping[str, Any]], *, max_detailed: int = MAX_DETAILED,
                   extra_summary: str | None = None) -> list[dict[str, Any]]:
    """Webhook payloads: <= 10 embeds each; beyond max_detailed events only a summary line."""
    detailed = events[:max_detailed]
    rest = len(events) - len(detailed)
    embeds = [build_embed(e) for e in detailed]
    messages = [{"embeds": embeds[i:i + MAX_EMBEDS_PER_MESSAGE]}
                for i in range(0, len(embeds), MAX_EMBEDS_PER_MESSAGE)]
    notes = []
    if rest > 0:
        notes.append(f"另有 {rest} 個符合條件的型態未列出，請到網站／scan-state 分支查看。")
    if extra_summary:
        notes.append(extra_summary)
    if notes:
        messages.append({"embeds": [{"title": "摘要", "description": "\n".join(notes), "color": COLOR_SUMMARY}]})
    return messages


def summary_message(text: str) -> dict[str, Any]:
    return {"embeds": [{"title": "頭肩底掃描", "description": text, "color": COLOR_SUMMARY}]}


def post_message(url: str, payload: Mapping[str, Any], *, sleep: Callable[[float], None] = time.sleep,
                 max_retries: int = MAX_RETRIES) -> None:
    """POST one webhook payload. 429 waits retry_after; 5xx / network errors back off; bounded retries."""
    last = "unknown error"
    for attempt in range(max_retries + 1):
        try:
            resp = requests.post(url, json=payload, timeout=20)
        except requests.RequestException as exc:  # message may embed the URL: keep only the type
            last = type(exc).__name__
            sleep(min(2.0 ** attempt, MAX_RETRY_WAIT))
            continue
        code = resp.status_code
        if 200 <= code < 300:
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


def run(scan_path: Path, state_path: Path, *, min_score: float, statuses: set[str], notify_all: bool,
        dry_run: bool, prune_days: int = PRUNE_DAYS, max_detailed: int = MAX_DETAILED,
        webhook: str | None = None, now: datetime | None = None,
        sleep: Callable[[float], None] = time.sleep, pause: float = PAUSE_BETWEEN_MESSAGES) -> int:
    now = now or datetime.now(timezone.utc)
    now_text = _utc_text(now)
    scan = json.loads(scan_path.read_text(encoding="utf-8"))
    results = scan.get("results", [])
    state = load_state(state_path)
    first_run = state is None
    prev = dict(state or {})
    print(f"[notify] {len(results)} patterns in scan, previous state: "
          f"{'none (first run)' if first_run else str(len(prev)) + ' patterns'}; "
          f"min_score={min_score:g}, statuses={','.join(sorted(statuses))}; "
          f"webhook {'已設定' if webhook else '未設定'}{'; dry-run' if dry_run else ''}")

    if first_run and not notify_all:
        events: list[dict[str, Any]] = []
        messages = [summary_message(f"開始追蹤，目前 {len(results)} 個型態")]
    else:
        events = find_events(results, None if first_run else prev, min_score=min_score, statuses=statuses)
        messages = build_messages(events, max_detailed=max_detailed)
    new_n = sum(e["kind"] == "new" for e in events)
    print(f"[notify] events: {new_n} new, {len(events) - new_n} upgraded; {len(messages)} message(s)")

    if dry_run:
        for i, m in enumerate(messages, 1):
            print(f"--- message {i}/{len(messages)} ---")
            print(json.dumps(m, ensure_ascii=False, indent=1))
        print("[notify] dry-run: nothing sent, state not written")
        return 0

    # Build the new state. Event patterns are recorded only after a successful send (so they are retried).
    sent_ok = True
    notified: set[str] = set()
    if not webhook:
        print("未設定 webhook，略過通知")
        notified = {e["row"]["pattern_id"] for e in events}
    else:
        detailed_ids = [e["row"]["pattern_id"] for e in events[:max_detailed]]
        n_detail_msgs = (len(detailed_ids) + MAX_EMBEDS_PER_MESSAGE - 1) // MAX_EMBEDS_PER_MESSAGE
        failed_error = ""
        for i, m in enumerate(messages):
            try:
                if i:
                    sleep(pause)
                post_message(webhook, m, sleep=sleep)
            except NotifyError as exc:
                sent_ok = False
                failed_error = str(exc)
                print(f"[notify] send failed: {failed_error}", file=sys.stderr)
                break
            if i < n_detail_msgs:
                notified.update(detailed_ids[i * MAX_EMBEDS_PER_MESSAGE:(i + 1) * MAX_EMBEDS_PER_MESSAGE])
        if sent_ok:
            notified = {e["row"]["pattern_id"] for e in events}  # incl. the ones only counted in the summary
            print(f"[notify] sent {len(messages)} message(s)")

    event_ids = {e["row"]["pattern_id"] for e in events}
    new_state: dict[str, Any] = {}
    for r in results:
        pid = r["pattern_id"]
        old = prev.get(pid)
        entry = {"status": r["status"], "score": float(r["pattern_score"]), "last_seen": now_text,
                 "last_notified_at": old.get("last_notified_at") if old else None}
        if first_run and not notify_all:
            entry["last_notified_at"] = now_text  # baseline
        elif pid in event_ids:
            if pid in notified:
                entry["last_notified_at"] = now_text
            elif old is not None:
                entry["status"] = old.get("status", entry["status"])  # keep old status: upgrade retried
            else:
                continue  # new + not sent: leave out so the next run retries
        new_state[pid] = entry
    # patterns gone from the scan: keep for prune_days (so a flicker does not re-notify), then drop
    cutoff = now - timedelta(days=prune_days)
    for pid, old in prev.items():
        if pid in new_state or pid in {r["pattern_id"] for r in results}:
            continue
        seen = _parse_time(old.get("last_seen"))
        if seen is None or seen >= cutoff:
            new_state[pid] = old
    pruned = len(set(prev) - set(new_state) - {r["pattern_id"] for r in results})
    save_state(state_path, new_state)
    print(f"[notify] state written: {len(new_state)} patterns ({pruned} pruned)")
    return 0 if sent_ok else 1


def parse_args(argv=None) -> argparse.Namespace:
    env_min = os.environ.get(ENV_MIN_SCORE, "").strip()
    p = argparse.ArgumentParser(description="Send new / upgraded IHS patterns to Discord")
    p.add_argument("--scan", default=str(DEFAULT_SCAN), help="scan.json of this run")
    p.add_argument("--state", default=str(DEFAULT_STATE), help="state.json of the previous run (updated)")
    p.add_argument("--min-score", type=float, default=float(env_min) if env_min else 60.0,
                   help=f"minimum pattern_score to notify (default 60; env {ENV_MIN_SCORE})")
    p.add_argument("--statuses", default="watchlist,confirmed",
                   help="statuses that trigger a notification for NEW patterns (upgrades are always confirmed)")
    p.add_argument("--notify-all", action="store_true", help="first run: notify every pattern instead of a summary")
    p.add_argument("--dry-run", action="store_true", help="print what would be sent; send nothing, write no state")
    p.add_argument("--prune-days", type=int, default=PRUNE_DAYS,
                   help="drop state entries not seen for this many days")
    p.add_argument("--max-detailed", type=int, default=MAX_DETAILED, help="max detailed embeds; rest summarised")
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
    return run(scan, Path(args.state), min_score=args.min_score, statuses=statuses, notify_all=args.notify_all,
               dry_run=args.dry_run, prune_days=args.prune_days, max_detailed=args.max_detailed, webhook=webhook)


if __name__ == "__main__":
    sys.exit(main())
