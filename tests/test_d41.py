"""D41: track-record data, its publishing, and the health alerts (no network: everything is faked)."""
from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from ihs_scanner import track_record as tr

ROOT = Path(__file__).resolve().parents[1]


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


ha = _load("health_alert")
mp = _load("merge_published")
UTC = timezone.utc


# ------------------------------------------------------------------ track record

def item(pid, status="open", *, kind="ihs", tf="1d", events=(), bp=100.0, tp1=110.0, tp2=120.0, inv=90.0, **kw):
    d = {"pattern_id": pid, "kind": kind, "pattern_label": "頭肩底", "symbol": pid, "name": "", "exchange": "binance",
         "market": "spot", "timeframe": tf, "breakout_time": "2026-10-20T00:00:00Z", "breakout_price": bp, "tp1": tp1,
         "tp2": tp2, "invalidation_price": inv, "status": status, "events": list(events),
         "added_at": "2026-10-22T05:00:00Z", "bars_since_breakout": 3, "last_bar_time": "2026-10-23T00:00:00Z"}
    d.update(kw)
    return d


def ev(t, price, bars=2, time="2026-10-22T00:00:00Z", reason=None):
    return {"type": t, "time": time, "price": price, "bars": bars, "silent_reason": reason}


def test_row_returns_by_status():
    rows = {
        "tp2": tr.make_row(item("a", "tp2", events=[ev("tp1", 110), ev("tp2", 120)])),
        "tp1": tr.make_row(item("b", "tp1", events=[ev("tp1", 110)])),
        "failed": tr.make_row(item("c", "failed", events=[ev("failed", 90)])),
        "expired": tr.make_row(item("d", "expired", events=[ev("expired", 104)])),
        "open": tr.make_row(item("e", "open", last_close=95.0)),
    }
    assert rows["tp2"]["return"] == pytest.approx(0.20) and rows["tp2"]["had_tp1"]
    assert rows["tp1"]["return"] == pytest.approx(0.10) and rows["tp1"]["result_basis"] == "tp1"
    assert rows["failed"]["return"] == pytest.approx(-0.10)
    assert rows["expired"]["return"] == pytest.approx(0.04) and rows["expired"]["result_basis"] == "expired"
    assert rows["open"]["return"] == pytest.approx(-0.05) and rows["open"]["result_basis"] == "last_close"
    assert rows["tp2"]["days"] == pytest.approx(2.0) and rows["tp2"]["bars"] == 2


def test_old_tracker_items_are_tolerated():
    old = item("x", "open")  # no last_close, no silent_reason, no triangle_type
    row = tr.make_row(old)
    assert row["return"] is None and row["result_basis"] is None and row["retro"] is False
    exp = item("y", "expired", events=[{"type": "expired", "time": "2026-10-25T00:00:00Z", "bars": 5}])
    assert tr.make_row(exp)["return"] is None  # unknown, not 0
    doc = tr.build({"x": old, "y": exp}, market="crypto")
    assert doc["summary"]["all"][0]["n_return"] == 0 and doc["summary"]["all"][0]["avg_return"] is None


def test_summary_denominator_and_groups():
    items = {
        "1": item("1", "tp2", events=[ev("tp1", 110), ev("tp2", 120)]),
        "2": item("2", "failed", events=[ev("tp1", 110), ev("failed", 90)]),   # TP1 first, then stopped
        "3": item("3", "failed", events=[ev("failed", 90)], tf="1w"),
        "4": item("4", "expired", events=[ev("expired", 100)], kind="triangle", triangle_type="ascending"),
        "5": item("5", "tp1", events=[ev("tp1", 110)]),
        "6": item("6", "open"),                                                # in no rate
    }
    doc = tr.build(items, market="crypto")
    total = doc["summary"]["all"][0]
    assert (total["kind"], total["timeframe"]) == (None, None)
    assert total["n"] == 5 and total["open"] == 1
    assert total["tp1"] == 3 and total["tp1_rate"] == pytest.approx(3 / 5)   # tp2 + failed-after-tp1 + tp1
    assert total["tp2_rate"] == pytest.approx(1 / 5) and total["failed_rate"] == pytest.approx(2 / 5)
    assert total["failed_after_tp1"] == 1 and total["expired_rate"] == pytest.approx(1 / 5)
    # avg of +20, -10, -10, 0, +10 percent = +2%
    assert total["avg_return"] == pytest.approx(0.02)
    keys = {(g["kind"], g["timeframe"]) for g in doc["summary"]["all"]}
    assert ("ihs", "1d") in keys and ("triangle", None) in keys and (None, "1w") in keys
    assert ("triangle", "4h") not in keys


