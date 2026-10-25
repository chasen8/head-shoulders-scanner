"""OKX PUBLIC market data (spot + USDT-margined linear swaps). No key, no trading permission.

Reachable from GitHub Actions' US runners (probed 2026-10-18: HTTP 200). Endpoints:
  GET /api/v5/public/instruments?instType=SPOT|SWAP
  GET /api/v5/market/candles?instId=&bar=&limit=<=300&after=<ts>   newest first, up to ~1440 bars
Bars use the UTC-aligned variants: 4H, 1Dutc, 1Wutc (weekly opens Monday 00:00 UTC, same as Binance).
Rate limit: candles 40 req / 2 s per IP; we pace at 12 req/s (min interval 1/12 s) and back off on 429
or error code 50011.
"""
from __future__ import annotations

import math
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


# ---------------------------------------------------------------------------------------------------------------
# D45 max position value of a USDT swap (public endpoints only, no key):
#   GET /api/v5/public/instruments?instType=SWAP        ctVal (base coin per contract), ctMult, instFamily,
#                                                       posLmtAmt / posLmtPct (user-level position limit)
#   GET /api/v5/public/position-tiers?instType=SWAP&tdMode=cross&instFamily=<up to 5, comma separated>
#                                                       maxSz per tier = contracts; the highest tier's maxSz is the
#                                                       largest position one account can hold
#   GET /api/v5/public/mark-price?instType=SWAP          markPx (USDT)
#   GET /api/v5/public/open-interest?instType=SWAP       oiUsd (for the posLmtPct part)
# value (USDT) = min(highest-tier maxSz * ctVal * ctMult * markPx, user limit), user limit = max(posLmtAmt,
#   oiUsd * posLmtPct / 100) when posLmtAmt is set (OKX docs: the effective user limit is max(posLmtAmt,
#   oiUSD x posLmtPct)). posLmtAmt is >= 250,000 USD on every swap (2026-10-25), so the tier term decides.
# Rate limit: position-tiers 10 req / 2 s per IP -> paced at 4 req/s (TIERS_REQUESTS_PER_SECOND).
POSITION_TIERS_PATH = "/api/v5/public/position-tiers"
MARK_PRICE_PATH = "/api/v5/public/mark-price"
OPEN_INTEREST_PATH = "/api/v5/public/open-interest"
TIERS_PER_REQUEST = 5
TIERS_REQUESTS_PER_SECOND = 4.0


def new_tiers_limiter() -> IntervalLimiter:
    return IntervalLimiter(1.0 / TIERS_REQUESTS_PER_SECOND)


def _f(x: Any) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def position_value(inst: Mapping[str, Any], max_sz: float | None, mark: float | None,
                   oi_usd: float | None = None) -> float | None:
    """Max position notional in USDT for one linear USDT swap (formula above); None when a piece is missing."""
    ct_val, ct_mult = _f(inst.get("ctVal")), _f(inst.get("ctMult")) or 1.0
    if max_sz is None or mark is None or ct_val is None or max_sz <= 0 or mark <= 0 or ct_val <= 0:
        return None
    value = max_sz * ct_val * ct_mult * mark
    lmt_amt, lmt_pct = _f(inst.get("posLmtAmt")), _f(inst.get("posLmtPct"))
    if lmt_amt is not None and lmt_amt > 0:
        value = min(value, max(lmt_amt, (oi_usd or 0.0) * (lmt_pct or 0.0) / 100.0))
    return value


def max_position_values(session, inst_ids, *, limiter: IntervalLimiter | None = None,
                        tiers_limiter: IntervalLimiter | None = None, sleep=time.sleep) -> dict[str, float | None]:
    """{instId: max position notional (USDT) or None} for the given <BASE>-USDT-SWAP ids. A failed tiers batch
    leaves its ids None (unknown -> the caller keeps them); a failed instruments / mark-price call raises."""
    inst_ids = list(dict.fromkeys(inst_ids))
    if not inst_ids:
        return {}
    tiers_limiter = tiers_limiter or new_tiers_limiter()

    def public(path: str, what: str) -> list:
        return _data(get_json(session, BASE_URL + path, {"instType": "SWAP"}, limiter=limiter, timeout=30.0,
                              rate_limited=_rate_limited, sleep=sleep), what)

    rows = {r.get("instId"): r for r in public(INSTRUMENTS_PATH, "instruments SWAP")}
    marks = {r.get("instId"): _f(r.get("markPx")) for r in public(MARK_PRICE_PATH, "mark-price SWAP")}
    try:
        oi = {r.get("instId"): _f(r.get("oiUsd")) for r in public(OPEN_INTEREST_PATH, "open-interest SWAP")}
    except SourceFetchError:
        oi = {}  # then the user limit is posLmtAmt alone, a lower bound of the real one
    families = list(dict.fromkeys(rows[i]["instFamily"] for i in inst_ids if (rows.get(i) or {}).get("instFamily")))
    max_sz: dict[str, float] = {}
    for k in range(0, len(families), TIERS_PER_REQUEST):
        batch = families[k:k + TIERS_PER_REQUEST]
        try:
            data = _data(get_json(session, BASE_URL + POSITION_TIERS_PATH,
                                  {"instType": "SWAP", "tdMode": "cross", "instFamily": ",".join(batch)},
                                  limiter=tiers_limiter, rate_limited=_rate_limited, sleep=sleep), "position-tiers")
        except SourceFetchError:
            continue
        for t in data:
            fam, sz = t.get("instFamily"), _f(t.get("maxSz"))
            if fam and sz is not None:
                max_sz[fam] = max(max_sz.get(fam, 0.0), sz)
    out: dict[str, float | None] = {}
    for i in inst_ids:
        r = rows.get(i)
        out[i] = None if r is None else position_value(r, max_sz.get(r.get("instFamily")), marks.get(i), oi.get(i))
    return out
