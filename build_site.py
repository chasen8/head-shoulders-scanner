"""Build the static site's data: fetch exchange public klines, scan, write site/data/.

Sources (--sources, charter D22): binance-spot, binance-perp (opt-in: fapi answers 451 on US runners),
okx-spot, okx-perp, gate-perp. A base coin is scanned from ONE source only, by priority
binance-spot > binance-perp > okx-spot > okx-perp > gate-perp. Every result carries exchange, market,
tv_symbol; pattern_id is prefixed "<exchange>|<market>|".

Output (front end only draws these values; every number it shows is computed here):
  site/data/scan.json            metadata + one summary row per pattern (no bars)
  site/data/bars/<file>.json     chart data for one pattern (bars, five points, neckline, breakout,
                                 TP1/TP2/invalidation levels), loaded by the page only when opened
  site/data/triangles.json + tri_bars/   D38 converging triangles on the same K lines (run_triangles; a triangle
                                 failure never fails this build)

Partial success: a symbol/timeframe that fails to fetch or scan is listed in scan.json `failures`.
Only when more than MAX_FAILURE_RATIO of the series fail does the build exit non-zero and leave
the previous scan.json and bars/ untouched.

Example (Git Bash, project root):
    /c/trading-data/ihs-scanner-v2/venv/Scripts/python.exe build_site.py            # all default sources
    /c/trading-data/ihs-scanner-v2/venv/Scripts/python.exe build_site.py --sources binance-spot,okx-perp
    /c/trading-data/ihs-scanner-v2/venv/Scripts/python.exe build_site.py --symbols BTCUSDT,ETHUSDT
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import numpy as np
import pandas as pd
import requests

from ihs_scanner import data_binance_futures, data_gate, data_okx
from ihs_scanner.config import MIN_MAX_POSITION_USDT, TIMEFRAMES, merge_config
from ihs_scanner.data_binance import (BinanceFetchError, WeightLimiter, fetch_klines, fetch_quote_volumes,
                                      list_usdt_symbols)
from ihs_scanner.keys import pattern_key
from ihs_scanner.neckline import calculate_neckline
from ihs_scanner.scanner import (OUTPUT_FIELDS, _to_jsonable, scan_symbols, sort_results,
                                 supersede_by_higher_timeframe)
from ihs_scanner.sources import (SOURCE_PRIORITY, Instrument, PositionFilterResult, SourceFetchError,
                                 merge_with_position_limit)
from ihs_scanner.swings import get_timestamps
from ihs_scanner.validation import CHECKS, shape_checks

DEFAULT_OUT = Path(__file__).resolve().parent / "site" / "data" / "scan.json"
# D29: Taiwan stocks are a separate market with their own scan.json + bars/ (written by build_site_tw.py),
# so the crypto and the Taiwan scans never overwrite each other.
DEFAULT_OUT_TW = Path(__file__).resolve().parent / "site" / "data" / "tw" / "scan.json"
MARKETS = ("crypto", "tw")
BARS_DIRNAME = "bars"
BARS_BEFORE_LS = 15  # chart context to the left of the left shoulder
# Weekly history is short (BTC has < 500 weekly bars) and patterns span <= 80 bars.
LIMIT_CAP_BY_TIMEFRAME = {"1w": 500}
MAX_FAILURE_RATIO = 0.20
MAX_WORKERS = 4
ERROR_CHARS = 240
# Must match .github/workflows/scan-and-notify.yml: cron "7 */4 * * *"
SCAN_EVERY_HOURS = 4
SCAN_MINUTE = 7
SCHEMA_VERSION = 8  # 3: bars files carry `levels`, `levels_estimated`, `rs_provisional`;
#                     4: rows carry `shape_checks`, settings `shape_validation` (D26);
#                     5: rows carry `pattern_key` (D24 key, no RS) for the site watchlist;
#                     6: rows carry `neckline_change_pct` and `neckline_trend`, settings `neckline_trend_threshold`;
#                     7: payload `market` ("crypto" | "tw"), rows (and bars files) may carry `name` (D29 Taiwan stocks)
#                     8: rows carry `neckline_touches`, `breakout_basis`; payload `superseded`, settings
#                        `breakout_price` (D36)
# Neckline N1->N2 total change: <= -T is "down", >= +T is "up", otherwise "flat" (front end only filters on it).
NECKLINE_TREND_THRESHOLD = 0.03
POINT_NAMES = ("LS", "N1", "H", "N2", "RS")
# Horizontal levels drawn on the chart (values come straight from the detector's output fields).
LEVELS = (("tp1", "TP1"), ("tp2", "TP2"), ("invalidation_price", "失效"))
# fapi.binance.com is HTTP 451 from GitHub's US runners, so binance-perp is not in the default set.
DEFAULT_SOURCES = ("binance-spot", "okx-spot", "okx-perp", "gate-perp")
ALL_SOURCES = SOURCE_PRIORITY
FETCH_ERRORS = (BinanceFetchError, SourceFetchError, ValueError, requests.RequestException)
# D45: why a source has no max-position filter (scan.json max_position_filter.not_applied)
MAX_POSITION_NOT_APPLIED = {
    "binance-spot": "spot: no position limit",
    "okx-spot": "spot: no position limit",
    "binance-perp": "position brackets (/fapi/v1/leverageBracket) need a signed request; not filtered",
}


def _csv_list(text: str) -> list[str]:
    return [s.strip() for s in text.split(",") if s.strip()]


def _iso(ts: Any) -> str | None:
    return _to_jsonable(pd.Timestamp(ts)) if ts is not None else None


def _unix(ts: Any) -> int:
    t = pd.Timestamp(ts)
    t = t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")
    return int(t.value // 1_000_000_000)


def _num(x: Any) -> float | None:
    f = float(x)
    return f if math.isfinite(f) else None


def _short_error(exc: BaseException | str) -> str:
    text = exc if isinstance(exc, str) else f"{type(exc).__name__}: {exc}"
    text = " ".join(str(text).split())
    return text if len(text) <= ERROR_CHARS else text[: ERROR_CHARS - 1] + "…"


def next_scan_time(now: datetime) -> datetime:
    """First cron slot (minute SCAN_MINUTE of every SCAN_EVERY_HOURS-th UTC hour) after `now`."""
    now = now.astimezone(timezone.utc)
    slot_hour = now.hour - now.hour % SCAN_EVERY_HOURS
    slot = now.replace(hour=slot_hour, minute=SCAN_MINUTE, second=0, microsecond=0)
    while slot <= now:
        slot += timedelta(hours=SCAN_EVERY_HOURS)
    return slot


def limit_for(timeframe: str, limit: int) -> int:
    return min(limit, LIMIT_CAP_BY_TIMEFRAME.get(timeframe, limit))


def chart_file_name(result: Mapping[str, Any]) -> str:
    """Filesystem/URL-safe, unique file name for one pattern's chart data.

    Symbols can be non-ASCII (e.g. 币安人生USDT), so unsafe characters become "_" and a short
    hash of pattern_id keeps names unique."""
    safe_sym = re.sub(r"[^A-Za-z0-9]", "_", str(result["symbol"])).strip("_") or "SYM"
    src = f"{result.get('exchange', 'binance')}_{result.get('market', 'spot')}"
    digest = hashlib.sha1(str(result["pattern_id"]).encode("utf-8")).hexdigest()[:10]
    return f"{src}_{safe_sym}_{result['timeframe']}_{_unix(result['LS_time'])}_{digest}.json"


def chart_for(result: Mapping[str, Any], df: pd.DataFrame, angle_scale: float) -> dict[str, Any]:
    """Everything the chart needs, in UNIX seconds (UTC).

    bars: [[time, open, high, low, close, volume], ...] from BARS_BEFORE_LS bars before LS
    through the last closed bar. neckline: the N1-N2 line evaluated (neckline_price_at) at
    the first bar of the LS and at the last bar, so the front end draws a straight segment.
    levels: TP1 / TP2 / invalidation (H.low) as horizontal lines; levels_estimated = not broken out yet
    (targets then use the current neckline price). rs_provisional: the right shoulder is still forming
    (no RS_confirm_time), so the RS point moves with new bars; the RS point carries "provisional".
    """
    ts = get_timestamps(df)
    pos = {name: int(ts.get_loc(pd.Timestamp(result[f"{name}_time"]))) for name in POINT_NAMES}
    last = len(df) - 1
    start = max(0, pos["LS"] - BARS_BEFORE_LS)
    neck = calculate_neckline((pos["N1"], result["N1_price"]), (pos["N2"], result["N2_price"]), angle_scale)

    seg = df.iloc[start:last + 1]
    bars = [
        [_unix(t), _num(o), _num(h), _num(lo), _num(c), _num(v)]
        for t, o, h, lo, c, v in zip(ts[start:last + 1], seg["open"], seg["high"], seg["low"],
                                     seg["close"], seg["volume"])
    ]
    provisional = rs_provisional(result)
    points = [{"label": name, "time": _unix(result[f"{name}_time"]), "price": _num(result[f"{name}_price"])}
              for name in POINT_NAMES]
    if provisional:
        points[-1]["provisional"] = True
    levels = [{"key": key, "label": label, "price": _num(result[key])}
              for key, label in LEVELS if result.get(key) is not None and _num(result[key]) is not None]
    breakout = None
    if result.get("breakout_detected"):
        breakout = {"time": _unix(result["breakout_time"]), "price": _num(result["breakout_price"])}
    return {
        "bars": bars,
        "points": points,
        "neckline": [
            {"time": _unix(ts[pos["LS"]]), "price": _num(neck.neckline_price_at(pos["LS"]))},
            {"time": _unix(ts[last]), "price": _num(neck.neckline_price_at(last))},
        ],
        "breakout": breakout,
        "levels": levels,
        "levels_estimated": not result.get("breakout_detected"),
        "rs_provisional": provisional,
    }


# 52-week position (local trial): window of daily closes per market, and bar durations for the as-of cut.
WEEK52_DAYS = {"crypto": 365, "tw": 252}
_TF_DELTA = {"4h": pd.Timedelta(hours=4), "1d": pd.Timedelta(days=1), "1w": pd.Timedelta(days=7)}


def week52_fields(df: pd.DataFrame, daily: pd.DataFrame | None, timeframe: str, days: int) -> dict[str, Any]:
    """`pos_52w` = (price - low) / (high - low) and `near_high_52w` = price / high, on CLOSES of the last `days`
    daily bars (price = the series' last closed bar, always included). Only daily bars that had CLOSED by the
    series' last bar's close are used (no look-ahead; 4h/1w use the same symbol's daily series). Fewer than
    `days` such daily bars, no daily series, or a flat range -> both None."""
    none = {"pos_52w": None, "near_high_52w": None}
    if daily is None or not len(df) or not len(daily):
        return none
    asof = get_timestamps(df)[-1] + _TF_DELTA[timeframe]
    dts = get_timestamps(daily)
    closes = daily["close"].to_numpy(dtype=float)[(dts + _TF_DELTA["1d"]) <= asof][-days:]
    price = float(df["close"].iloc[-1])
    if len(closes) < days or not math.isfinite(price) or not np.isfinite(closes).all():
        return none
    hi, lo = max(float(closes.max()), price), min(float(closes.min()), price)
    if hi <= lo or hi <= 0:
        return none
    return {"pos_52w": round((price - lo) / (hi - lo), 4), "near_high_52w": round(price / hi, 4)}


def rs_provisional(result: Mapping[str, Any]) -> bool:
    """True while the right shoulder is still forming (D23 status "forming": no RS_confirm_time)."""
    return result.get("RS_confirm_time") is None


def shape_for(chart: Mapping[str, Any]) -> dict[str, Any]:
    """Tiny summary for the list silhouette: [time, price] pairs only (no bars)."""
    last = chart["bars"][-1]
    brk = chart["breakout"]
    return {
        "points": [[p["time"], p["price"]] for p in chart["points"]],
        "neckline": [[p["time"], p["price"]] for p in chart["neckline"]],
        "breakout": [brk["time"], brk["price"]] if brk else None,
        "last": [last[0], last[4]],
    }


def neckline_trend(r: Mapping[str, Any]) -> tuple[float | None, str | None]:
    """(N2_price / N1_price - 1, "down" | "flat" | "up"); D17: computed here, the front end only filters."""
    n1, n2 = r.get("N1_price"), r.get("N2_price")
    if not n1 or n2 is None or n1 <= 0:
        return None, None
    chg = float(n2) / float(n1) - 1.0
    t = NECKLINE_TREND_THRESHOLD
    return chg, ("down" if chg <= -t else "up" if chg >= t else "flat")


def _neckline_side(r: Mapping[str, Any]) -> str:
    close, neck = float(r["current_close"]), float(r["current_neckline_price"])
    return "above" if close > neck else ("below" if close < neck else "at")


def _default_instrument(symbol: str) -> Instrument:
    base = symbol[:-4] if symbol.endswith("USDT") and len(symbol) > 4 else symbol
    return Instrument(base=base, symbol=symbol, native=symbol, exchange="binance", market="spot")


def build_payload(symbol_data: Mapping[str, Mapping[str, pd.DataFrame]], cfg: Mapping[str, Any], *,
                  symbols: list[str], timeframes: list[str], limit: int,
                  generated_at: datetime | None = None, scheduled: bool = False,
                  failures: list[dict[str, Any]] | None = None,
                  skipped: list[dict[str, Any]] | None = None,
                  symbols_source: str = "list",
                  instruments: Mapping[str, Instrument] | None = None,
                  sources: Sequence[str] | None = None,
                  source_listed: Mapping[str, int] | None = None,
                  source_errors: list[dict[str, Any]] | None = None,
                  market: str = "crypto",
                  next_scan: Callable[[datetime], datetime] | None = None,
                  data_source: str | None = None,
                  extra_settings: Mapping[str, Any] | None = None,
                  position_filter: Mapping[str, Any] | None = None) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """Returns (scan.json payload, {bars file name: chart data}).

    Each symbol/timeframe is scanned on its own; a detector exception is recorded in `failures`
    (stage "scan") instead of aborting the build. scheduled=False -> next_scan_at is null.
    market: "crypto" or "tw" (D29); next_scan: the market's cron schedule (default: every 4 h);
    instruments with a `name` (Taiwan stocks) put it on every row and bars file.
    position_filter (D45, position_filter_meta()): per filtered source `excluded_max_position` /
    `max_position_unknown` in source_counts, and the whole record as payload `max_position_filter`."""
    next_scan = next_scan or next_scan_time
    generated_at = generated_at or datetime.now(timezone.utc)
    failures = list(failures or [])
    instruments = instruments or {}

    def inst_of(sym: str) -> Instrument:
        return instruments.get(sym) or _default_instrument(sym)

    results: list[dict[str, Any]] = []
    for sym, by_tf in symbol_data.items():
        for tf, df in by_tf.items():
            try:
                results.extend(scan_symbols({sym: {tf: df}}, cfg))
            except Exception as exc:  # one bad series must not sink the whole site
                failures.append({"symbol": sym, "timeframe": tf, "stage": "scan", "error": _short_error(exc)})
    for r in results:  # tag the origin; pattern_id gets a prefix so ids never collide across sources
        inst = inst_of(r["symbol"])
        r["exchange"], r["market"], r["tv_symbol"] = inst.exchange, inst.market, inst.tv_symbol
        if inst.name:
            r["name"] = inst.name
        r["pattern_id"] = f"{inst.exchange}|{inst.market}|{r['pattern_id']}"
    superseded: list[dict[str, Any]] = []
    if cfg["supersede_by_higher_timeframe"]:  # D36: across timeframes, so after every series is scanned
        results, gone = supersede_by_higher_timeframe(results)
        superseded = [{"pattern_id": g["pattern_id"], "superseded_by": g["superseded_by"]} for g in gone]
    results = sort_results(results)

    angle_scale = float(cfg["angle_scale"])
    rows, charts = [], {}
    for r in results:
        try:
            chart = chart_for(r, symbol_data[r["symbol"]][r["timeframe"]], angle_scale)
        except Exception as exc:
            failures.append({"symbol": r["symbol"], "timeframe": r["timeframe"], "stage": "chart",
                             "error": _short_error(exc)})
            continue
        keys = OUTPUT_FIELDS + [k for k in r if k not in OUTPUT_FIELDS]
        row = {k: _to_jsonable(r.get(k)) for k in keys}
        row["rank"] = len(rows) + 1
        row["pattern_key"] = pattern_key(row)  # same function as notify_discord (D24); row times are strings
        row["neckline_side"] = _neckline_side(r)
        row["rs_provisional"] = rs_provisional(r)
        row["neckline_change_pct"], row["neckline_trend"] = neckline_trend(row)
        row.update(week52_fields(symbol_data[r["symbol"]][r["timeframe"]], symbol_data[r["symbol"]].get("1d"),
                                 r["timeframe"], WEEK52_DAYS.get(market, 365)))
        row["shape_checks"] = shape_checks(r, cfg)  # D26: value / limit / pass decided here (D17)
        name = chart_file_name(r)
        row["chart_file"] = f"{BARS_DIRNAME}/{name}"
        row["shape"] = shape_for(chart)
        charts[name] = {"schema_version": SCHEMA_VERSION, "pattern_id": row["pattern_id"],
                        "symbol": row["symbol"], "exchange": row["exchange"], "market": row["market"],
                        "timeframe": row["timeframe"], **({"name": row["name"]} if row.get("name") else {}), **chart}
        rows.append(row)

    last_closed: dict[str, str | None] = {}
    for tf in timeframes:
        stamps = [get_timestamps(by_tf[tf])[-1] for by_tf in symbol_data.values()
                  if tf in by_tf and len(by_tf[tf])]
        last_closed[tf] = _iso(max(stamps)) if stamps else None
    scanned = sorted(sym for sym, by_tf in symbol_data.items() if by_tf)
    series = sum(len(by_tf) for by_tf in symbol_data.values())
    for f in failures:
        inst = inst_of(f["symbol"])
        f.setdefault("exchange", inst.exchange)
        f.setdefault("market", inst.market)
    blank = {"listed": 0, "symbols": 0, "scanned_symbols": 0, "series_scanned": 0, "failed_series": 0}
    source_counts: dict[str, dict[str, int]] = {}
    for sym in symbols:
        source_counts.setdefault(inst_of(sym).source, dict(blank))["symbols"] += 1
    for sym, by_tf in symbol_data.items():
        c = source_counts.setdefault(inst_of(sym).source, dict(blank))
        c["scanned_symbols"] += 1 if by_tf else 0
        c["series_scanned"] += len(by_tf)
    for sym, _tf in {(f["symbol"], f["timeframe"]) for f in failures}:
        source_counts.setdefault(inst_of(sym).source, dict(blank))["failed_series"] += 1
    for key, n in (source_listed or {}).items():
        source_counts.setdefault(key, dict(blank))["listed"] = n
    for key in (position_filter or {}).get("applied_sources", ()):
        c = source_counts.setdefault(key, dict(blank))
        c["excluded_max_position"] = sum(1 for e in position_filter["excluded"] if e["source"] == key)
        c["max_position_unknown"] = int(position_filter["unknown"].get(key, 0))
    payload = {
        "schema_version": SCHEMA_VERSION,
        "market": market,
        "generated_at": generated_at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "next_scan_at": next_scan(generated_at).strftime("%Y-%m-%dT%H:%M:%SZ") if scheduled else None,
        "settings": {
            "symbols_source": symbols_source,
            "symbols": symbols,
            "timeframes": timeframes,
            "mode": "breakout" if cfg["enable_breakout_filter"] else "candidate",
            "include_stale": bool(cfg["include_stale"]),
            "volume_filter": bool(cfg["enable_volume_filter"]),
            "min_pattern_score": float(cfg["min_pattern_score"]),
            "profile": str(cfg["profile"]),
            "neckline_trend_threshold": NECKLINE_TREND_THRESHOLD,
            "breakout_price": str(cfg["breakout_price"]),  # D36: "high" (wick) or "close"; front end wording
            # D26 thresholds (None = check off); the per-row values and pass flags are in shape_checks
            "shape_validation": {cid: (None if cfg[key] is None else float(cfg[key]))
                                 for cid, _f, key, _k, _t in CHECKS},
            "bars_per_series": {tf: limit_for(tf, limit) for tf in timeframes},
            "sources": list(sources) if sources else sorted(source_counts),
            "data_source": data_source or
                           "exchange public klines (" + ", ".join(sorted(source_counts)) + "), closed bars only",
            **dict(extra_settings or {}),
        },
        "source_counts": {k: source_counts[k] for k in sorted(source_counts)},
        "source_errors": list(source_errors or []),
        "requested_symbols": len(symbols),
        "scanned_symbols": len(scanned),
        "series_total": len(symbols) * len(timeframes),
        "series_scanned": series,
        "failures": failures,
        "skipped": list(skipped or []),
        "max_position_filter": dict(position_filter) if position_filter else None,  # D45
        "superseded": superseded,  # D36: smaller-timeframe patterns replaced by a larger timeframe (not in results)
        "last_closed": last_closed,
        "results": rows,
    }
    return payload, charts


def _dump(obj: Any, path: Path) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _replace_dir(src: Path, dst: Path, attempts: int = 5) -> None:
    """Swap src into dst. Retries because OneDrive/antivirus can briefly lock files on Windows."""
    for i in range(attempts):
        try:
            if dst.exists():
                shutil.rmtree(dst)
            src.replace(dst)
            return
        except OSError:
            if i == attempts - 1:
                raise
            time.sleep(0.5 * (i + 1))


def write_site(payload: Mapping[str, Any], charts: Mapping[str, Mapping[str, Any]], out: Path) -> dict[str, Any]:
    """Write bars/ (fresh: old files never linger) then scan.json, both swapped in at the end.

    Nothing is deleted before all new files are written, so a crash mid-build leaves the previous
    site intact. Returns sizes in bytes."""
    out.parent.mkdir(parents=True, exist_ok=True)
    bars_dir = out.parent / BARS_DIRNAME
    tmp_dir = out.parent / (BARS_DIRNAME + ".tmp")
    if tmp_dir.exists():
        shutil.rmtree(tmp_dir)
    tmp_dir.mkdir()
    total = 0
    for name, chart in charts.items():
        path = tmp_dir / name
        _dump(chart, path)
        total += path.stat().st_size
    tmp = out.with_suffix(out.suffix + ".tmp")
    _dump(payload, tmp)
    _replace_dir(tmp_dir, bars_dir)
    tmp.replace(out)
    return {"scan_json": out.stat().st_size, "bars": total, "bars_files": len(charts)}


def write_payload(payload: Mapping[str, Any], out: Path) -> Path:
    """Write only scan.json (kept for callers that already have a payload)."""
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(out.suffix + ".tmp")
    _dump(payload, tmp)
    tmp.replace(out)
    return out


# ------------------------------------------------------------------------------------------------------------------
# D38 converging triangles: run in the same build on the same K lines, written NEXT TO scan.json:
#   <market dir>/triangles.json        metadata + one row per triangle (no bars)
#   <market dir>/tri_bars/<file>.json  chart data for one triangle (bars, both lines out to the apex, touches,
#                                      breakout, target / conservative target / invalidation levels)
# A triangle failure never fails the IHS build: run_triangles() catches everything, records per-series errors in
# triangles.json `failures`, and leaves the previous triangles.json / tri_bars untouched when more than
# MAX_FAILURE_RATIO of the series fail or the whole step crashes.
TRI_JSON = "triangles.json"
TRI_BARS_DIRNAME = "tri_bars"
TRI_SCHEMA_VERSION = 1
TRI_LINE_SAMPLES = 12  # points per line segment: lines are straight in log price, so they bend a little on the chart
TRI_MAX_EXTENSION_OF_LENGTH = 1.0  # lines run to the apex, but at most this x pattern length past the pattern end
TRI_LEVELS = (("target", "目標"), ("target_conservative", "保守目標"), ("invalidation_price", "失效"))


def tri_chart_file_name(row: Mapping[str, Any]) -> str:
    safe_sym = re.sub(r"[^A-Za-z0-9]", "_", str(row["symbol"])).strip("_") or "SYM"
    src = f"{row.get('exchange', 'binance')}_{row.get('market', 'spot')}"
    digest = hashlib.sha1(str(row["pattern_id"]).encode("utf-8")).hexdigest()[:10]
    return f"tri_{src}_{safe_sym}_{row['timeframe']}_{_unix(row['start_time'])}_{digest}.json"


def _sample_x(a: int, b: int, n: int) -> list[int]:
    if b <= a:
        return [a]
    return sorted({int(round(a + (b - a) * i / n)) for i in range(n + 1)})


def tri_chart_for(row: Mapping[str, Any], df: pd.DataFrame) -> dict[str, Any]:
    """Chart data for one triangle, times in UNIX seconds (UTC); every price is computed here (D17).

    bars: [[time, o, h, l, c, v]] from BARS_BEFORE_LS bars before the first pivot to the last closed bar.
    resistance / support: [{time, price}] along each line (log-linear between the row's start and end prices),
      split into `*_line` (first pivot -> pattern end) and `*_ext` (pattern end -> apex, dashed on the chart).
    future_times: one time per bar after the last closed bar that the extensions reach (the chart's time axis is
      bar-indexed, so the front end adds them as empty bars); spacing = the pattern's mean bar duration.
    apex: {time, price, shown}: shown=False when the extension stops TRI_MAX_EXTENSION_OF_LENGTH x the pattern
      length past the pattern end before reaching the apex."""
    ts = get_timestamps(df)
    s = int(ts.get_loc(pd.Timestamp(row["start_time"])))
    end = int(ts.get_loc(pd.Timestamp(row["end_time"])))
    last = len(df) - 1
    start = max(0, s - BARS_BEFORE_LS)
    seg = df.iloc[start:last + 1]
    bars = [[_unix(t), _num(o), _num(h), _num(lo), _num(c), _num(v)]
            for t, o, h, lo, c, v in zip(ts[start:last + 1], seg["open"], seg["high"], seg["low"], seg["close"],
                                         seg["volume"])]
    span = max(1, end - s)
    lines = {}
    for side in ("resistance", "support"):
        p0, p1 = math.log(float(row[f"{side}_start_price"])), math.log(float(row[f"{side}_end_price"]))
        lines[side] = (p0, (p1 - p0) / span)
    apex_x = last + float(row["apex_bars_ahead"])
    limit = end + int(math.ceil(TRI_MAX_EXTENSION_OF_LENGTH * span))
    x_end = max(end, min(int(round(apex_x)), limit))
    shown = int(round(apex_x)) <= limit
    step = (ts[last] - ts[s]) / max(1, last - s)

    def t_at(x: int) -> int:
        return _unix(ts[x]) if x <= last else _unix(ts[last] + step * (x - last))

    def price(side: str, x: int) -> float:
        if shown and x == x_end and x_end > end:
            return float(row["apex_price"])  # both lines meet at the apex
        a, b = lines[side]
        return math.exp(a + b * (x - s))

    out = {}
    for side in ("resistance", "support"):
        out[f"{side}_line"] = [{"time": t_at(x), "price": _num(price(side, x))} for x in _sample_x(s, end, TRI_LINE_SAMPLES)]
        out[f"{side}_ext"] = ([{"time": t_at(x), "price": _num(price(side, x))}
                               for x in _sample_x(end, x_end, TRI_LINE_SAMPLES)] if x_end > end else [])
    touches = [{"time": _unix(t["time"]), "price": _num(t["price"]), "side": t["side"]} for t in row["touches"]]
    breakout = None
    if row.get("breakout_time"):
        breakout = {"time": _unix(row["breakout_time"]), "price": _num(row["breakout_price"]),
                    "direction": row["breakout_direction"]}
    levels = [{"key": key, "label": label, "price": _num(row[key])}
              for key, label in TRI_LEVELS if row.get(key) is not None]
    return {
        "bars": bars,
        **out,
        "future_times": [t_at(x) for x in range(last + 1, x_end + 1)],
        "touches": touches,
        "breakout": breakout,
        "levels": levels,
        "levels_estimated": bool(row.get("target_estimated")),
        "apex": {"time": t_at(x_end) if shown else _unix(row["apex_time"]), "price": _num(row["apex_price"]),
                 "shown": shown},
    }


def tri_shape_for(chart: Mapping[str, Any]) -> dict[str, Any]:
    """List silhouette: each line's two ends ([time, price]), the breakout and the last close."""
    def ends(side):
        pts = chart[f"{side}_line"] + chart[f"{side}_ext"]
        return [[pts[0]["time"], pts[0]["price"]], [pts[-1]["time"], pts[-1]["price"]]]
    last = chart["bars"][-1]
    brk = chart["breakout"]
    return {"resistance": ends("resistance"), "support": ends("support"),
            "breakout": [brk["time"], brk["price"]] if brk else None, "last": [last[0], last[4]]}


def build_triangle_payload(symbol_data: Mapping[str, Mapping[str, pd.DataFrame]], tri_cfg: Mapping[str, Any], *,
                           symbols: list[str], timeframes: list[str], generated_at: datetime,
                           scheduled: bool = False, symbols_source: str = "list",
                           instruments: Mapping[str, Instrument] | None = None,
                           sources: Sequence[str] | None = None, market: str = "crypto",
                           next_scan: Callable[[datetime], datetime] | None = None,
                           detect: Callable | None = None) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """(triangles.json payload, {tri_bars file name: chart}). A detector exception on one series is recorded in
    `failures` (stage "triangle") and the other series continue."""
    from ihs_scanner.keys import triangle_key
    from ihs_scanner.triangles import detect_triangles, sort_triangles
    detect = detect or detect_triangles
    next_scan = next_scan or next_scan_time
    instruments = instruments or {}

    def inst_of(sym: str) -> Instrument:
        return instruments.get(sym) or _default_instrument(sym)

    rows_raw: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    t0 = time.perf_counter()
    per_series_ms: list[float] = []
    for sym, by_tf in symbol_data.items():
        for tf, df in by_tf.items():
            t1 = time.perf_counter()
            try:
                rows_raw.extend(detect(df, tf, tri_cfg, symbol=sym))
            except Exception as exc:  # one bad series must not sink the others
                inst = inst_of(sym)
                failures.append({"symbol": sym, "timeframe": tf, "stage": "triangle", "exchange": inst.exchange,
                                 "market": inst.market, "error": _short_error(exc)})
            per_series_ms.append((time.perf_counter() - t1) * 1000)
    detect_seconds = time.perf_counter() - t0
    per_series_ms.sort()
    timing = ({"mean": round(sum(per_series_ms) / len(per_series_ms), 2),
               "p95": round(per_series_ms[int(0.95 * (len(per_series_ms) - 1))], 2),
               "max": round(per_series_ms[-1], 2)} if per_series_ms else None)
    for r in rows_raw:
        inst = inst_of(r["symbol"])
        r["exchange"], r["market"], r["tv_symbol"] = inst.exchange, inst.market, inst.tv_symbol
        if inst.name:
            r["name"] = inst.name
        r["pattern_id"] = f"{inst.exchange}|{inst.market}|{r['pattern_id']}"
    rows, charts = [], {}
    for r in sort_triangles(rows_raw):
        try:
            chart = tri_chart_for(r, symbol_data[r["symbol"]][r["timeframe"]])
        except Exception as exc:
            failures.append({"symbol": r["symbol"], "timeframe": r["timeframe"], "stage": "triangle_chart",
                             "exchange": r["exchange"], "market": r["market"], "error": _short_error(exc)})
            continue
        row = {k: _to_jsonable(v) if not isinstance(v, (list, dict)) else v for k, v in r.items()}
        row["rank"] = len(rows) + 1
        row["pattern_key"] = triangle_key(row)
        name = tri_chart_file_name(row)
        row["chart_file"] = f"{TRI_BARS_DIRNAME}/{name}"
        row["shape"] = tri_shape_for(chart)
        charts[name] = {"schema_version": TRI_SCHEMA_VERSION, "pattern_id": row["pattern_id"], "symbol": row["symbol"],
                        "exchange": row["exchange"], "market": row["market"], "timeframe": row["timeframe"],
                        "triangle_type": row["triangle_type"], **({"name": row["name"]} if row.get("name") else {}),
                        **chart}
        rows.append(row)
    last_closed: dict[str, str | None] = {}
    for tf in timeframes:
        stamps = [get_timestamps(by_tf[tf])[-1] for by_tf in symbol_data.values() if tf in by_tf and len(by_tf[tf])]
        last_closed[tf] = _iso(max(stamps)) if stamps else None
    cfg_json = json.loads(json.dumps(dict(tri_cfg), default=str))
    payload = {
        "schema_version": TRI_SCHEMA_VERSION,
        "kind": "triangles",
        "market": market,
        "generated_at": generated_at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "next_scan_at": next_scan(generated_at).strftime("%Y-%m-%dT%H:%M:%SZ") if scheduled else None,
        "settings": {
            "symbols_source": symbols_source,
            "timeframes": list(timeframes),
            "sources": list(sources) if sources else [],
            "include_stale": bool(tri_cfg["include_stale"]),
            "breakout_price": str(tri_cfg["breakout_price"]),
            "config": cfg_json,  # every threshold in effect (config.TRIANGLE_CONFIG + market overrides)
        },
        "requested_symbols": len(symbols),
        "scanned_symbols": sum(1 for by_tf in symbol_data.values() if by_tf),
        "series_total": len(symbols) * len(timeframes),
        "series_scanned": sum(len(by_tf) for by_tf in symbol_data.values()),
        "detect_seconds": round(detect_seconds, 3),
        "detect_ms_per_series": timing,  # {mean, p95, max} milliseconds
        "failures": failures,
        "last_closed": last_closed,
        "results": rows,
    }
    return payload, charts


def write_triangles(payload: Mapping[str, Any], charts: Mapping[str, Mapping[str, Any]], out_dir: Path) -> dict[str, Any]:
    """Same swap-in-at-the-end order as write_site: tri_bars.tmp/ first, then triangles.json."""
    out_dir.mkdir(parents=True, exist_ok=True)
    tmp_dir = out_dir / (TRI_BARS_DIRNAME + ".tmp")
    if tmp_dir.exists():
        shutil.rmtree(tmp_dir)
    tmp_dir.mkdir()
    total = 0
    for name, chart in charts.items():
        path = tmp_dir / name
        _dump(chart, path)
        total += path.stat().st_size
    out = out_dir / TRI_JSON
    tmp = out.with_suffix(out.suffix + ".tmp")
    _dump(payload, tmp)
    _replace_dir(tmp_dir, out_dir / TRI_BARS_DIRNAME)
    tmp.replace(out)
    return {"json": out.stat().st_size, "bars": total, "bars_files": len(charts)}


def run_triangles(symbol_data: Mapping[str, Mapping[str, pd.DataFrame]], *, scan_out: Path, market: str,
                  include_stale: bool, generated_at: str, scheduled: bool, symbols: list[str],
                  timeframes: list[str], instruments: Mapping[str, Instrument] | None = None,
                  sources: Sequence[str] | None = None, symbols_source: str = "list",
                  next_scan: Callable[[datetime], datetime] | None = None) -> dict[str, Any] | None:
    """Detect + write triangles next to scan_out. Never raises (D38: triangles must not fail the IHS build);
    returns a small summary, or None when nothing was written."""
    from ihs_scanner.config import triangle_config
    try:
        cfg = triangle_config({"include_stale": bool(include_stale)}, market=market)
        gen = datetime.strptime(generated_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        payload, charts = build_triangle_payload(
            symbol_data, cfg, symbols=symbols, timeframes=timeframes, generated_at=gen, scheduled=scheduled,
            symbols_source=symbols_source, instruments=instruments, sources=sources, market=market,
            next_scan=next_scan)
        failed = {(f["symbol"], f["timeframe"]) for f in payload["failures"] if f["stage"] == "triangle"}
        scanned = payload["series_scanned"]
        if scanned and len(failed) / scanned > MAX_FAILURE_RATIO:
            print(f"[triangles FAIL] {len(failed)} of {scanned} series failed; {TRI_JSON} NOT updated "
                  f"(first error: {payload['failures'][0]['error']})", file=sys.stderr)
            return None
        sizes = write_triangles(payload, charts, Path(scan_out).parent)
    except Exception as exc:  # noqa: BLE001 -- isolation is the point
        print(f"[triangles FAIL] {_short_error(exc)}; {TRI_JSON} NOT updated (head-and-shoulders output is fine)",
              file=sys.stderr)
        return None
    counts: dict[str, dict[str, int]] = {"type": {}, "status": {}, "timeframe": {}}
    for r in payload["results"]:
        for key, field in (("type", "triangle_type"), ("status", "status"), ("timeframe", "timeframe")):
            counts[key][r[field]] = counts[key].get(r[field], 0) + 1
    print(f"[triangles] {len(payload['results'])} triangles in {payload['detect_seconds']:.1f}s detect over "
          f"{scanned} series (ms per series {payload['detect_ms_per_series']}); failures={len(payload['failures'])}; by type {counts['type']}, status "
          f"{counts['status']}, timeframe {counts['timeframe']}; wrote {TRI_JSON} ({sizes['json'] / 1024:.0f} KB) + "
          f"{sizes['bars_files']} {TRI_BARS_DIRNAME} files ({sizes['bars'] / 1024:.0f} KB)")
    return {"count": len(payload["results"]), "counts": counts, "detect_seconds": payload["detect_seconds"],
            "failures": len(payload["failures"])}


Fetcher = Callable[[Instrument, str, int, Any], pd.DataFrame]


def _interleave(tasks_by_source: Mapping[str, list]) -> list:
    """Round-robin across sources so each source's own rate limiter works in parallel with the others."""
    out, lists = [], [list(v) for v in tasks_by_source.values()]
    for i in range(max((len(v) for v in lists), default=0)):
        out.extend(v[i] for v in lists if i < len(v))
    return out


def fetch_all(instruments: list[Instrument], timeframes: list[str], limit: int, *,
              fetchers: Mapping[str, Fetcher], workers: int = MAX_WORKERS, progress_every: int = 300,
              status: Callable[[], str] | None = None):
    """Fetch every instrument x timeframe with <= `workers` threads; each source has its own fetcher
    (and therefore its own rate limiter).

    Returns (symbol_data, failures, skipped); empty series (e.g. a pair listed this week has no
    closed weekly bar yet) are `skipped`, not failures. Failures record exchange and market."""
    local = threading.local()

    def session():
        if not hasattr(local, "s"):
            local.s = requests.Session()
        return local.s

    def job(inst: Instrument, tf: str):
        return fetchers[inst.source](inst, tf, limit_for(tf, limit), session())

    by_source: dict[str, list] = {}
    for inst in instruments:
        by_source.setdefault(inst.source, []).extend((inst, tf) for tf in timeframes)
    tasks = _interleave(by_source)
    symbol_data: dict[str, dict[str, pd.DataFrame]] = {}
    failures: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    done = 0
    with ThreadPoolExecutor(max_workers=max(1, min(workers, MAX_WORKERS))) as pool:
        futs = {pool.submit(job, inst, tf): (inst, tf) for inst, tf in tasks}
        for fut in as_completed(futs):
            inst, tf = futs[fut]
            sym = inst.symbol
            done += 1
            try:
                df = fut.result()
            except FETCH_ERRORS as exc:
                failures.append({"symbol": sym, "timeframe": tf, "stage": "fetch", "exchange": inst.exchange,
                                 "market": inst.market, "error": _short_error(exc)})
                print(f"[fetch FAIL] {inst.source} {sym} {tf}: {_short_error(exc)}", file=sys.stderr)
            else:
                if df.empty:
                    skipped.append({"symbol": sym, "timeframe": tf, "exchange": inst.exchange,
                                    "market": inst.market, "reason": "no closed klines yet"})
                else:
                    symbol_data.setdefault(sym, {})[tf] = df
            if done % progress_every == 0 or done == len(tasks):
                print(f"[fetch] {done}/{len(tasks)} series, failures={len(failures)}"
                      + (f", {status()}" if status else ""))
    # deterministic order (as_completed is not)
    order = {i.symbol: n for n, i in enumerate(instruments)}
    symbol_data = {s: {tf: symbol_data[s][tf] for tf in timeframes if tf in symbol_data[s]}
                   for s in sorted(symbol_data, key=order.get)}
    key = lambda f: (order.get(f["symbol"], 0), timeframes.index(f["timeframe"]))  # noqa: E731
    return symbol_data, sorted(failures, key=key), sorted(skipped, key=key)


def spot_instruments(symbols: Sequence[str]) -> list[Instrument]:
    return [Instrument(base=s[:-4] if s.endswith("USDT") and len(s) > 4 else s, symbol=s, native=s,
                       exchange="binance", market="spot") for s in symbols]


def make_sources(args, spot_limiter: WeightLimiter) -> dict[str, dict[str, Any]]:
    """key -> {"list": f(session) -> [Instrument], "fetch": f(inst, tf, limit, session) -> DataFrame,
    "limiter": rate limiter or None, optional "max_position": f(session, [Instrument]) -> {native: USDT or None}
    (D45, perps only)}. Looked up through module globals so tests can monkeypatch."""
    perp_limiter = WeightLimiter(threshold=data_binance_futures.DEFAULT_WEIGHT_THRESHOLD)
    okx_limiter, gate_limiter = data_okx.new_limiter(), data_gate.new_limiter()
    okx_tiers_limiter = data_okx.new_tiers_limiter()
    use_all = args.symbols.strip().lower() == "all"

    def list_spot(session):
        symbols = list_usdt_symbols(limiter=spot_limiter) if use_all else \
            list(dict.fromkeys(s.upper() for s in _csv_list(args.symbols)))
        if args.min_quote_volume > 0:
            vols = fetch_quote_volumes(limiter=spot_limiter)
            before = len(symbols)
            symbols = [s for s in symbols if vols.get(s, 0.0) >= args.min_quote_volume]
            print(f"[symbols] {len(symbols)} of {before} have 24h quote volume >= {args.min_quote_volume:,.0f} USDT")
        return spot_instruments(symbols)

    return {
        "binance-spot": {
            "list": list_spot, "limiter": spot_limiter,
            "fetch": lambda inst, tf, limit, sess: fetch_klines(inst.native, tf, limit, session=sess,
                                                                limiter=spot_limiter)},
        "binance-perp": {
            "list": lambda sess: data_binance_futures.list_instruments(sess, limiter=perp_limiter),
            "limiter": perp_limiter,
            "fetch": lambda inst, tf, limit, sess: data_binance_futures.fetch_klines(
                inst.native, tf, limit, session=sess, limiter=perp_limiter)},
        "okx-spot": {
            "list": lambda sess: data_okx.list_instruments(sess, "spot", limiter=okx_limiter),
            "limiter": okx_limiter,
            "fetch": lambda inst, tf, limit, sess: data_okx.fetch_klines(inst.native, tf, limit, session=sess,
                                                                         limiter=okx_limiter)},
        "okx-perp": {
            "list": lambda sess: data_okx.list_instruments(sess, "perp", limiter=okx_limiter),
            "limiter": okx_limiter,
            "max_position": lambda sess, insts: data_okx.max_position_values(
                sess, [i.native for i in insts], limiter=okx_limiter, tiers_limiter=okx_tiers_limiter),
            "fetch": lambda inst, tf, limit, sess: data_okx.fetch_klines(inst.native, tf, limit, session=sess,
                                                                         limiter=okx_limiter)},
        "gate-perp": {
            "list": lambda sess: data_gate.list_instruments(sess, limiter=gate_limiter),
            "limiter": gate_limiter,
            "max_position": lambda sess, insts: data_gate.max_position_values(
                sess, [i.native for i in insts], limiter=gate_limiter),
            "fetch": lambda inst, tf, limit, sess: data_gate.fetch_klines(inst.native, tf, limit, session=sess,
                                                                          limiter=gate_limiter)},
    }


def position_filter_meta(pf: PositionFilterResult, min_value: float, source_keys: Sequence[str]) -> dict[str, Any]:
    """D45 scan.json `max_position_filter`: threshold, filtered sources, every excluded instrument (and which source
    supplies that coin instead, if any), lookups without a value (kept) and failed lookups (kept)."""
    return {
        "min_usdt": min_value,
        "applied_sources": [k for k in source_keys if k in pf.checked],
        "not_applied": {k: MAX_POSITION_NOT_APPLIED[k] for k in source_keys if k in MAX_POSITION_NOT_APPLIED},
        "checked": dict(pf.checked),
        "unknown": dict(pf.unknown),
        "excluded": list(pf.excluded),
        "errors": list(pf.errors),
    }


def print_position_filter(meta: Mapping[str, Any]) -> None:
    if meta["min_usdt"] <= 0:
        print("[max position] filter off")
        return
    parts = []
    for k in meta["applied_sources"]:
        n_ex = sum(1 for e in meta["excluded"] if e["source"] == k)
        parts.append(f"{k}: {meta['checked'].get(k, 0)} checked, {n_ex} excluded, "
                     f"{meta['unknown'].get(k, 0)} unknown (kept)")
    print(f"[max position] < {meta['min_usdt']:,.0f} USDT: " + ("; ".join(parts) or "no perp source checked"))
    for e in meta["excluded"]:
        print(f"  excluded {e['source']} {e['symbol']} max {e['max_position_usdt']:,.0f} USDT"
              + (f" -> scanned from {e['replaced_by']}" if e["replaced_by"] else ""))
    for e in meta["errors"]:
        print(f"[max position FAIL] {e['source']}: {e['error']} (all kept)", file=sys.stderr)


def parse_sources(text: str | None) -> list[str]:
    """None -> DEFAULT_SOURCES; "all" -> every source; else a comma list (priority order is fixed)."""
    if text is None or text.strip().lower() in ("", "default"):
        keys = list(DEFAULT_SOURCES)
    elif text.strip().lower() == "all":
        keys = list(ALL_SOURCES)
    else:
        keys = _csv_list(text.lower())
    bad = [k for k in keys if k not in ALL_SOURCES]
    if bad or not keys:
        raise ValueError(f"unknown sources {bad}; choose from {list(ALL_SOURCES)}, 'default' or 'all'")
    return [k for k in ALL_SOURCES if k in keys]


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Scan exchange public klines and write site/data/")
    p.add_argument("--market", choices=MARKETS, default="crypto",
                   help="crypto (default) or tw = Taiwan listed + OTC stocks (D29; written to site/data/tw/)")
    p.add_argument("--tw-include-etf", action="store_true",
                   help="tw: also scan ETFs (00xx codes); default is common stocks only")
    p.add_argument("--tw-rps", type=float, default=None,
                   help="tw: Yahoo requests per second over all workers (default 5)")
    p.add_argument("--sources", default=None,
                   help="comma list of %s; 'default' = %s, 'all' = every source (binance-perp is blocked "
                        "from US IPs). One source per base coin, by that priority." %
                        (",".join(ALL_SOURCES), ",".join(DEFAULT_SOURCES)))
    p.add_argument("--symbols", default="all",
                   help='"all" = every instrument of the chosen sources (default); a comma list of Binance spot '
                        'symbols (debug) restricts the scan to those, binance-spot only; tw: stock codes, e.g. 2330,6488')
    p.add_argument("--timeframes", default="4h,1d,1w", help="comma separated subset of 4h,1d,1w")
    p.add_argument("--limit", type=int, default=1000,
                   help="closed klines per symbol/timeframe (1w is capped at %d)" % LIMIT_CAP_BY_TIMEFRAME["1w"])
    p.add_argument("--min-quote-volume", type=float, default=0.0,
                   help="binance-spot only: skip symbols whose 24h quote volume (USDT) is below this; 0 = no filter")
    p.add_argument("--min-max-position", type=float, default=MIN_MAX_POSITION_USDT,
                   help="D45: skip OKX / Gate perpetuals whose largest openable position is worth less than this "
                        "many USDT (spot is not filtered); 0 = off (default %(default).0f)")
    p.add_argument("--workers", type=int, default=MAX_WORKERS, help=f"fetch threads (max {MAX_WORKERS})")
    p.add_argument("--weight-threshold", type=int, default=4500,
                   help="binance-spot: pause until the next minute when used weight/min would exceed this "
                        "(IP limit 6000)")
    p.add_argument("--include-stale", action="store_true",
                   help="also include stale patterns (for local preview; production leaves this off)")
    p.add_argument("--out", default=None,
                   help="output JSON path (default site/data/scan.json; --market tw: site/data/tw/scan.json)")
    p.add_argument("--scheduled", action="store_true",
                   help="a cron runs this every 4h: publish next_scan_at (automatic when GITHUB_EVENT_NAME=schedule)")
    args = p.parse_args(argv)
    if args.out is None:
        args.out = str(DEFAULT_OUT_TW if args.market == "tw" else DEFAULT_OUT)
    return args


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass
    t0 = time.perf_counter()
    args = parse_args(argv)
    if args.market == "tw":  # D29: separate pipeline (stock list + Yahoo), same detector and payload
        import build_site_tw
        return build_site_tw.main_tw(args, t0)
    timeframes = _csv_list(args.timeframes)
    bad = [tf for tf in timeframes if tf not in TIMEFRAMES]
    if not timeframes or bad or args.limit < 1:
        print(f"error: need timeframes from {TIMEFRAMES} and limit >= 1; bad timeframes: {bad}", file=sys.stderr)
        return 2
    use_all = args.symbols.strip().lower() == "all"
    try:
        source_keys = parse_sources(args.sources) if use_all else ["binance-spot"]
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    spot_limiter = WeightLimiter(threshold=args.weight_threshold)
    sources = make_sources(args, spot_limiter)

    # 1) list each source's instruments; only binance-spot (the primary source, D3) is fatal.
    list_session = requests.Session()
    by_source: dict[str, list[Instrument]] = {}
    source_errors: list[dict[str, Any]] = []
    for key in source_keys:
        try:
            by_source[key] = sources[key]["list"](list_session)
        except FETCH_ERRORS as exc:
            if key == "binance-spot":
                print(f"error: could not get the symbol list: {_short_error(exc)}; {args.out} was NOT updated",
                      file=sys.stderr)
                return 1
            source_errors.append({"source": key, "stage": "list", "error": _short_error(exc)})
            print(f"[source FAIL] {key}: could not list instruments: {_short_error(exc)}", file=sys.stderr)
    pf = merge_with_position_limit(  # D45: drop small-limit perps before any K line is fetched
        by_source, {k: (lambda insts, _f=sources[k]["max_position"]: _f(list_session, insts))
                    for k in by_source if sources[k].get("max_position")},
        float(args.min_max_position), errors=FETCH_ERRORS)
    merged = pf.merged
    position_filter = position_filter_meta(pf, float(args.min_max_position), list(by_source))
    print_position_filter(position_filter)
    if not merged:
        print("error: no symbols to scan", file=sys.stderr)
        return 2
    source_listed = {k: len(v) for k, v in by_source.items()}
    kept = {k: sum(1 for i in merged if i.source == k) for k in by_source}
    print("[sources] " + ", ".join(f"{k}: {source_listed[k]} listed -> {kept[k]} scanned" for k in by_source)
          + f"; {len(merged)} unique coins")
    symbols = [i.symbol for i in merged]
    instruments = {i.symbol: i for i in merged}

    fetchers = {k: sources[k]["fetch"] for k in by_source}
    symbol_data, failures, skipped = fetch_all(
        merged, timeframes, args.limit, fetchers=fetchers, workers=args.workers,
        status=lambda: f"binance max weight/min={spot_limiter.max_seen}")
    t_fetch = time.perf_counter() - t0

    cfg = merge_config({"include_stale": bool(args.include_stale)})
    scheduled = bool(args.scheduled) or os.environ.get("GITHUB_EVENT_NAME") == "schedule"
    payload, charts = build_payload(symbol_data, cfg, symbols=symbols, timeframes=timeframes, limit=args.limit,
                                    scheduled=scheduled, failures=failures, skipped=skipped,
                                    symbols_source="all" if use_all else "list", instruments=instruments,
                                    sources=source_keys, source_listed=source_listed,
                                    source_errors=source_errors, position_filter=position_filter)
    total = payload["series_total"]
    failed_series = {(f["symbol"], f["timeframe"]) for f in payload["failures"]}
    ratio = len(failed_series) / total if total else 1.0
    if ratio > MAX_FAILURE_RATIO:
        print(f"error: {len(failed_series)} of {total} series failed ({ratio:.0%} > {MAX_FAILURE_RATIO:.0%}); "
              f"{args.out} was NOT updated", file=sys.stderr)
        return 1

    sizes = write_site(payload, charts, Path(args.out))
    counts: dict[str, int] = {}
    for r in payload["results"]:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    elapsed = time.perf_counter() - t0
    print(f"\nscanned {payload['scanned_symbols']} symbols / {payload['series_scanned']} of {total} series "
          f"in {elapsed:.1f}s (fetch {t_fetch:.1f}s); failures={len(payload['failures'])} "
          f"skipped={len(payload['skipped'])}")
    for key, c in payload["source_counts"].items():
        print(f"  {key}: {c['symbols']} symbols, {c['series_scanned']} series scanned, "
              f"{c['failed_series']} failed series")
    print(f"max X-MBX-USED-WEIGHT-1M seen: {spot_limiter.max_seen}; throttle waits: {spot_limiter.throttle_waits} "
          f"({spot_limiter.slept:.1f}s)")
    print(f"wrote {args.out} ({sizes['scan_json'] / 1024:.0f} KB) + {sizes['bars_files']} bars files "
          f"({sizes['bars'] / 1024:.0f} KB): {len(payload['results'])} patterns {counts}; "
          f"next scan ~{payload['next_scan_at'] or 'not scheduled'}")
    run_triangles(symbol_data, scan_out=Path(args.out), market="crypto", include_stale=bool(args.include_stale),
                  generated_at=payload["generated_at"], scheduled=scheduled, symbols=symbols, timeframes=timeframes,
                  instruments=instruments, sources=source_keys, symbols_source="all" if use_all else "list")
    return 0


if __name__ == "__main__":
    sys.exit(main())