def test_retro_and_anomaly_are_kept_out_of_live_summary():
    items = {
        "r": item("r", "failed", events=[ev("failed", 90, reason="first_run")]),
        "s": item("s", "tp1", events=[ev("tp1", 110, reason="before_signal")]),
        "live": item("live", "tp2", events=[ev("tp1", 110), ev("tp2", 120)]),
        "bad": item("bad", "failed", kind="triangle", inv=105.0, events=[ev("failed", 105)]),  # inv above breakout
    }
    doc = tr.build(items, market="tw")
    assert doc["counts"]["retro"] == 2 and doc["counts"]["anomaly"] == 1
    assert doc["summary"]["live"][0]["n"] == 1
    assert doc["summary"]["all"][0]["n"] == 3  # anomaly never counted
    bad = next(r for r in doc["items"] if r["pattern_id"] == "bad")
    assert bad["anomaly"] and bad["return"] is None
    assert doc["tracking_since"] == "2026-10-22T05:00:00Z"


def test_write_from_tracker(tmp_path):
    f = tmp_path / "tp_tracker.json"
    f.write_text(json.dumps({"version": 1, "updated_at": "2026-10-22T05:00:00Z",
                             "items": {"a": item("a", "open")}}), encoding="utf-8")
    out = tmp_path / "site" / "data" / "track_record.json"
    assert tr.write_from_tracker(f, out, "crypto")
    d = json.loads(out.read_text(encoding="utf-8"))
    assert d["market"] == "crypto" and d["items"][0]["pattern_id"] == "a" and d["tracker_updated_at"]
    assert not tr.write_from_tracker(tmp_path / "missing.json", tmp_path / "x.json", "tw")
    f.write_text("{bad", encoding="utf-8")
    assert not tr.write_from_tracker(f, tmp_path / "y.json", "tw")


def test_tracker_evaluate_records_last_close():
    import pandas as pd
    from ihs_scanner import tp_tracker as tt
    t0 = pd.Timestamp("2026-10-20", tz="UTC")
    bars = pd.DataFrame({"timestamp": [t0, t0 + pd.Timedelta(days=1)], "open": [100, 101], "high": [101, 102],
                         "low": [99, 100], "close": [100.5, 101.5], "volume": [1, 1],
                         "close_time": [t0 + pd.Timedelta(hours=23), t0 + pd.Timedelta(days=1, hours=23)]})
    it = item("z", "open")
    res = tt.evaluate(it, bars, now=datetime(2026, 10, 23, tzinfo=UTC))
    assert res["status"] == "open" and res["last_close"] == pytest.approx(101.5)


# ------------------------------------------------------------------ publishing

def _write(root: Path, rel: str, text: str = "x"):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def test_publish_keeps_both_markets_track_records(tmp_path):
    prev, new, out = tmp_path / "prev", tmp_path / "new", tmp_path / "out"
    _write(prev, "data/track_record.json", "crypto-old")
    _write(prev, "data/tw/track_record.json", "tw-old")
    _write(prev, "data/scan.json", "c")
    _write(new, "data/tw/track_record.json", "tw-new")      # a TW run
    _write(new, "data/tw/scan.json", "t")
    _write(new, "data/track_record.json", "STALE-crypto")    # stale copy from a checkout: must be dropped
    mp.merge_tree(new, prev, out, "tw", mp.OWNED["site"], mp.KEEP_IF_ABSENT["site"]["tw"])
    assert (out / "data/tw/track_record.json").read_text(encoding="utf-8") == "tw-new"
    assert (out / "data/track_record.json").read_text(encoding="utf-8") == "crypto-old"
    # crypto run
    new2, out2 = tmp_path / "new2", tmp_path / "out2"
    _write(new2, "data/track_record.json", "crypto-new")
    _write(new2, "data/scan.json", "c2")
    mp.merge_tree(new2, prev, out2, "crypto", mp.OWNED["site"], mp.KEEP_IF_ABSENT["site"]["crypto"])
    assert (out2 / "data/track_record.json").read_text(encoding="utf-8") == "crypto-new"
    assert (out2 / "data/tw/track_record.json").read_text(encoding="utf-8") == "tw-old"


