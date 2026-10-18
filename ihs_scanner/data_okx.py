"""OKX PUBLIC market data (spot + USDT-margined linear swaps). No key, no trading permission.

Reachable from GitHub Actions' US runners (probed 2026-10-18: HTTP 200). Endpoints:
  GET /api/v5/public/instruments?instType=SPOT|SWAP
  GET /api/v5/market/candles?instId=&bar=&limit=<=300&after=<ts>   newest first, up to ~1440 bars
Bars use the UTC-aligned variants: 4H, 1Dutc, 1Wutc (weekly opens Monday 00:00 UTC, same as Binance).
Rate limit: candles 40 req / 2 s per IP; we pace at 12 req/s (min interval 1/12 s) and back off on 429
or error code 50011.
"""
from __future__ import annotations

import time
from typing import Any, Mapping

import pandas as pd
import requests

from .data_binance import EXCLUDED_BASE_ASSETS
from .sources import (IntervalLimiter, Instrument, SourceFetchError, get_json, keep_base, ohlcv_frame)

BASE_URL = "https://www.okx.com"
INSTRUMENTS_PATH = "/api/v5/public/instruments"
CANDLES_PATH = "/api/v5/market/candles"
BARS = {"4h": "4H", "1d": "1Dutc", "1w": "1Wutc"}
MAX_PER_REQUEST = 300
REQUESTS_PER_SECOND = 12.0


def new_limiter() -> IntervalLimiter:
    return IntervalLimiter(1.0 / REQUESTS_PER_SECOND)


def _rate_limited(data: Any) -> bool:
    return isinstance(data, Mapping) and str(data.get("code")) == "50011"


def _data(payload: Any, what: str) -> list:
    if not isinstance(payload, Mapping) or str(payload.get("code")) != "0":
        raise SourceFetchError(f"OKX {what}: {str(payload)[:160]}")
    return payload.get("data") or []


def filter_instruments(rows: list[Mapping[str, Any]], market: str,
                       excluded=EXCLUDED_BASE_ASSETS) -> list[Instrument]:
    """market 'spot': quoteCcy USDT, state live. 'perp': <BASE>-USDT-SWAP, linear, live.
    Stablecoin/fiat bases and leveraged tokens are dropped."""
    cand: list[tuple[str, str]] = []  # (base, instId)
    for r in rows:
        if r.get("state") != "live":
            continue
        inst_id = str(r.get("instId", ""))
        if market == "spot":
            if r.get("quoteCcy") != "USDT" or not inst_id.endswith("-USDT"):
                continue
            cand.append((str(r.get("baseCcy") or inst_id.rsplit("-", 1)[0]), inst_id))
        else:
            if not inst_id.endswith("-USDT-SWAP") or r.get("ctType") != "linear":
                continue
            cand.append((inst_id.split("-")[0], inst_id))
    bases = {b for b, _ in cand}
    return sorted((Instrument(base=b, symbol=b + "USDT", native=i, exchange="okx", market=market)
                   for b, i in cand if keep_base(b, bases, excluded)), key=lambda x: x.symbol)


def list_instruments(session, market: str, *, limiter: IntervalLimiter | None = None,
                     verbose: bool = True) -> list[Instrument]:
    inst_type = "SPOT" if market == "spot" else "SWAP"
    payload = get_json(session, BASE_URL + INSTRUMENTS_PATH, {"instType": inst_type}, limiter=limiter,
                       timeout=30.0, rate_limited=_rate_limited)
    out = filter_instruments(_data(payload, "instruments " + inst_type), market)
    if verbose:
        print(f"[symbols] {len(out)} OKX {market} USDT instruments live")
    return out


def fetch_klines(inst_id: str, timeframe: str, limit: int = 1000, *, session=None,
                 limiter: IntervalLimiter | None = None, now_ms: int | None = None,
                 timeout: float = 20.0, sleep=time.sleep) -> pd.DataFrame:
    """Most recent `limit` CLOSED klines, oldest first. Pages backwards with `after` (<=300 per call)."""
    if timeframe not in BARS:
        raise ValueError(f"unsupported timeframe {timeframe!r}")
    if limit < 1:
        raise ValueError("limit must be >= 1")
    session = session or requests.Session()
    need = limit + 1  # the newest candle is usually still open
    rows: list = []
    after = None
    while len(rows) < need:
        batch = min(MAX_PER_REQUEST, need - len(rows))
        params: dict[str, Any] = {"instId": inst_id, "bar": BARS[timeframe], "limit": batch}
        if after is not None:
            params["after"] = after
        payload = get_json(session, BASE_URL + CANDLES_PATH, params, limiter=limiter, timeout=timeout,
                           rate_limited=_rate_limited, sleep=sleep)
        data = _data(payload, f"candles {inst_id}")
        if not data:
            break
        # each row: [ts, o, h, l, c, vol, volCcy, volCcyQuote, confirm]; confirm "1" = closed
        rows.extend(r[:6] for r in data if str(r[8] if len(r) > 8 else "1") == "1")
        after = data[-1][0]
        if len(data) < batch:
            break  # no more history
    return ohlcv_frame(rows, timeframe, now_ms=now_ms, limit=limit)
