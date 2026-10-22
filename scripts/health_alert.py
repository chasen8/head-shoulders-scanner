"""System health alerts to Discord (charter D41). Python standard library only (the watchdog workflow installs nothing).

Sub-commands (all exit 0 whatever happens, an alert helper must never turn a green workflow red):
  failure      a scan / publish job failed: one message with the failed step names and the run URL (b1)
  scan-health  a build that SUCCEEDED but looks unhealthy: failed-series ratio > threshold, source_errors (b2);
               the same problem is not repeated within 24 h (alert_state*.json kept on the scan-state branch)
  watchdog     nobody scanned for too long / a scan workflow was disabled by GitHub (b3); optional daily heartbeat (b4)

Channel: the bot looks for the text channel named 「測試」 in the server DISCORD_GUILD_ID (override the name with
DISCORD_ALERT_CHANNEL_NAME, or give the channel id directly with DISCORD_ALERT_CHANNEL_ID). Not found / no bot
settings -> only a log line. The bot token, guild id, channel id and GITHUB_TOKEN are read from the environment and are
masked in every printed or sent text; URLs coming from error texts are cut down to scheme + host.

Examples:
    python scripts/health_alert.py failure --market crypto --stage scan
    python scripts/health_alert.py scan-health --market tw --scan site/data/tw/scan.json --state alert_state_tw.json
    python scripts/health_alert.py watchdog --state alert_state_watchdog.json
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

DISCORD_API = "https://discord.com/api/v10"
USER_AGENT = "DiscordBot (https://github.com/chasen8/head-shoulders-scanner, 1.0)"
SITE_BASE = "https://chasen8.github.io/head-shoulders-site/"
DEFAULT_CHANNEL_NAME = "測試"
TPE = timezone(timedelta(hours=8))
TEXT_CHANNEL_TYPES = (0, 5)
MARKET_NAME = {"crypto": "加密貨幣", "tw": "台股"}
SCAN_WORKFLOWS = {"scan-and-notify.yml": "加密貨幣掃描", "scan-tw.yml": "台股掃描"}
CONFIG: dict[str, Any] = {
    "max_failure_ratio": 0.05,   # failed series / series total above this -> warning (build_site itself aborts at 20%)
    "dedupe_hours": 24,          # the same problem is sent at most once per this many hours
    "crypto_max_age_hours": 10,  # crypto scans every 4 h: two missed runs
    "tw_max_age_hours": 30,      # Taiwan scans once per weekday; weekend hours are not counted
    "heartbeat_tpe_hour": 9,     # b4: the daily "all good" message goes out in this Taipei hour (default OFF)
    "state_keep_days": 7,
}
Http = Callable[..., "tuple[int, Any]"]


# ------------------------------------------------------------------ plumbing

def _http(method: str, url: str, headers: Mapping[str, str] | None = None, body: Any = None,
          timeout: float = 20.0) -> tuple[int, Any]:
    """-> (status, parsed JSON or None). status 0 = network failure. Never raises, never echoes the URL."""
    data = json.dumps(body).encode("utf-8") if body is not None else None
    hdrs = {"User-Agent": USER_AGENT, **(headers or {})}
    if data is not None:
        hdrs["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            code = resp.status
    except urllib.error.HTTPError as exc:
        code, raw = exc.code, b""
    except Exception:  # noqa: BLE001 - URLError / timeout / ssl: keep only "failed"
        return 0, None
    try:
        return code, json.loads(raw.decode("utf-8")) if raw else None
    except ValueError:
        return code, None


def mask(text: str, *secrets: str | None) -> str:
    for s in secrets:
        if s and len(s) >= 4:
            text = text.replace(s, "***")
    return text


_URL = re.compile(r"https?://[^\s)>\]\"']+")


def scrub(text: Any, limit: int = 160) -> str:
    """Error text safe to post: URLs reduced to scheme://host, control chars and newlines flattened, shortened."""
    t = _URL.sub(lambda m: re.match(r"https?://[^/?#]+", m.group(0)).group(0) + "/…", str(text))
    t = re.sub(r"\s+", " ", t).strip()
    return t if len(t) <= limit else t[: limit - 1] + "…"


