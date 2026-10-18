"""Binance USDT-M perpetual futures PUBLIC data (fapi). No key, no trading permission.

NOTE: https://fapi.binance.com answers HTTP 451 on GitHub Actions' US runners (probed 2026-10-18), so
this source is OPT-IN (`build_site.py --sources ...,binance-perp`) and works from non-US IPs only. The
default scan covers the same perp-only coins through OKX / Gate perps instead (charter D22).

Rate limits (Binance USDS-M futures docs): IP REQUEST_WEIGHT 2400/min; GET /fapi/v1/klines weight by
limit: <100 -> 1, [100,500) -> 2, [500,1000] -> 5, >1000 -> 10; GET /fapi/v1/exchangeInfo weight 1.
Same X-MBX-USED-WEIGHT-1M header as spot, so `WeightLimiter` is reused (threshold 1800).
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping

import requests

from . import data_binance as spot
from .sources import Instrument, keep_base

BASE_URL = "https://fapi.binance.com"
KLINES_PATH = "/fapi/v1/klines"
EXCHANGE_INFO_PATH = "/fapi/v1/exchangeInfo"
DEFAULT_WEIGHT_THRESHOLD = 1800  # IP limit is 2400/min


def kline_weight(limit: int) -> int:
    if limit < 1:
        raise ValueError("limit must be >= 1")
    return 1 if limit < 100 else 2 if limit < 500 else 5 if limit <= 1000 else 10


def filter_perpetuals(exchange_info: Mapping[str, Any],
                      excluded: Iterable[str] = spot.EXCLUDED_BASE_ASSETS) -> list[Instrument]:
    """contractType PERPETUAL + quoteAsset USDT + status TRADING (delivery contracts excluded)."""
    rows = [s for s in exchange_info.get("symbols", [])
            if s.get("contractType") == "PERPETUAL" and s.get("quoteAsset") == "USDT"
            and s.get("status") == "TRADING"]
    bases = {str(s.get("baseAsset", "")) for s in rows}
    return sorted((Instrument(base=s["baseAsset"], symbol=s["baseAsset"] + "USDT", native=s["symbol"],
                              exchange="binance", market="perp")
                   for s in rows if keep_base(str(s.get("baseAsset", "")), bases, excluded)),
                  key=lambda i: i.symbol)


def list_instruments(session=None, *, limiter: spot.WeightLimiter | None = None, timeout: float = 30.0,
                     verbose: bool = True) -> list[Instrument]:
    session = session or requests.Session()
    info = spot._get_json(session, EXCHANGE_INFO_PATH, None, (BASE_URL,), timeout, limiter=limiter, weight=1)
    out = filter_perpetuals(info)
    if verbose:
        print(f"[symbols] {len(out)} Binance USDT-M perpetuals trading")
    return out


def fetch_klines(symbol: str, timeframe: str, limit: int = 1000, **kwargs):
    """Same contract as data_binance.fetch_klines, against fapi."""
    kwargs.setdefault("bases", (BASE_URL,))
    return spot.fetch_klines(symbol, timeframe, limit, klines_path=KLINES_PATH, weight_fn=kline_weight, **kwargs)
