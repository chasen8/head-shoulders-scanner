"""Binance PUBLIC kline fetcher (charter D3). No API key, no trading permission.

Primary endpoint: https://data-api.binance.vision/api/v3/klines
Fallback:         https://api.binance.com/api/v3/klines
Only closed candles are kept (close_time < now).
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Sequence

import pandas as pd
import requests

PRIMARY_BASE = "https://data-api.binance.vision"
FALLBACK_BASE = "https://api.binance.com"
KLINES_PATH = "/api/v3/klines"
INTERVALS = {"4h": "4h", "1d": "1d", "1w": "1w"}
MAX_PER_REQUEST = 1000
DEFAULT_CACHE_DIR = Path(r"C:\trading-data\ihs-scanner-v2\data")
COLUMNS = ["timestamp", "open", "high", "low", "close", "volume", "close_time"]


class BinanceFetchError(RuntimeError):
    pass


def _get_json(session, params: dict, bases: Sequence[str], timeout: float):
    errors = []
    for base in bases:
        url = base + KLINES_PATH
        try:
            resp = session.get(url, params=params, timeout=timeout)
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:  # network error, HTTP 451/429, bad JSON...
            errors.append(f"{url}: {exc!r}")
    raise BinanceFetchError("all kline endpoints failed: " + " | ".join(errors))


def _rows_to_frame(rows: list) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame(columns=COLUMNS)
    raw = pd.DataFrame([r[:7] for r in rows],
                       columns=["open_time", "open", "high", "low", "close", "volume", "close_time"])
    df = pd.DataFrame({
        "timestamp": pd.to_datetime(raw["open_time"].astype("int64"), unit="ms", utc=True),
        "open": raw["open"].astype(float),
        "high": raw["high"].astype(float),
        "low": raw["low"].astype(float),
        "close": raw["close"].astype(float),
        "volume": raw["volume"].astype(float),
        "close_time": pd.to_datetime(raw["close_time"].astype("int64"), unit="ms", utc=True),
    })
    return df.drop_duplicates("timestamp").sort_values("timestamp").reset_index(drop=True)


def cache_path(symbol: str, timeframe: str, cache_dir: str | Path = DEFAULT_CACHE_DIR) -> Path:
    return Path(cache_dir) / f"{symbol.upper()}_{timeframe}.csv"


def load_cached_klines(symbol: str, timeframe: str,
                       cache_dir: str | Path = DEFAULT_CACHE_DIR) -> pd.DataFrame | None:
    path = cache_path(symbol, timeframe, cache_dir)
    if not path.exists():
        return None
    df = pd.read_csv(path, encoding="utf-8")
    for col in ("timestamp", "close_time"):
        df[col] = pd.to_datetime(df[col], utc=True)
    return df


def fetch_klines(symbol: str, timeframe: str, limit: int = 1000, *,
                 cache_dir: str | Path | None = None,
                 session=None,
                 now_ms: int | None = None,
                 timeout: float = 15.0,
                 bases: Sequence[str] = (PRIMARY_BASE, FALLBACK_BASE)) -> pd.DataFrame:
    """Return the most recent `limit` CLOSED klines, oldest first.

    Paginates backwards with endTime when limit > 1000. If cache_dir is given the
    result is also written to <cache_dir>/<SYMBOL>_<tf>.csv.
    """
    if timeframe not in INTERVALS:
        raise ValueError(f"unsupported timeframe {timeframe!r}")
    if limit < 1:
        raise ValueError("limit must be >= 1")
    session = session or requests.Session()
    need = limit + 1  # one extra: the newest candle is usually still open
    rows: list = []
    end_time = None
    while len(rows) < need:
        batch = min(MAX_PER_REQUEST, need - len(rows))
        params = {"symbol": symbol.upper(), "interval": INTERVALS[timeframe], "limit": batch}
        if end_time is not None:
            params["endTime"] = end_time
        data = _get_json(session, params, bases, timeout)
        if not data:
            break
        rows = list(data) + rows
        end_time = int(data[0][0]) - 1
        if len(data) < batch:
            break  # no more history
    df = _rows_to_frame(rows)
    now = pd.Timestamp(now_ms if now_ms is not None else int(time.time() * 1000), unit="ms", tz="UTC")
    df = df[df["close_time"] < now].tail(limit).reset_index(drop=True)
    if cache_dir is not None:
        path = cache_path(symbol, timeframe, cache_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(path, index=False, encoding="utf-8")
    return df