def secrets_of(env: Mapping[str, str]) -> list[str]:
    return [env.get(k, "") for k in ("DISCORD_BOT_TOKEN", "DISCORD_GUILD_ID", "DISCORD_ALERT_CHANNEL_ID",
                                     "GITHUB_TOKEN", "SITE_DEPLOY_KEY")]


def _norm(text: Any) -> str:
    return str(text or "").strip().lstrip("#").strip().lower()


def resolve_channel(env: Mapping[str, str], http: Http | None = None) -> tuple[str | None, str]:
    """-> (channel id or None, log text without any id / token). Order: DISCORD_ALERT_CHANNEL_ID, else the text
    channel called DISCORD_ALERT_CHANNEL_NAME (default 「測試」) in DISCORD_GUILD_ID."""
    http = http or _http
    override = (env.get("DISCORD_ALERT_CHANNEL_ID") or "").strip()
    if override:
        return override, "channel given by DISCORD_ALERT_CHANNEL_ID"
    token = (env.get("DISCORD_BOT_TOKEN") or "").strip()
    guild = (env.get("DISCORD_GUILD_ID") or "").strip()
    name = (env.get("DISCORD_ALERT_CHANNEL_NAME") or "").strip() or DEFAULT_CHANNEL_NAME
    if not token:
        return None, "bot not configured (DISCORD_BOT_TOKEN); alert only logged"
    if not guild:
        return None, f"DISCORD_GUILD_ID not set, cannot find channel 「{name}」; alert only logged"
    code, data = http("GET", f"{DISCORD_API}/guilds/{guild}/channels", {"Authorization": f"Bot {token}"})
    if code != 200 or not isinstance(data, list):
        return None, f"could not read the server's channel list (HTTP {code}); alert only logged"
    want = _norm(name)
    for ch in data:
        if isinstance(ch, dict) and ch.get("type") in TEXT_CHANNEL_TYPES and _norm(ch.get("name")) == want \
                and ch.get("id"):
            return str(ch["id"]), f"found channel 「{name}」"
    return None, f"no text channel named 「{name}」 in the server; alert only logged"


def send_alert(text: str, env: Mapping[str, str], http: Http | None = None) -> bool:
    """Post `text` to the alert channel. True only when Discord accepted it. Always prints a masked log."""
    http = http or _http
    sec = secrets_of(env)
    text = mask(text, *sec)
    channel, why = resolve_channel(env, http)
    print(f"[health] {why}")
    print("[health] message:\n" + "\n".join("  | " + ln for ln in text.splitlines()))
    if channel is None:
        return False
    token = (env.get("DISCORD_BOT_TOKEN") or "").strip()
    if not token:  # channel id given but no bot: nothing to send with
        print("[health] no DISCORD_BOT_TOKEN; not sent")
        return False
    code, _ = http("POST", f"{DISCORD_API}/channels/{channel}/messages", {"Authorization": f"Bot {token}"},
                   {"content": text[:1900], "allowed_mentions": {"parse": []}})
    ok = 200 <= code < 300
    print(f"[health] {'sent' if ok else f'send failed (HTTP {code})'}")
    return ok


# ------------------------------------------------------------------ dedupe state

def load_state(path: Path | str | None) -> dict[str, Any]:
    try:
        d = json.loads(Path(path).read_text(encoding="utf-8"))  # type: ignore[arg-type]
        if isinstance(d, dict) and isinstance(d.get("alerts"), dict):
            return d
    except (OSError, ValueError, TypeError):
        pass
    return {"version": 1, "alerts": {}}


