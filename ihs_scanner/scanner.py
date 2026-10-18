"""Multi symbol / timeframe scanning, ordering and export (spec steps 10-12)."""
from __future__ import annotations

import json
import math
import time
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import pandas as pd

from .config import merge_config
from .detector import detect_inverse_head_shoulders
from .scoring import result_sort_key

OUTPUT_FIELDS = [
    "symbol", "timeframe", "pattern_type", "mode", "status", "pattern_score",
    "shoulder_score", "head_depth_score", "neckline_score", "breakout_score",
    "LS_time", "LS_price", "N1_time", "N1_price", "H_time", "H_price",
    "N2_time", "N2_price", "RS_time", "RS_price", "RS_confirm_time",
    "shoulder_diff", "head_depth", "neckline_angle", "neckline_slope_pct_per_bar",
    "current_close", "current_neckline_price", "distance_to_neckline",
    "breakout_detected", "breakout_time", "breakout_price", "volume_ratio",
    "average_volume_20", "bars_since_rs", "bars_since_breakout", "signal_time", "as_of_time",
    "pattern_id",
]


def sort_results(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(results, key=result_sort_key)


def scan_symbols(symbol_data: Mapping[str, Mapping[str, pd.DataFrame]],
                 config: Mapping[str, Any] | None = None,
                 timings: dict | None = None) -> list[dict[str, Any]]:
    """symbol_data = {symbol: {timeframe: df}}. Returns sorted results with
    pattern_score >= min_pattern_score. If `timings` is a dict it is filled with
    {(symbol, timeframe): (n_bars, seconds)}."""
    cfg = merge_config(config)
    results: list[dict[str, Any]] = []
    for symbol, by_tf in symbol_data.items():
        for timeframe, df in by_tf.items():
            t0 = time.perf_counter()
            res = detect_inverse_head_shoulders(df, timeframe, cfg, symbol=symbol)
            if timings is not None:
                timings[(symbol, timeframe)] = (len(df), time.perf_counter() - t0)
            results.extend(res)
    min_score = float(cfg["min_pattern_score"])
    results = [r for r in results if r["pattern_score"] >= min_score]
    return sort_results(results)


def _to_jsonable(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, pd.Timestamp):
        if pd.isna(value):
            return None
        ts = value.tz_localize("UTC") if value.tzinfo is None else value.tz_convert("UTC")
        return ts.strftime("%Y-%m-%dT%H:%M:%SZ")
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    if isinstance(value, (int, np.integer)):
        return int(value)
    if isinstance(value, (float, np.floating)):
        f = float(value)
        return f if math.isfinite(f) else None
    return value


def _ordered_rows(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for r in results:
        keys = OUTPUT_FIELDS + [k for k in r if k not in OUTPUT_FIELDS]
        rows.append({k: _to_jsonable(r.get(k)) for k in keys})
    return rows


def export_results(results: list[dict[str, Any]], output_path: str | Path) -> Path:
    """Write results to .csv or .json (by extension). Times are ISO 8601 UTC
    strings ("...Z"); None / NaN become JSON null or an empty CSV cell."""
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = _ordered_rows(results)
    suffix = path.suffix.lower()
    if suffix == ".json":
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(rows, fh, ensure_ascii=False, indent=2, allow_nan=False)
    elif suffix == ".csv":
        frame = pd.DataFrame(rows, columns=rows[0].keys() if rows else OUTPUT_FIELDS)
        frame.to_csv(path, index=False, encoding="utf-8")
    else:
        raise ValueError(f"unsupported output extension {suffix!r} (use .csv or .json)")
    return path
