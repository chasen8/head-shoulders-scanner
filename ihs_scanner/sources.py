"""Shared plumbing for the multi-exchange data sources (charter D22).

A *source* is one (exchange, market) pair: binance-spot, binance-perp, okx-spot, okx-perp, gate-perp.
Each source module lists its tradable USDT instruments and fetches closed klines in the common frame
format of `data_binance` (timestamp/open/high/low/close/volume/close_time, UTC, oldest first).
`merge_instruments` keeps exactly one source per base coin, by priority order.
No API key and no trading permission is used anywhere: public market-data endpoints only.
"""
from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping, Sequence

import pandas as pd

from .data_binance import COLUMNS, EXCLUDED_BASE_ASSETS, _rows_to_frame, is_leveraged_base

TIMEFRAME_MS = {"4h": 4 * 3600 * 1000, "1d": 24 * 3600 * 1000, "1w": 7 * 24 * 3600 * 1000}

# Exchange prefix TradingView uses in "EXCHANGE:SYMBOL".
TV_EXCHANGE = {"binance": "BINANCE", "okx": "OKX", "gate": "GATEIO", "twse": "TWSE", "tpex": "TPEX"}

# Dedup priority (first wins): a base coin is scanned from exactly one source.
SOURCE_PRIORITY = ("binance-spot", "binance-perp", "okx-spot", "okx-perp", "gate-perp")

_CONTRACT_MULTIPLIER = re.compile(r"^(?:1000000|100000|10000|1000|1M)(?=[A-Z])")


class SourceFetchError(RuntimeError):
    pass


@dataclass(frozen=True)
class Instrument:
    base: str          # e.g. "HMSTR", "1000PEPE"
    symbol: str        # unified display symbol: base + "USDT"; unique across merged sources
    native: str        # the exchange's own id (BTCUSDT / BTC-USDT-SWAP / BTC_USDT)
    exchange: str      # binance | okx | gate | twse | tpex (D29: Taiwan listed / OTC stocks)
    market: str        # spot | perp | stock
    name: str = ""     # display name (Taiwan stocks: short Chinese name, e.g. 台積電); empty for crypto

    @property
    def source(self) -> str:
        return f"{self.exchange}-{self.market}"

    @property
    def tv_symbol(self) -> str:
        suffix = ".P" if self.market == "perp" else ""
        return f"{TV_EXCHANGE.get(self.exchange, self.exchange.upper())}:{self.symbol}{suffix}"


def dedupe_key(base: str) -> str:
    """Normalize a base for cross-exchange dedup: 1000PEPE (perp) and PEPE (spot) are one coin."""
    b = base.upper()
    stripped = _CONTRACT_MULTIPLIER.sub("", b)
    return stripped if len(stripped) >= 2 else b


def merge_instruments(by_source: Mapping[str, Sequence[Instrument]],
                      priority: Sequence[str] = SOURCE_PRIORITY) -> list[Instrument]:
    """One instrument per base coin; earlier sources in `priority` win. Order: priority, then symbol."""
    seen: set[str] = set()
    out: list[Instrument] = []
    for key in priority:
        for inst in sorted(by_source.get(key, ()), key=lambda i: i.symbol):
            k = dedupe_key(inst.base)
            if k in seen:
                continue
            seen.add(k)
            out.append(inst)
    return out


MaxPositionFn = Callable[[Sequence[Instrument]], Mapping[str, "float | None"]]


@dataclass
class PositionFilterResult:
    merged: list[Instrument]
    excluded: list[dict[str, Any]]           # {symbol, base, source, native, max_position_usdt, replaced_by}
    checked: dict[str, int]                  # source -> instruments whose limit was looked up
    unknown: dict[str, int]                  # source -> looked up but no value (kept)
    errors: list[dict[str, Any]]             # {source, error}: the whole lookup failed (all kept)