def test_publish_keeps_previous_record_when_run_has_none(tmp_path):
    prev, new, out = tmp_path / "prev", tmp_path / "new", tmp_path / "out"
    _write(prev, "data/track_record.json", "crypto-old")
    _write(new, "data/scan.json", "c2")  # notify_tp step skipped: no track_record.json this run
    mp.merge_tree(new, prev, out, "crypto", mp.OWNED["site"], mp.KEEP_IF_ABSENT["site"]["crypto"])
    assert (out / "data/track_record.json").read_text(encoding="utf-8") == "crypto-old"
    # the scan-state tree: tracker, record and alert state of the publishing market are all kept
    sprev, snew, sout = tmp_path / "sp", tmp_path / "sn", tmp_path / "so"
    for rel in ("tp_tracker_tw.json", "site/data/tw/track_record.json", "alert_state_tw.json", "state.json"):
        _write(sprev, rel, "old")
    _write(snew, "state_tw.json", "new")
    mp.merge_tree(snew, sprev, sout, "tw", mp.OWNED["state"], mp.KEEP_IF_ABSENT["state"]["tw"])
    for rel in ("tp_tracker_tw.json", "site/data/tw/track_record.json", "alert_state_tw.json", "state.json"):
        assert (sout / rel).read_text(encoding="utf-8") == "old", rel


# ------------------------------------------------------------------ health_alert

TOKEN, GUILD, CHAN = "tok-SECRET-123456", "9999888877776666", "5555444433332222"


class FakeHttp:
    def __init__(self, channels=None, send_code=200, jobs=None, workflows=None, site=None):
        self.channels = channels if channels is not None else [{"id": CHAN, "name": "測試", "type": 0}]
        self.send_code, self.jobs, self.workflows, self.site = send_code, jobs, workflows, site or {}
        self.posts: list = []

    def __call__(self, method, url, headers=None, body=None, timeout=20):
        if method == "POST":
            self.posts.append((url, body))
            return self.send_code, {}
        if "/guilds/" in url:
            return 200, self.channels
        if url.endswith("/jobs?per_page=100"):
            return 200, self.jobs
        if "/actions/workflows" in url:
            return 200, self.workflows
        for path, doc in self.site.items():
            if path in url:
                return 200, doc
        return 404, None


ENV = {"DISCORD_BOT_TOKEN": TOKEN, "DISCORD_GUILD_ID": GUILD, "GITHUB_TOKEN": "ghs_SECRETTOKEN",
       "GITHUB_REPOSITORY": "chasen8/head-shoulders-scanner", "GITHUB_RUN_ID": "123456789",
       "GITHUB_SERVER_URL": "https://github.com"}


def test_channel_resolution_by_name_and_override():
    assert ha.resolve_channel(ENV, FakeHttp())[0] == CHAN
    chans = [{"id": "1", "name": "測試", "type": 2}, {"id": "2", "name": "#測試", "type": 0}]
    assert ha.resolve_channel(ENV, FakeHttp(chans))[0] == "2"  # voice channel skipped, leading # ignored
    other = {"id": "7", "name": "alerts", "type": 0}
    assert ha.resolve_channel({**ENV, "DISCORD_ALERT_CHANNEL_NAME": "alerts"}, FakeHttp([other]))[0] == "7"
    assert ha.resolve_channel({**ENV, "DISCORD_ALERT_CHANNEL_ID": "42"}, FakeHttp([]))[0] == "42"
    cid, why = ha.resolve_channel(ENV, FakeHttp([{"id": "1", "name": "general", "type": 0}]))
    assert cid is None and "測試" in why
    assert ha.resolve_channel({}, FakeHttp())[0] is None
    assert ha.resolve_channel({"DISCORD_BOT_TOKEN": TOKEN}, FakeHttp())[0] is None
    for why in (ha.resolve_channel(ENV, FakeHttp([]))[1], ha.resolve_channel({"DISCORD_BOT_TOKEN": TOKEN}, FakeHttp())[1]):
        assert TOKEN not in why and GUILD not in why


