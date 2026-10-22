"""Take-profit tracking after a breakout (charter D39). Pure logic, no network: notify_tp.py does the I/O.

What is tracked (one item per pattern_id; after the breakout the RS / the triangle lines no longer move):
  * IHS      status "confirmed"    TP1 = tp1, TP2 = tp2, invalidation = invalidation_price (H.low)
  * triangle status "breakout_up"  TP1 = target_conservative, TP2 = target, invalidation = invalidation_price
    (downward triangle breakouts are not tracked yet)

Breakout price (the base of the gain %): the LINE price at the breakout bar, i.e. IHS `breakout_level` (neckline at
the breakout bar) and triangle `breakout_price` (resistance line at the breakout bar). Since D36 the IHS
`breakout_price` is the HIGH of the breakout bar (a wick through the neckline), which can already be far above the
neckline (NMR 1w: neckline 11.05, bar high 15.50), so it is kept only as `breakout_bar_high`.

Evaluation (evaluate()): the CLOSED bars (close_time < now) from the breakout bar (index 0) on, in order; it is a
pure function of those bars, so evaluating as of bar t only ever needs df[:t+1] (no look-ahead, no repaint):
  open: low <= invalidation                    -> "failed" (also when the same bar touches a TP: intrabar order is
                                                  unknown, so the bar is read conservatively as stopped out)
        high >= TP2                            -> "tp1" (when high >= TP1 too) + "tp2", tracking ends
        high >= TP1                            -> "tp1", tracking goes on for TP2
  tp1:  low <= invalidation                    -> "failed" (same conservative rule; the tp1 event stays recorded)
        high >= TP2                            -> "tp2", tracking ends
  no TP2 / failure by bar MAX_BARS[tf] after the breakout bar -> "expired"
The breakout bar itself counts: price has to cross the line before it can reach a target above it.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping

import pandas as pd

TRACKER_VERSION = 1
TP_CONFIG: dict[str, Any] = {
    # bars after the breakout bar before an untouched item expires (4h 180 = 30 days, 1d 120, 1w 52 = 1 year)
    "max_bars": {"4h": 180, "1d": 120, "1w": 52},
    "notify_failed": False,  # a failure (low <= invalidation) is recorded; True also sends it to Discord
    "prune_days": 90,        # finished items are kept this long so a still-listed pattern is not re-added
}
TIMEFRAME_SECONDS = {"4h": 4 * 3600, "1d": 86400, "1w": 7 * 86400}
OPEN_STATUSES = ("open", "tp1")
END_STATUSES = ("tp2", "failed", "expired")
EVENT_TYPES = ("tp1", "tp2", "failed", "expired")
TRIANGLE_LABEL = {"ascending": "上升三角", "descending": "下降三角", "symmetrical": "對稱三角"}
IHS_LABEL = "頭肩底"
CHART_DROP_KEYS = ("bars", "future_times")  # a chart skeleton = the bars file minus the bars themselves


def utc_text(t: datetime | pd.Timestamp) -> str:
    return pd.Timestamp(t).tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_utc(text: Any) -> pd.Timestamp | None:
    if not text:
        return None
    try:
        ts = pd.Timestamp(text)
    except (TypeError, ValueError):
        return None
    return ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")


def _num(x: Any) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def is_tw_row(row: Mapping[str, Any]) -> bool:
    return row.get("market") == "stock" or row.get("exchange") in ("twse", "tpex")


def chart_skeleton(doc: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """The bars file without its bars: points / neckline / lines / breakout / levels (a few hundred bytes), kept in
    the tracker so a chart can still be drawn (with freshly fetched bars) after the bars file is gone."""
    if not isinstance(doc, Mapping) or not doc.get("bars"):
        return None
    return {k: v for k, v in doc.items() if k not in CHART_DROP_KEYS}


def _base_item(row: Mapping[str, Any], *, kind: str, label: str, breakout_price: Any, tp1: Any, tp2: Any,
               score: Any, chart: Mapping[str, Any] | None, now: datetime) -> dict[str, Any] | None:
    need = {"pattern_id": row.get("pattern_id"), "breakout_time": row.get("breakout_time"),
            "breakout_price": _num(breakout_price), "tp1": _num(tp1), "tp2": _num(tp2),
            "invalidation_price": _num(row.get("invalidation_price"))}
    if any(v is None for v in need.values()) or row.get("timeframe") not in TIMEFRAME_SECONDS:
        return None
    return {
        **need,
        "kind": kind,
        "pattern_label": label,
        "pattern_key": row.get("pattern_key"),
        "symbol": row.get("symbol"),
        "name": row.get("name") or "",
        "exchange": row.get("exchange") or "binance",
        "market": row.get("market") or "spot",
        "tv_symbol": row.get("tv_symbol"),
        "timeframe": row["timeframe"],
        "signal_time": row.get("signal_time") or row.get("breakout_time"),
        "score": _num(score),
        "chart_file": row.get("chart_file"),
        "chart": chart_skeleton(chart),
        "status": "open",
        "events": [],
        "added_at": utc_text(now),
        "last_checked_at": None,
        "last_bar_time": None,
        "bars_since_breakout": None,
        "ended_at": None,
    }


def ihs_item(row: Mapping[str, Any], *, chart: Mapping[str, Any] | None = None,
             now: datetime | None = None) -> dict[str, Any] | None:
    """Tracker item for a confirmed IHS row; None when the row lacks a field the tracking needs."""
    if row.get("status") != "confirmed":
        return None
    item = _base_item(row, kind="ihs", label=IHS_LABEL, breakout_price=row.get("breakout_level"),
                      tp1=row.get("tp1"), tp2=row.get("tp2"), score=row.get("pattern_score"), chart=chart,
                      now=now or datetime.now(timezone.utc))
    if item is not None:
        item["breakout_bar_high"] = _num(row.get("breakout_price"))
    return item


def triangle_item(row: Mapping[str, Any], *, chart: Mapping[str, Any] | None = None,
                  now: datetime | None = None) -> dict[str, Any] | None:
    """Tracker item for an upward triangle breakout (TP1 = conservative target, TP2 = target)."""
    if row.get("status") != "breakout_up":
        return None
    label = TRIANGLE_LABEL.get(str(row.get("triangle_type")), "收斂三角")
    item = _base_item(row, kind="triangle", label=label, breakout_price=row.get("breakout_price"),
                      tp1=row.get("target_conservative"), tp2=row.get("target"), score=row.get("score"),
                      chart=chart, now=now or datetime.now(timezone.utc))
    if item is not None:
        item["triangle_type"] = row.get("triangle_type")
    return item


def _event(kind: str, bar: Any, k: int, price: float) -> dict[str, Any]:
    return {"type": kind, "time": utc_text(bar.timestamp), "price": float(price), "bars": int(k),
            "bar_high": float(bar.high), "bar_low": float(bar.low), "bar_close": float(bar.close)}


def closed_after_breakout(item: Mapping[str, Any], bars: pd.DataFrame, now: datetime) -> pd.DataFrame:
    """The closed bars (close_time < now) from the breakout bar on, oldest first."""
    if bars is None or bars.empty:
        return bars.iloc[0:0] if bars is not None else pd.DataFrame()
    start = parse_utc(item["breakout_time"])
    df = bars[(bars["close_time"] < pd.Timestamp(now).tz_convert("UTC")) & (bars["timestamp"] >= start)]
    return df.sort_values("timestamp").reset_index(drop=True)


def evaluate(item: Mapping[str, Any], bars: pd.DataFrame, *, now: datetime,
             max_bars: Mapping[str, int] | None = None) -> dict[str, Any]:
    """-> {"status", "events": [...], "bars_since_breakout", "last_bar_time"} from scratch (deterministic).

    `bars` is the common frame (timestamp / open / high / low / close / volume / close_time, UTC). Only bars with
    close_time < now are used. An empty result (status "open", no events) also when the breakout bar is not in it.
    """
    limit = int((max_bars or TP_CONFIG["max_bars"]).get(item["timeframe"], 120))
    tp1, tp2, stop = float(item["tp1"]), float(item["tp2"]), float(item["invalidation_price"])
    df = closed_after_breakout(item, bars, now)
    status, events = "open", []
    last_k, last_time = -1, None
    for k, bar in enumerate(df.itertuples(index=False)):
        if k > limit:
            break
        last_k, last_time = k, utc_text(bar.timestamp)
        stopped = float(bar.low) <= stop
        if status == "open":
            if stopped:
                events.append(_event("failed", bar, k, stop))
                status = "failed"
                break
            if float(bar.high) >= tp2:
                if float(bar.high) >= tp1:
                    events.append(_event("tp1", bar, k, tp1))
                events.append(_event("tp2", bar, k, tp2))
                status = "tp2"
                break
            if float(bar.high) >= tp1:
                events.append(_event("tp1", bar, k, tp1))
                status = "tp1"
        elif status == "tp1":
            if stopped:
                events.append(_event("failed", bar, k, stop))
                status = "failed"
                break
            if float(bar.high) >= tp2:
                events.append(_event("tp2", bar, k, tp2))
                status = "tp2"
                break
        if k == limit:
            events.append(_event("expired", bar, k, float(bar.close)))
            status = "expired"
            break
    return {"status": status, "events": events, "bars_since_breakout": last_k if last_k >= 0 else None,
            "last_bar_time": last_time}


def bars_needed(item: Mapping[str, Any], now: datetime, *, max_bars: Mapping[str, int] | None = None,
                cap: int = 1000, margin: int = 5) -> int:
    """How many of the newest bars to fetch so they reach back to the chart start (or the breakout bar); calendar
    based, so Taiwan (no nights / weekends) and crypto weekends over-estimate, which is harmless."""
    step = TIMEFRAME_SECONDS[item["timeframe"]]
    start = parse_utc(item["breakout_time"])
    chart_start = chart_start_time(item.get("chart"))
    if chart_start is not None and chart_start < start:
        start = chart_start
    n = int(math.ceil((pd.Timestamp(now).tz_convert("UTC") - start).total_seconds() / step)) + margin
    return max(10, min(cap, n))


def chart_start_time(chart: Mapping[str, Any] | None) -> pd.Timestamp | None:
    """Earliest time drawn by a chart skeleton (IHS points / triangle lines), minus a few bars of context."""
    if not isinstance(chart, Mapping):
        return None
    times = [p.get("time") for p in chart.get("points") or []]
    times += [p.get("time") for p in (chart.get("resistance_line") or []) + (chart.get("support_line") or [])]
    times = [float(t) for t in times if isinstance(t, (int, float))]
    if not times:
        return None
    return pd.Timestamp(min(times), unit="s", tz="UTC")


def new_items(scan: Mapping[str, Any] | None, triangles: Mapping[str, Any] | None, *,
              charts: Mapping[str, Mapping[str, Any]] | None = None,
              now: datetime | None = None) -> list[dict[str, Any]]:
    """Candidate items of this scan: confirmed IHS rows + breakout_up triangles (charts: chart_file -> bars doc)."""
    now = now or datetime.now(timezone.utc)
    charts = charts or {}
    out: list[dict[str, Any]] = []
    for row in (scan or {}).get("results") or []:
        it = ihs_item(row, chart=charts.get(row.get("chart_file") or ""), now=now)
        if it:
            out.append(it)
    for row in (triangles or {}).get("results") or []:
        it = triangle_item(row, chart=charts.get(row.get("chart_file") or ""), now=now)
        if it:
            out.append(it)
    return out


def recorded_types(item: Mapping[str, Any]) -> set[str]:
    return {e.get("type") for e in item.get("events") or []}


def prune(items: Mapping[str, Mapping[str, Any]], now: datetime, days: int) -> dict[str, Mapping[str, Any]]:
    """Drop finished items whose ended_at is older than `days`."""
    cutoff = pd.Timestamp(now).tz_convert("UTC") - timedelta(days=days)
    out = {}
    for pid, it in items.items():
        ended = parse_utc(it.get("ended_at"))
        if it.get("status") in END_STATUSES and ended is not None and ended < cutoff:
            continue
        out[pid] = it
    return out


def summarize(items: Iterable[Mapping[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for it in items:
        counts[it.get("status", "?")] = counts.get(it.get("status", "?"), 0) + 1
    return counts