def merge_with_position_limit(by_source: Mapping[str, Sequence[Instrument]],
                              max_position: Mapping[str, MaxPositionFn], min_value: float,
                              priority: Sequence[str] = SOURCE_PRIORITY,
                              errors: tuple[type[BaseException], ...] = (Exception,)) -> PositionFilterResult:
    """D45: merge_instruments + drop perp instruments whose max position value (USDT) < min_value.

    Sources run in D22 priority order. For a source with a lookup in `max_position`, only the instruments whose base
    is not already taken by an earlier source are looked up (saves requests); an excluded instrument does NOT claim
    its base, so a later source (e.g. Gate perp after OKX perp) can still supply that coin when its own limit is
    high enough (`replaced_by`). Unknown values (None, or a failed lookup) keep the instrument. min_value <= 0 or no
    lookups -> exactly merge_instruments."""
    seen: set[str] = set()
    out: list[Instrument] = []
    excluded: list[dict[str, Any]] = []
    checked: dict[str, int] = {}
    unknown: dict[str, int] = {}
    errs: list[dict[str, Any]] = []
    for key in priority:
        cands = []
        for inst in sorted(by_source.get(key, ()), key=lambda i: i.symbol):
            if dedupe_key(inst.base) not in seen:
                cands.append(inst)
        fn = max_position.get(key) if min_value > 0 else None
        values: Mapping[str, float | None] = {}
        if fn is not None and cands:
            checked[key] = len(cands)
            try:
                values = fn(cands)
            except errors as exc:  # lookup failed: keep everything, record why
                errs.append({"source": key, "error": f"{type(exc).__name__}: {exc}"[:240]})
                values = {}
        for inst in cands:
            k = dedupe_key(inst.base)
            if k in seen:  # two instruments of one coin in the same source (PEPE / 1000PEPE)
                continue
            if fn is not None:
                v = values.get(inst.native)
                if v is None:
                    unknown[key] = unknown.get(key, 0) + 1
                elif v < min_value:
                    excluded.append({"symbol": inst.symbol, "base": inst.base, "source": key, "native": inst.native,
                                     "max_position_usdt": round(float(v), 2)})
                    continue
            seen.add(k)
            out.append(inst)
    taken = {dedupe_key(i.base): i.source for i in out}
    for e in excluded:
        e["replaced_by"] = taken.get(dedupe_key(e["base"]))
    return PositionFilterResult(out, excluded, checked, unknown, errs)


def keep_base(base: str, usdt_bases: Iterable[str], excluded: Iterable[str] = EXCLUDED_BASE_ASSETS) -> bool:
    """False for stablecoin/fiat bases and leveraged tokens (same rules as the Binance spot list)."""
    return base.upper() not in {e.upper() for e in excluded} and not is_leveraged_base(base, usdt_bases)


class IntervalLimiter:
    """Thread-safe request pacing: at most one request per `min_interval` seconds (shared by workers)."""

    def __init__(self, min_interval: float, *, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep):
        self.min_interval = min_interval
        self._clock, self._sleep = clock, sleep
        self._lock = threading.Lock()
        self._next = 0.0
        self.requests = 0

    def acquire(self) -> None:
        with self._lock:
            now = self._clock()
            wait = self._next - now
            self._next = max(now, self._next) + self.min_interval
            self.requests += 1
        if wait > 0:
            self._sleep(wait)


def get_json(session, url: str, params: Mapping[str, Any] | None, *, limiter: IntervalLimiter | None = None,
             timeout: float = 20.0, retries: int = 3, backoff: float = 1.0,
             sleep: Callable[[float], None] = time.sleep,
             rate_limited: Callable[[Any], bool] | None = None):
    """GET + JSON with pacing and exponential backoff on HTTP 429/5xx, network errors, or when
    `rate_limited(json)` is true (exchange-specific code inside an HTTP 200)."""
    last = "no attempt"
    for attempt in range(retries + 1):
        if limiter is not None:
            limiter.acquire()
        try:
            resp = session.get(url, params=dict(params or {}), timeout=timeout)
            status = getattr(resp, "status_code", 200)
            if status == 429 or status >= 500:
                last = f"HTTP {status}"
            else:
                if status >= 400:
                    raise SourceFetchError(f"{url}: HTTP {status} {str(resp.text)[:120]!r}")
                data = resp.json()
                if rate_limited is not None and rate_limited(data):
                    last = f"rate limited: {str(data)[:120]}"
                else:
                    return data
        except SourceFetchError:
            raise
        except Exception as exc:  # network error, bad JSON
            last = repr(exc)
        if attempt < retries:
            sleep(backoff * (2 ** attempt))
    raise SourceFetchError(f"{url}: {last} (after {retries + 1} attempts)")


def ohlcv_frame(rows: Sequence[Sequence[Any]], timeframe: str, *, now_ms: int | None = None,
                limit: int | None = None) -> pd.DataFrame:
    """rows: [open_time_ms, open, high, low, close, volume] in any order -> common frame, closed bars only.

    close_time = open_time + interval - 1ms (the Binance convention); a bar is closed when
    close_time < now. Keeps the newest `limit` closed bars."""
    step = TIMEFRAME_MS[timeframe]
    full = [[int(r[0]), r[1], r[2], r[3], r[4], r[5], int(r[0]) + step - 1] for r in rows]
    df = _rows_to_frame(full)
    now = pd.Timestamp(now_ms if now_ms is not None else int(time.time() * 1000), unit="ms", tz="UTC")
    df = df[df["close_time"] < now]
    if limit is not None:
        df = df.tail(limit)
    return df.reset_index(drop=True)[COLUMNS]