def save_state(path: Path | str, state: Mapping[str, Any]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")


def _parse(ts: Any) -> datetime | None:
    try:
        d = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def is_due(state: Mapping[str, Any], key: str, now: datetime, hours: float | None = None) -> bool:
    """True when `key` was never sent or was last sent more than `hours` ago."""
    last = _parse(state.get("alerts", {}).get(key))
    return last is None or now - last >= timedelta(hours=CONFIG["dedupe_hours"] if hours is None else hours)


def mark_sent(state: dict[str, Any], keys: Sequence[str], now: datetime) -> None:
    for k in keys:
        state.setdefault("alerts", {})[k] = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    cutoff = now - timedelta(days=CONFIG["state_keep_days"])
    state["alerts"] = {k: v for k, v in state["alerts"].items() if (_parse(v) or now) >= cutoff}
    state["version"] = 1


# ------------------------------------------------------------------ b1: failed job

def run_url(env: Mapping[str, str]) -> str | None:
    """https://github.com/<owner/repo>/actions/runs/<id> from the standard Actions variables; None if odd-looking."""
    server = (env.get("GITHUB_SERVER_URL") or "https://github.com").rstrip("/")
    repo, rid = env.get("GITHUB_REPOSITORY", ""), env.get("GITHUB_RUN_ID", "")
    if not re.fullmatch(r"https://[A-Za-z0-9.-]+", server) or not re.fullmatch(r"[\w.-]+/[\w.-]+", repo) \
            or not rid.isdigit():
        return None
    return f"{server}/{repo}/actions/runs/{rid}"


def failed_steps(jobs_doc: Any) -> list[str]:
    """['job / step', ...] of every step whose conclusion is failure (GET .../runs/{id}/jobs)."""
    out = []
    for job in (jobs_doc or {}).get("jobs") or []:
        for st in job.get("steps") or []:
            if st.get("conclusion") == "failure":
                out.append(f"{job.get('name')} / {st.get('name')}")
        if job.get("conclusion") == "failure" and not any(s.get("conclusion") == "failure"
                                                          for s in job.get("steps") or []):
            out.append(f"{job.get('name')}")
    return out


def fetch_failed_steps(env: Mapping[str, str], http: Http | None = None) -> list[str] | None:
    http = http or _http
    token, repo, rid = env.get("GITHUB_TOKEN", ""), env.get("GITHUB_REPOSITORY", ""), env.get("GITHUB_RUN_ID", "")
    if not (token and repo and rid.isdigit()):
        return None
    code, doc = http("GET", f"https://api.github.com/repos/{repo}/actions/runs/{rid}/jobs?per_page=100",
                     {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"})
    return failed_steps(doc) if code == 200 and isinstance(doc, dict) else None


def failure_message(market: str, stage: str, steps: list[str] | None, url: str | None, now: datetime) -> str:
    what = "網站發布失敗" if stage == "publish" else "掃描失敗"
    lines = [f"⚠️ {MARKET_NAME.get(market, market)}{what}"]
    if steps is None:
        lines.append("失敗步驟：（讀不到，請看執行紀錄）")
    elif steps:
        lines.append("失敗步驟：" + "；".join(scrub(s, 80) for s in steps[:5]))
    else:
        lines.append("失敗步驟：（沒有步驟標為失敗，可能是逾時或被取消）")
    if url:
        lines.append(f"執行紀錄：{url}")
    lines.append("時間（台北）：" + now.astimezone(TPE).strftime("%Y-%m-%d %H:%M"))
    if stage == "publish":
        lines.append("舊網站內容不受影響；下一次排程會重新發布。")
    else:
        lines.append("這一輪沒有更新網站與通知；下一次排程會重試。")
    return "\n".join(lines)


# ------------------------------------------------------------------ b2: build ok but unhealthy

def _failed_series(payload: Mapping[str, Any]) -> tuple[int, int, list[str]]:
    fails = payload.get("failures") or []
    series = {(f.get("symbol"), f.get("timeframe")) for f in fails if isinstance(f, dict)}
    total = payload.get("series_total") or ((payload.get("series_scanned") or 0) + len(series))
    by_ex: dict[str, int] = {}
    for f in fails:
        if isinstance(f, dict):
            k = str(f.get("exchange") or f.get("stage") or "?")
            by_ex[k] = by_ex.get(k, 0) + 1
    top = [f"{k} {v}" for k, v in sorted(by_ex.items(), key=lambda kv: -kv[1])[:3]]
    return len(series), int(total or 0), top


def scan_problems(scan: Mapping[str, Any], triangles: Mapping[str, Any] | None = None,
                  max_ratio: float | None = None) -> list[tuple[str, str]]:
    """-> [(dedupe key, one-line text)]: failed-series ratio over the threshold, source_errors, triangle failures."""
    max_ratio = CONFIG["max_failure_ratio"] if max_ratio is None else max_ratio
    out: list[tuple[str, str]] = []
    failed, total, top = _failed_series(scan)
    if total and failed / total > max_ratio:
        first = next((f for f in scan.get("failures") or [] if isinstance(f, dict) and f.get("error")), {})
        out.append(("failures_ratio",
                    f"資料抓取／偵測失敗 {failed}/{total} 條序列（{failed / total:.1%}，門檻 {max_ratio:.0%}）"
                    + (f"；集中在 {'、'.join(top)}" if top else "")
                    + (f"；例：{scrub(first.get('error'))}" if first else "")))
    for e in scan.get("source_errors") or []:
        if isinstance(e, dict):
            out.append((f"source_error:{e.get('source')}",
                        f"資料來源 {scrub(e.get('source'), 40)} 異常（{scrub(e.get('stage') or '?', 20)}）："
                        f"{scrub(e.get('error'))}"))
    if triangles:
        tf, tt_, _ = _failed_series(triangles)
        if tt_ and tf / tt_ > max_ratio:
            out.append(("tri_failures_ratio", f"收斂三角偵測失敗 {tf}/{tt_} 條序列（{tf / tt_:.1%}）"))
    return out


def cmd_scan_health(args: argparse.Namespace, env: Mapping[str, str], now: datetime, http: Http | None) -> int:
    def load(p: str | None) -> dict | None:
        try:
            return json.loads(Path(p).read_text(encoding="utf-8")) if p else None  # type: ignore[arg-type]
        except (OSError, ValueError):
            return None
    scan = load(args.scan)
    if scan is None:
        print(f"[health] scan file unreadable; nothing checked")
        return 0
    tri_path = args.triangles or str(Path(args.scan).with_name("triangles.json"))
    problems = scan_problems(scan, load(tri_path), args.max_ratio)
    state = load_state(args.state)
    keyed = [(f"scan-health:{args.market}:{k}", t) for k, t in problems]
    due = [(k, t) for k, t in keyed if is_due(state, k, now)]
    print(f"[health] scan-health {args.market}: {len(problems)} problem(s), {len(due)} due "
          f"({len(problems) - len(due)} repeated within {CONFIG['dedupe_hours']} h)")
    if due:
        text = f"⚠️ {MARKET_NAME.get(args.market, args.market)}掃描有異常（掃描有完成，結果可能不完整）\n" + \
            "\n".join("・" + t for _, t in due)
        url = run_url(env)
        if url:
            text += f"\n執行紀錄：{url}"
        if send_alert(text, env, http):
            mark_sent(state, [k for k, _ in due], now)
    if args.state:
        save_state(args.state, state)
    return 0


# ------------------------------------------------------------------ b3 / b4: watchdog

def business_age_hours(t0: datetime, t1: datetime) -> float:
    """Hours from t0 to t1 minus the Saturday / Sunday (UTC) hours in between."""
    if t1 <= t0:
        return 0.0
    total = 0.0
    cur = t0
    while cur < t1:
        nxt = min((cur + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0), t1)
        if cur.weekday() < 5:
            total += (nxt - cur).total_seconds()
        cur = nxt
    return total / 3600


def stale_problem(market: str, generated: datetime | None, now: datetime) -> tuple[str, str] | None:
    """Crypto: older than crypto_max_age_hours. Taiwan: weekend-free age above tw_max_age_hours, and no check at all
    while it is a weekend in Taipei. A public holiday still has a scan (the workflow runs, only the bars repeat),
    so holidays do not trigger this; a disabled / failed workflow does."""
    name = MARKET_NAME[market]
    if generated is None:
        return f"unreadable:{market}", f"讀不到{name}的 scan.json 或裡面沒有 generated_at（網站或發布異常？）"
    if market == "tw":
        if now.astimezone(TPE).weekday() >= 5:
            return None
        age, limit = business_age_hours(generated, now), CONFIG["tw_max_age_hours"]
        note = "（不含週末）"
    else:
        age, limit = (now - generated).total_seconds() / 3600, CONFIG["crypto_max_age_hours"]
        note = ""
    if age <= limit:
        return None
    return (f"stale:{market}", f"{name}已經 {age:.0f} 小時{note}沒有更新（上次 "
            f"{generated.astimezone(TPE).strftime('%Y-%m-%d %H:%M')} 台北，門檻 {limit} 小時）")


def workflow_problems(doc: Any) -> list[tuple[str, str]]:
    out = []
    by_path = {Path(str(w.get("path", ""))).name: w for w in (doc or {}).get("workflows") or [] if isinstance(w, dict)}
    for fname, label in SCAN_WORKFLOWS.items():
        w = by_path.get(fname)
        if w is None:
            out.append((f"workflow:{fname}", f"找不到 {label} workflow（{fname}）"))
        elif w.get("state") != "active":
            out.append((f"workflow:{fname}", f"{label} workflow（{fname}）被停用：狀態 {w.get('state')}"
                        "（公開 repo 60 天沒有提交活動會被 GitHub 自動停用；到 Actions 頁面按 Enable）"))
    return out


def watchdog_problems(now: datetime, generated: Mapping[str, datetime | None],
                      workflows_doc: Any = None) -> list[tuple[str, str]]:
    out = [p for m in ("crypto", "tw") if (p := stale_problem(m, generated.get(m), now))]
    if workflows_doc is not None:
        out += workflow_problems(workflows_doc)
    return out


def fetch_generated(path: str, base: str, http: Http | None = None) -> tuple[datetime | None, dict | None]:
    http = http or _http
    code, doc = http("GET", f"{base.rstrip('/')}/{path}?t={int(datetime.now().timestamp())}",
                     {"Cache-Control": "no-cache"})
    if code == 200 and isinstance(doc, dict):
        return _parse(doc.get("generated_at")), doc
    return None, None


def heartbeat_text(generated: Mapping[str, datetime | None], docs: Mapping[str, dict | None]) -> str:
    def when(m: str) -> str:
        g = generated.get(m)
        return g.astimezone(TPE).strftime("%m-%d %H:%M") if g else "未知"

    def n(m: str) -> str:
        d = docs.get(m)
        return str(len(d.get("results") or [])) if isinstance(d, dict) else "?"
    return (f"✅ 系統正常：加密上次更新 {when('crypto')}（目前 {n('crypto')} 個型態）、"
            f"台股上次更新 {when('tw')}（目前 {n('tw')} 個型態）。時間為台北時間。")


def cmd_watchdog(args: argparse.Namespace, env: Mapping[str, str], now: datetime, http: Http | None) -> int:
    http = http or _http
    base = args.site_url or SITE_BASE
    gen: dict[str, datetime | None] = {}
    docs: dict[str, dict | None] = {}
    gen["crypto"], docs["crypto"] = fetch_generated("data/scan.json", base, http)
    gen["tw"], docs["tw"] = fetch_generated("data/tw/scan.json", base, http)
    wf_doc = None
    token, repo = env.get("GITHUB_TOKEN", ""), env.get("GITHUB_REPOSITORY", "")
    if token and repo:
        code, d = http("GET", f"https://api.github.com/repos/{repo}/actions/workflows?per_page=100",
                       {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"})
        wf_doc = d if code == 200 and isinstance(d, dict) else None
        if wf_doc is None:
            print(f"[health] could not list workflows (HTTP {code}); the disabled-workflow check is skipped")
    problems = watchdog_problems(now, gen, wf_doc)
    state = load_state(args.state)
    keyed = [(f"watchdog:{k}", t) for k, t in problems]
    due = [(k, t) for k, t in keyed if is_due(state, k, now)]
    print(f"[health] watchdog: crypto generated_at={gen['crypto']}, tw generated_at={gen['tw']}; "
          f"{len(problems)} problem(s), {len(due)} due, {len(problems) - len(due)} repeated within "
          f"{CONFIG['dedupe_hours']} h" + ("" if problems else " -> all good"))
    for _k, t in keyed:
        print(f"[health] problem: {t}")
    if due:
        text = "⚠️ 掃描看門狗：系統可能沒有正常運作\n" + "\n".join("・" + t for _, t in due)
        url = f"https://github.com/{repo}/actions" if repo else ""
        if url:
            text += f"\n到 Actions 頁面看：{url}"
        if send_alert(text, env, http):
            mark_sent(state, [k for k, _ in due], now)
    elif not problems and (args.heartbeat == "force" or (
            args.heartbeat == "auto" and now.astimezone(TPE).hour == CONFIG["heartbeat_tpe_hour"])):
        send_alert(heartbeat_text(gen, docs), env, http)
    elif not problems:
        print("[health] heartbeat off (default)" if args.heartbeat == "off" else "[health] not heartbeat time")
    save_state(args.state, state)
    return 0


# ------------------------------------------------------------------ CLI

def cmd_failure(args: argparse.Namespace, env: Mapping[str, str], now: datetime, http: Http | None) -> int:
    steps = fetch_failed_steps(env, http)
    send_alert(failure_message(args.market, args.stage, steps, run_url(env), now), env, http)
    return 0


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="System health alerts to Discord (D41)")
    sub = p.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("failure", help="a workflow job failed")
    f.add_argument("--market", choices=sorted(MARKET_NAME), required=True)
    f.add_argument("--stage", choices=("scan", "publish"), default="scan")
    s = sub.add_parser("scan-health", help="build succeeded but failures / source errors are high")
    s.add_argument("--market", choices=sorted(MARKET_NAME), required=True)
    s.add_argument("--scan", required=True)
    s.add_argument("--triangles", default=None)
    s.add_argument("--state", default=None, help="alert_state json (dedupe); kept on scan-state")
    s.add_argument("--max-ratio", type=float, default=None)
    w = sub.add_parser("watchdog", help="no recent successful scan / disabled workflow")
    w.add_argument("--state", default="alert_state_watchdog.json")
    w.add_argument("--site-url", default=None)
    w.add_argument("--heartbeat", choices=("off", "auto", "force"), default="off",
                   help="b4: daily all-good message (default off)")
    return p.parse_args(argv)


def main(argv: Sequence[str] | None = None, *, env: Mapping[str, str] | None = None, http: Http | None = None,
         now: datetime | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    args = parse_args(argv)
    env = os.environ if env is None else env
    now = now or datetime.now(timezone.utc)
    cmd = {"failure": cmd_failure, "scan-health": cmd_scan_health, "watchdog": cmd_watchdog}[args.cmd]
    try:
        return cmd(args, env, now, http)
    except Exception as exc:  # noqa: BLE001 - never fail the workflow because of the alert helper
        print(f"[health] {args.cmd} failed: {type(exc).__name__}: {mask(str(exc)[:160], *secrets_of(env))}",
              file=sys.stderr)
        return 0


if __name__ == "__main__":
    sys.exit(main())