def test_failure_message_has_run_url_steps_and_no_secrets(capsys):
    jobs = {"jobs": [{"name": "scan", "conclusion": "failure", "steps": [
        {"name": "Run tests", "conclusion": "success"},
        {"name": f"Scan {TOKEN} https://x.example/api?key=abc123", "conclusion": "failure"}]}]}
    fake = FakeHttp(jobs=jobs)
    rc = ha.main(["failure", "--market", "crypto", "--stage", "scan"], env=ENV, http=fake,
                 now=datetime(2026, 10, 23, 1, 0, tzinfo=UTC))
    assert rc == 0 and len(fake.posts) == 1
    url, body = fake.posts[0]
    text = body["content"]
    assert text.startswith("⚠️ 加密貨幣掃描失敗")
    assert "https://github.com/chasen8/head-shoulders-scanner/actions/runs/123456789" in text
    assert "Scan" in text
    for secret in (TOKEN, GUILD, CHAN, "ghs_SECRETTOKEN"):
        assert secret not in text
    assert "abc123" not in text
    assert TOKEN not in text and "key=abc123" not in text
    assert CHAN in url  # the channel id is only in the API path, never in the message


def test_failure_titles_and_odd_run_url():
    now = datetime(2026, 10, 23, tzinfo=UTC)
    assert ha.failure_message("tw", "publish", [], None, now).startswith("⚠️ 台股網站發布失敗")
    assert "讀不到" in ha.failure_message("tw", "scan", None, None, now)
    assert ha.run_url({**ENV, "GITHUB_RUN_ID": "12;rm"}) is None
    assert ha.run_url({**ENV, "GITHUB_REPOSITORY": "a b"}) is None


def test_no_channel_only_logs(capsys):
    fake = FakeHttp([])
    ha.main(["failure", "--market", "tw"], env=ENV, http=fake)
    assert fake.posts == []
    out = capsys.readouterr().out
    assert "alert only logged" in out and TOKEN not in out


def _scan(failed, total, errors=()):
    return {"series_total": total, "series_scanned": total - failed,
            "failures": [{"symbol": f"S{i}", "timeframe": "1d", "stage": "fetch", "exchange": "okx",
                          "error": "HTTP 500 for https://www.okx.com/api/v5/x?sign=SECRET"} for i in range(failed)],
            "source_errors": list(errors)}


def test_failure_ratio_threshold():
    assert ha.scan_problems(_scan(5, 100)) == []                      # exactly 5% is fine
    p = ha.scan_problems(_scan(6, 100))
    assert [k for k, _ in p] == ["failures_ratio"] and "6/100" in p[0][1]
    assert "SECRET" not in p[0][1] and "okx.com" in p[0][1]          # URL cut down to the host
    assert ha.scan_problems(_scan(6, 100), max_ratio=0.10) == []
    p = ha.scan_problems(_scan(0, 100, [{"source": "okx-perp", "stage": "list", "error": "boom"}]))
    assert p[0][0] == "source_error:okx-perp"
    assert ha.scan_problems({"series_total": 0, "failures": []}) == []


def test_dedupe_24h(tmp_path):
    now = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)
    state = ha.load_state(tmp_path / "none.json")
    assert ha.is_due(state, "k", now)
    ha.mark_sent(state, ["k"], now)
    assert not ha.is_due(state, "k", now + timedelta(hours=23, minutes=59))
    assert ha.is_due(state, "k", now + timedelta(hours=24))
    ha.save_state(tmp_path / "s.json", state)
    assert not ha.is_due(ha.load_state(tmp_path / "s.json"), "k", now + timedelta(hours=1))


def test_scan_health_sends_once_per_day(tmp_path):
    scan = tmp_path / "scan.json"
    scan.write_text(json.dumps(_scan(30, 100)), encoding="utf-8")
    st = tmp_path / "alert_state.json"
    fake = FakeHttp()
    t0 = datetime(2026, 10, 23, 1, 0, tzinfo=UTC)
    argv = ["scan-health", "--market", "crypto", "--scan", str(scan), "--state", str(st)]
    ha.main(argv, env=ENV, http=fake, now=t0)
    ha.main(argv, env=ENV, http=fake, now=t0 + timedelta(hours=4))
    assert len(fake.posts) == 1 and "30/100" in fake.posts[0][1]["content"]
    ha.main(argv, env=ENV, http=fake, now=t0 + timedelta(hours=25))
    assert len(fake.posts) == 2
    # a failed send is not recorded: it is retried next time
    st2 = tmp_path / "s2.json"
    bad = FakeHttp(send_code=500)
    ha.main(["scan-health", "--market", "tw", "--scan", str(scan), "--state", str(st2)], env=ENV, http=bad, now=t0)
    ha.main(["scan-health", "--market", "tw", "--scan", str(scan), "--state", str(st2)], env=ENV, http=bad, now=t0)
    assert len(bad.posts) == 2


