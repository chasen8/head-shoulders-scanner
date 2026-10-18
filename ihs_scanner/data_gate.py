"""Gate.io PUBLIC USDT-margined perpetual futures data. No key, no trading permission.

Why Gate: Binance USDT-M futures (fapi.binance.com) answers HTTP 451 on GitHub's US runners and Bybit
answers 403 (probed 2026-10-18), while api.gateio.ws answers 200 and lists perps for the small coins
that exist only as contracts. Endpoints:
  GET /api/v4/futures/usdt/contracts
  GET /api/v4/futures/usdt/candlesticks?contract=BTC_USDT&interval=4h|1d|1w&limit=<=2000   (1 call/series)
Public rate limit 200 req / 10 s per IP; we pace at 10 req/s and back off on HTTP 429.
Volume is in contracts (consistent per symbol, which is all the detector's relative volume needs).
"""
from __future__ import annotations

import time
from typing import Any, Mapping

import pandas as pd
import requests

from .data_binance import EXCLUDED_BASE_ASSETS
from .sources import IntervalLimiter, Instrument, SourceFetchError, get_json, keep_base, ohlcv_frame

BASE_URL = "https://api.gateio.ws"
CONTRACTS_PATH = "/api/v4/futures/usdt/contracts"
CANDLES_PATH = "/api/v4/futures/usdt/candlesticks"
INTERVALS = {"4h": "4h", "1d": "1d", "1w": "1w"}
MAX_PER_REQUEST = 2000
REQUESTS_PER_SECOND = 10.0


def new_limiter() -> IntervalLimiter:
    return IntervalLimiter(1.0 / REQUESTS_PER_SECOND)


def filter_contracts(rows: list[Mapping[str, Any]], excluded=EXCLUDED_BASE_ASSETS) -> list[Instrument]:
    """status trading, not delisting, linear ('direct') USDT contracts named <BASE>_USDT."""
    cand = []
    for r in rows:
        name = str(r.get("name", ""))
        if (r.get("status") == "trading" and not r.get("in_delisting") and r.get("type", "direct") == "direct"
                and name.endswith("_USDT") and len(name) > 5):
            cand.append((name[:-5], name))
    bases = {b for b, _ in cand}
    return sorted((Instrument(base=b, symbol=b + "USDT", native=n, exchange="gate", market="perp")
                   for b, n in cand if keep_base(b, bases, excluded)), key=lambda x: x.symbol)


def list_instruments(session, *, limiter: IntervalLimiter | None = None, verbose: bool = True) -> list[Instrument]:
    payload = get_json(session, BASE_URL + CONTRACTS_PATH, None, limiter=limiter, timeout=30.0)
    if not isinstance(payload, list):
        raise SourceFetchError(f"Gate contracts: {str(payload)[:160]}")
    out = filter_contracts(payload)
    if verbose:
        print(f"[symbols] {len(out)} Gate USDT perpetual contracts trading")
    return out


def fetch_klines(contract: str, timeframe: str, limit: int = 1000, *, session=None,
                 limiter: IntervalLimiter | None = None, now_ms: int | None = None,
                 timeout: float = 20.0, sleep=time.sleep) -> pd.DataFrame:
    """Most recent `limit` CLOSED klines, oldest first (single request, up to 2000 bars)."""
    if timeframe not in INTERVALS:
        raise ValueError(f"unsupported timeframe {timeframe!r}")
    if limit < 1:
        raise ValueError("limit must be >= 1")
    session = session or requests.Session()
    params = {"contract": contract, "interval": INTERVALS[timeframe],
              "limit": min(MAX_PER_REQUEST, limit + 1)}  # +1: newest candle is usually still open
    data = get_json(session, BASE_URL + CANDLES_PATH, params, limiter=limiter, timeout=timeout, sleep=sleep)
    if not isinstance(data, list):
        raise SourceFetchError(f"Gate candlesticks {contract}: {str(data)[:160]}")
    rows = [[int(r["t"]) * 1000, r["o"], r["h"], r["l"], r["c"], r["v"]] for r in data]
    return ohlcv_frame(rows, timeframe, now_ms=now_ms, limit=limit)
