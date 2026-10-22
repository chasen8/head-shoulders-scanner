"""Track-record page data (charter D41): the real results of the take-profit tracker (D39) as one JSON file per market.

    tp_tracker.json      -> site/data/track_record.json       (crypto)
    tp_tracker_tw.json   -> site/data/tw/track_record.json    (Taiwan)

The front end only draws what is in the file (D17): every return / rate / duration is computed here.

Per item (one tracked breakout):
  status       open | tp1 | tp2 | failed | expired   (tp1 = TP1 reached, still tracking TP2)
  result       the point that decides the item: tp2 -> the TP2 event, failed -> the invalidation event (price =
               invalidation price), expired -> the expiry bar's close, tp1 -> the TP1 event, open -> the latest
               closed bar the tracker saw (`last_close`, only when the tracker recorded it; older files do not)
  return       result price / breakout price - 1  (a ratio; None = unknown, e.g. an old file's expired item without
               a close). Gain base = the LINE price at the breakout bar (D39), not the breakout bar's high.
  bars / days  bars (days) from the breakout bar to the result bar (open: to the last checked bar)
  anomaly      True when the tracked levels are impossible for an upward breakout (invalidation price >= breakout
               price, or TP1 <= breakout price; seen on some descending triangles flagged breakout_up): such an item
               "fails" on its first bar by construction, so it is listed (with a note) but kept out of every summary
               and its `return` is None. The tracker (D39) is not changed by this.
  retro        True when an event was found while the item was first added (first run, or the event is dated before
               the pattern's signal_time): the result had already happened before tracking, so it is not a real-time
               result; `summary.live` leaves those out, `summary.all` keeps them.

Summary denominator (`n`): items that are DECIDED or have reached TP1 = status tp1 / tp2 / failed / expired.
Plain `open` items (nothing happened yet) are counted separately in `open`, they are in no rate.
  tp1_rate     items that ever reached TP1 (incl. tp2, and failed / expired after TP1) / n
  tp2_rate     status tp2 / n
  failed_rate  status failed / n  (includes "TP1 first, then stopped out"; `failed_after_tp1` counts those)
  expired_rate status expired / n
  avg_return   mean of `return` over the items of n whose return is known (`n_return`); a tp1 item that is still
               open is valued at the TP1 price (a floor, TP2 may still come)
Groups: kind x timeframe, kind x all timeframes, all kinds x timeframe, and everything.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from ihs_scanner import tp_tracker as tt

RECORD_VERSION = 1
DECIDED = ("tp1", "tp2", "failed", "expired")  # the denominator of every rate
RETRO_REASONS = ("first_run", "before_signal")
KINDS = ("ihs", "triangle")
TIMEFRAMES = ("4h", "1d", "1w")


def _event(item: Mapping[str, Any], etype: str) -> Mapping[str, Any] | None:
    for e in item.get("events") or []:
        if e.get("type") == etype:
            return e
    return None


def _ratio(price: Any, base: Any) -> float | None:
    p, b = tt._num(price), tt._num(base)
    if p is None or b is None or b == 0:
        return None
    return p / b - 1


def _days(t0: Any, t1: Any) -> float | None:
    a, b = tt.parse_utc(t0), tt.parse_utc(t1)
    if a is None or b is None:
        return None
    return max((b - a).total_seconds() / 86400, 0.0)


def make_row(item: Mapping[str, Any]) -> dict[str, Any]:
    """One record of the page from a tracker item (old tracker files lack last_close / silent_reason: tolerated)."""
    status = item.get("status") or "open"
    ev = {t: _event(item, t) for t in tt.EVENT_TYPES}
    result = ev["tp2"] if status == "tp2" else ev["failed"] if status == "failed" else \
        ev["expired"] if status == "expired" else ev["tp1"] if status == "tp1" else None
    basis = status if result is not None else None
    r_time = r_price = None
    bars = None
    if result is not None:
        r_time, r_price, bars = result.get("time"), tt._num(result.get("price")), result.get("bars")
    elif status == "open" and tt._num(item.get("last_close")) is not None:
        r_time, r_price, basis = item.get("last_bar_time"), tt._num(item.get("last_close")), "last_close"
        bars = item.get("bars_since_breakout")
    elif status == "open":
        bars = item.get("bars_since_breakout")
        r_time = item.get("last_bar_time")
    bp, inv, t1 = (tt._num(item.get(k)) for k in ("breakout_price", "invalidation_price", "tp1"))
    anomaly = bp is not None and ((inv is not None and inv >= bp) or (t1 is not None and t1 <= bp))
    ret = None if anomaly else _ratio(r_price, item.get("breakout_price"))
    retro = any(e.get("silent_reason") in RETRO_REASONS for e in item.get("events") or [])
    row = {
        "pattern_id": item.get("pattern_id"),
        "kind": item.get("kind"),
        "pattern_label": item.get("pattern_label"),
        "triangle_type": item.get("triangle_type"),
        "symbol": item.get("symbol"),
        "name": item.get("name") or "",
        "exchange": item.get("exchange"),
        "market": item.get("market"),
        "tv_symbol": item.get("tv_symbol"),
        "timeframe": item.get("timeframe"),
        "score": item.get("score"),
        "signal_time": item.get("signal_time"),
        "breakout_time": item.get("breakout_time"),
        "breakout_price": tt._num(item.get("breakout_price")),
        "tp1": tt._num(item.get("tp1")),
        "tp2": tt._num(item.get("tp2")),
        "invalidation_price": tt._num(item.get("invalidation_price")),
        "status": status,
        "had_tp1": ev["tp1"] is not None,
        "tp1_time": ev["tp1"].get("time") if ev["tp1"] else None,
        "tp2_time": ev["tp2"].get("time") if ev["tp2"] else None,
        "result_basis": basis,   # tp1 | tp2 | failed | expired | last_close | None
        "result_time": r_time,
        "result_price": r_price,
        "return": ret,
        "bars": int(bars) if isinstance(bars, (int, float)) else None,
        "days": _days(item.get("breakout_time"), r_time),
        "retro": retro,
        "anomaly": anomaly,
        "added_at": item.get("added_at"),
        "last_bar_time": item.get("last_bar_time"),
        "last_close": tt._num(item.get("last_close")),
    }
    return row


def _group(rows: list[Mapping[str, Any]]) -> dict[str, Any]:
    decided = [r for r in rows if r["status"] in DECIDED]
    n = len(decided)
    rets = [r["return"] for r in decided if r["return"] is not None]

    def rate(k: int) -> float | None:
        return k / n if n else None

    tp1 = sum(1 for r in decided if r["had_tp1"])
    tp2 = sum(1 for r in decided if r["status"] == "tp2")
    failed = sum(1 for r in decided if r["status"] == "failed")
    expired = sum(1 for r in decided if r["status"] == "expired")
    return {
        "n": n, "open": sum(1 for r in rows if r["status"] == "open"),
        "tp1": tp1, "tp2": tp2, "failed": failed, "expired": expired,
        "failed_after_tp1": sum(1 for r in decided if r["status"] == "failed" and r["had_tp1"]),
        "tp1_rate": rate(tp1), "tp2_rate": rate(tp2), "failed_rate": rate(failed), "expired_rate": rate(expired),
        "n_return": len(rets), "avg_return": (sum(rets) / len(rets)) if rets else None,
    }


def summarize(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Groups (see module doc): [{"kind": "ihs"|"triangle"|None, "timeframe": "4h"|...|None, **stats}]; always
    contains the overall group (kind None, timeframe None); empty (kind, timeframe) groups are left out."""
    rows = list(rows)
    out = []
    for kind in (None, *KINDS):
        for tf in (None, *TIMEFRAMES):
            sub = [r for r in rows if (kind is None or r["kind"] == kind) and (tf is None or r["timeframe"] == tf)]
            if sub or (kind is None and tf is None):
                out.append({"kind": kind, "timeframe": tf, **_group(sub)})
    return out


