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
  * watchlist (右肩已確認) and never notified before (appeared with a confirmed RS) -> "new"
  * confirmed (已突破) and no confirmed notification yet for this key       -> "new" (unseen) / "upgraded"
  forming -> watchlist (the right shoulder got confirmed) is NOT notified.
First run (no state file, or a state older than version 2): one summary message only, every current pattern
becomes the baseline (no per-pattern spam, D20); --notify-all overrides. A message that failed to send leaves
its patterns un-notified, so the next run retries them.

Example:
    python notify_discord.py --dry-run
    DISCORD_WEBHOOK_URL=... python notify_discord.py --min-score 60
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
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
DEFAULT_MIN_SCORE = 50.0  # template forming patterns score ~50-70 (the user's five examples: 53-71)
DEFAULT_STATUSES = "forming,watchlist,confirmed"
STATE_VERSION = 2
MAX_EMBEDS_PER_MESSAGE = 10
MAX_DETAILED = 20
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
EXCHANGE_LABEL = {"binance": "幣安", "okx": "OKX", "gate": "Gate"}
MARKET_LABEL = {"spot": "現貨", "perp": "永續"}


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
    """"new" / "upgraded" / None for one row against its previous state entry."""
    status = row.get("status")
    if status not in statuses or float(row.get("pattern_score") or 0) < min_score:
        return None
    notified = old.get("notified_status") if old else None
    if status == "confirmed":
        if notified == "confirmed":
            return None
        return "new" if old is None else "upgraded"
    if status in ("forming", "watchlist"):
        return "new" if notified is None else None  # forming -> watchlist is not an event
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


def _pct(x: Any, digits: int = 1) -> str:
    if x is None:
        return "-"
    return f"{float(x) * 100:.{digits}f}%"


def market_label(row: Mapping[str, Any]) -> str:
    ex = row.get("exchange") or "binance"
    mk = row.get("market") or "spot"
    return f"{EXCHANGE_LABEL.get(ex, ex)} {MARKET_LABEL.get(mk, mk)}"


def build_embed(event: Mapping[str, Any]) -> dict[str, Any]:
    r = event["row"]
    status = r["status"]
    ref = r.get("current_close")
    side = {"above": "高於", "below": "低於"}.get(r.get("neckline_side"), "距")
    dist = r.get("distance_to_neckline")
    neck = fmt_price(r.get("current_neckline_price"), ref)
    neck_text = f"{neck}（{side}頸線 {float(dist) * 100:.1f}%）" if dist is not None else neck
    est = "" if r.get("breakout_detected") else "（預估）"
    title = f"{r['symbol']} {TF_LABEL.get(r['timeframe'], r['timeframe'])} {STATUS_LABEL.get(status, status)}"
    notes = []
    if status == "forming":
        notes.append("右肩尚未確認，會隨新 K 線變動。")
    if event.get("kind") == "upgraded" and event.get("previous") in ("forming", "watchlist", "stale"):
        notes.append(f"由「{STATUS_LABEL[event['previous']]}」轉為已突破。")
    if not r.get("breakout_detected"):
        notes.append("尚未突破：TP2 以目前頸線價預估。")
    fields = [
        ("交易所", market_label(r)),
        ("分數", f"{float(r['pattern_score']):.1f}"),
        ("目前價", fmt_price(ref)),
        ("頸線價", neck_text),
        ("TP1", fmt_price(r.get("tp1"), ref)),  # D28: fixed, not an estimate
        ("TP2" + est, fmt_price(r.get("tp2"), ref)),
        ("失效價（頭部低點）", fmt_price(r.get("invalidation_price"), ref)),
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


def first_run_text(rows: Mapping[str, Mapping[str, Any]], min_score: float) -> str:
    counts: dict[str, int] = {}
    for r in rows.values():
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    parts = "、".join(f"{STATUS_LABEL.get(s, s)} {counts[s]}" for s in ("forming", "watchlist", "confirmed", "stale")
                     if counts.get(s))
    above = sum(float(r.get("pattern_score") or 0) >= min_score for r in rows.values())
    return (f"開始追蹤，目前 {len(rows)} 個型態" + (f"（{parts}）" if parts else "") +
            f"，其中分數 ≥ {min_score:g} 的 {above} 個。之後只通知新出現的型態與突破。")


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
    rows = unique_rows(results)
    state = load_state(state_path)
    first_run = state is None
    prev = dict(state or {})
    print(f"[notify] {len(results)} patterns in scan ({len(rows)} keys), previous state: "
          f"{'none or old version (first run)' if first_run else str(len(prev)) + ' patterns'}; "
          f"min_score={min_score:g}, statuses={','.join(sorted(statuses))}; "
          f"webhook {'已設定' if webhook else '未設定'}{'; dry-run' if dry_run else ''}")

    baseline = first_run and not notify_all
    if baseline:
        events: list[dict[str, Any]] = []
        messages = [summary_message(first_run_text(rows, min_score))]
    else:
        events = find_events(results, None if first_run else prev, min_score=min_score, statuses=statuses)
        messages = build_messages(events, max_detailed=max_detailed)
    kinds: dict[str, int] = {}
    for e in events:
        label = "confirmed" if e["row"]["status"] == "confirmed" else e["row"]["status"]
        kinds[label] = kinds.get(label, 0) + 1
    print(f"[notify] events: {len(events)} ({', '.join(f'{k} {v}' for k, v in sorted(kinds.items())) or 'none'}); "
          f"{len(messages)} message(s)")

    if dry_run:
        for i, m in enumerate(messages, 1):
            print(f"--- message {i}/{len(messages)} ---")
            print(json.dumps(m, ensure_ascii=False, indent=1))
        print("[notify] dry-run: nothing sent, state not written")
        return 0

    # Which event keys count as notified. Failed sends stay un-notified, so the next run retries them.
    sent_ok = True
    notified: set[str] = set()
    if not webhook:
        print("未設定 webhook，略過通知")
        notified = {e["key"] for e in events}  # D20: not re-sent once a webhook is configured
    else:
        detailed_keys = [e["key"] for e in events[:max_detailed]]
        n_detail_msgs = (len(detailed_keys) + MAX_EMBEDS_PER_MESSAGE - 1) // MAX_EMBEDS_PER_MESSAGE
        for i, m in enumerate(messages):
            try:
                if i:
                    sleep(pause)
                post_message(webhook, m, sleep=sleep)
            except NotifyError as exc:
                sent_ok = False
                print(f"[notify] send failed: {exc}", file=sys.stderr)
                break
            if i < n_detail_msgs:
                notified.update(detailed_keys[i * MAX_EMBEDS_PER_MESSAGE:(i + 1) * MAX_EMBEDS_PER_MESSAGE])
        if sent_ok:
            notified = {e["key"] for e in events}  # incl. the ones only counted in the summary
            print(f"[notify] sent {len(messages)} message(s)")

    new_state: dict[str, Any] = {}
    for key, r in rows.items():
        old = prev.get(key) or {}
        entry = {"status": r["status"], "score": float(r.get("pattern_score") or 0), "last_seen": now_text,
                 "last_notified_at": old.get("last_notified_at"), "notified_status": old.get("notified_status")}
        if baseline:
            entry["last_notified_at"], entry["notified_status"] = now_text, r["status"]
        elif key in notified:
            entry["last_notified_at"], entry["notified_status"] = now_text, r["status"]
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