def test_business_age_skips_weekend():
    fri = datetime(2026, 10, 23, 6, 10, tzinfo=UTC)      # a Friday
    mon = datetime(2026, 10, 26, 6, 0, tzinfo=UTC)
    assert fri.weekday() == 4 and mon.weekday() == 0
    assert ha.business_age_hours(fri, mon) < 30
    assert ha.business_age_hours(fri, fri + timedelta(hours=5)) == pytest.approx(5)


def test_watchdog_time_rules():
    now_wed = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)  # Wednesday
    fresh = now_wed - timedelta(hours=3)
    assert ha.stale_problem("crypto", fresh, now_wed) is None
    assert ha.stale_problem("crypto", now_wed - timedelta(hours=10, minutes=1), now_wed)[0] == "stale:crypto"
    assert ha.stale_problem("crypto", None, now_wed)[0] == "unreadable:crypto"
    assert ha.stale_problem("tw", now_wed - timedelta(hours=29), now_wed) is None
    assert ha.stale_problem("tw", now_wed - timedelta(hours=31), now_wed)[0] == "stale:tw"
    # Monday morning after a normal Friday scan: the weekend does not count
    mon = datetime(2026, 10, 26, 3, 0, tzinfo=UTC)
    fri_scan = datetime(2026, 10, 23, 6, 10, tzinfo=UTC)
    assert ha.stale_problem("tw", fri_scan, mon) is None
    # Tuesday evening with no Monday scan: warn
    tue = datetime(2026, 10, 27, 12, 0, tzinfo=UTC)
    assert ha.stale_problem("tw", fri_scan, tue)[0] == "stale:tw"
    # Saturday / Sunday (Taipei): never a Taiwan warning, even if very old
    sat = datetime(2026, 10, 24, 3, 0, tzinfo=UTC)
    assert ha.stale_problem("tw", now_wed - timedelta(days=9), sat) is None
    # crypto does not care about weekends
    assert ha.stale_problem("crypto", sat - timedelta(hours=11), sat)[0] == "stale:crypto"


def test_disabled_workflow_detected():
    doc = {"workflows": [{"path": ".github/workflows/scan-and-notify.yml", "state": "active"},
                         {"path": ".github/workflows/scan-tw.yml", "state": "disabled_inactivity"}]}
    p = ha.workflow_problems(doc)
    assert [k for k, _ in p] == ["workflow:scan-tw.yml"] and "disabled_inactivity" in p[0][1]
    assert ha.workflow_problems({"workflows": []})[0][0].startswith("workflow:")


def test_watchdog_command_alerts_then_stays_quiet(tmp_path):
    now = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)
    site = {"data/tw/scan.json": {"generated_at": (now - timedelta(hours=2)).isoformat(), "results": []},
            "data/scan.json": {"generated_at": (now - timedelta(hours=12)).isoformat(), "results": []}}
    fake = FakeHttp(site=site, workflows={"workflows": [
        {"path": ".github/workflows/scan-and-notify.yml", "state": "active"},
        {"path": ".github/workflows/scan-tw.yml", "state": "active"}]})
    st = tmp_path / "w.json"
    argv = ["watchdog", "--state", str(st), "--site-url", "https://site.example/"]
    ha.main(argv, env=ENV, http=fake, now=now)
    ha.main(argv, env=ENV, http=fake, now=now + timedelta(hours=3))
    assert len(fake.posts) == 1 and "加密貨幣已經" in fake.posts[0][1]["content"]
    assert TOKEN not in fake.posts[0][1]["content"]


def test_heartbeat_default_off_and_forced(tmp_path):
    now = datetime(2026, 10, 21, 1, 23, tzinfo=UTC)  # 09:23 Taipei
    doc = {"generated_at": (now - timedelta(hours=1)).isoformat(), "results": [1, 2]}
    fake = FakeHttp(site={"data/tw/scan.json": doc, "data/scan.json": doc})
    st = str(tmp_path / "h.json")
    ha.main(["watchdog", "--state", st], env=ENV, http=fake, now=now)  # default: off
    assert fake.posts == []
    ha.main(["watchdog", "--state", st, "--heartbeat", "auto"], env=ENV, http=fake, now=now)
    assert len(fake.posts) == 1 and "✅ 系統正常" in fake.posts[0][1]["content"]
    ha.main(["watchdog", "--state", st, "--heartbeat", "auto"], env=ENV, http=fake, now=now.replace(hour=5))
    assert len(fake.posts) == 1  # not 09:00 Taipei