def build(items: Mapping[str, Mapping[str, Any]], *, market: str, tracker_updated_at: str | None = None,
          now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    rows = [make_row(it) for it in items.values()]
    rows.sort(key=lambda r: (str(r.get("breakout_time") or ""), str(r.get("pattern_id"))), reverse=True)
    added = sorted(r["added_at"] for r in rows if r.get("added_at"))
    return {
        "version": RECORD_VERSION,
        "market": market,
        "generated_at": tt.utc_text(now),
        "tracker_updated_at": tracker_updated_at,
        "tracking_since": added[0] if added else None,
        "counts": {"items": len(rows), "retro": sum(1 for r in rows if r["retro"]),
                   "anomaly": sum(1 for r in rows if r["anomaly"]),
                   **{s: sum(1 for r in rows if r["status"] == s) for s in ("open", "tp1", "tp2", "failed", "expired")}},
        "summary": {"live": summarize(r for r in rows if not r["retro"] and not r["anomaly"]),
                    "all": summarize(r for r in rows if not r["anomaly"])},
        "items": rows,
    }


def write_from_tracker(tracker_path: Path, out_path: Path, market: str, now: datetime | None = None) -> bool:
    """tracker file -> record JSON (atomic). False = no usable tracker file (nothing written)."""
    try:
        data = json.loads(Path(tracker_path).read_text(encoding="utf-8"))
        items = data["items"]
        if data.get("version") != tt.TRACKER_VERSION or not isinstance(items, dict):
            return False
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return False
    doc = build(items, market=market, tracker_updated_at=data.get("updated_at"), now=now)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_suffix(out_path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, ensure_ascii=False, separators=(",", ":"))
    tmp.replace(out_path)
    return True


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="tp_tracker*.json -> track_record.json (D41)")
    ap.add_argument("--tracker", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--market", choices=("crypto", "tw"), required=True)
    a = ap.parse_args(argv)
    ok = write_from_tracker(Path(a.tracker), Path(a.out), a.market)
    print(f"[track_record] {'wrote ' + a.out if ok else 'tracker unreadable: nothing written'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
