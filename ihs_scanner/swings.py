"""Swing high / swing low detection (spec step 1, charter D4).

A swing low at bar i: low[i] is STRICTLY lower than each of the N bars before
and the N bars after. Equal values (flat bottoms) do not count. The pivot is
only known once the N right-hand bars have closed, so confirm_index = i + N,
and the last N bars of any series can never be pivots (no look-ahead).
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from numpy.lib.stride_tricks import sliding_window_view


@dataclass(frozen=True)
class SwingPoint:
    kind: str  # "high" or "low"
    index: int  # bar of the extreme (positional index into df)
    confirm_index: int  # index + N: first bar at which the pivot is known
    price: float  # high (for "high") or low (for "low")
    time: pd.Timestamp  # open time of the extreme bar (UTC)


def get_timestamps(df: pd.DataFrame) -> pd.DatetimeIndex:
    """UTC DatetimeIndex from df["timestamp"] (or the index if there is no such column).

    Numeric timestamps are treated as epoch milliseconds (Binance convention).
    """
    raw = df["timestamp"] if "timestamp" in df.columns else df.index
    if pd.api.types.is_numeric_dtype(raw):
        ts = pd.to_datetime(raw, unit="ms", utc=True)
    else:
        ts = pd.to_datetime(raw, utc=True)
    return pd.DatetimeIndex(ts)


def pivot_mask(values, n: int, kind: str) -> np.ndarray:
    """Boolean mask of strict pivots. kind = "low" or "high"."""
    if n < 1:
        raise ValueError("pivot_window must be >= 1")
    if kind not in ("low", "high"):
        raise ValueError("kind must be 'low' or 'high'")
    arr = np.asarray(values, dtype=float)
    mask = np.zeros(arr.shape[0], dtype=bool)
    if arr.shape[0] < 2 * n + 1:
        return mask
    win = sliding_window_view(arr, 2 * n + 1)
    center = win[:, n]
    others = np.concatenate([win[:, :n], win[:, n + 1:]], axis=1)
    with np.errstate(invalid="ignore"):
        if kind == "low":
            hit = center < others.min(axis=1)
        else:
            hit = center > others.max(axis=1)
    mask[n: arr.shape[0] - n] = hit  # NaN comparisons are False -> no pivot
    return mask


def find_swing_points(df: pd.DataFrame, pivot_window: int) -> dict[str, list[SwingPoint]]:
    """Return {"highs": [...], "lows": [...]}, each sorted by index."""
    n = int(pivot_window)
    times = get_timestamps(df)
    highs = df["high"].to_numpy(dtype=float)
    lows = df["low"].to_numpy(dtype=float)
    out: dict[str, list[SwingPoint]] = {}
    for kind, arr in (("high", highs), ("low", lows)):
        idx = np.flatnonzero(pivot_mask(arr, n, kind))
        out[kind + "s"] = [
            SwingPoint(kind=kind, index=int(i), confirm_index=int(i) + n,
                       price=float(arr[i]), time=times[i])
            for i in idx
        ]
    return out
