"""Binance PUBLIC market-data fetcher (charter D3). No API key, no trading permission.

Primary endpoint: https://data-api.binance.vision/api/v3/...
Fallback:         https://api.binance.com/api/v3/...
Only closed candles are kept (close_time < now).

Rate limits (Binance spot REST docs, checked 2026-10-18, and confirmed from response headers):
  - IP limit REQUEST_WEIGHT = 6000 per minute.
  - GET /api/v3/klines weight 2 for any limit (the old 1/2/5/10 by-limit table no longer applies;
    limit=100 and limit=1000 both raise X-MBX-USED-WEIGHT-1M by 2).
  - GET /api/v3/exchangeInfo weight 20; GET /api/v3/ticker/24hr without symbol weight 80.
`WeightLimiter` reads X-MBX-USED-WEIGHT-1M from every response and, when the next request would
push the minute past `threshold`, sleeps until the next minute. HTTP 429/418 are retried after
the Retry-After header (418 = IP ban: retried only if Retry-After is short).
"""
from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

import pandas as pd
import requests

PRIMARY_BASE = "https://data-api.binance.vision"
FALLBACK_BASE = "https://api.binance.com"
KLINES_PATH = "/api/v3/klines"
EXCHANGE_INFO_PATH = "/api/v3/exchangeInfo"
TICKER_24H_PATH = "/api/v3/ticker/24hr"
INTERVALS = {"4h": "4h", "1d": "1d", "1w": "1w"}
MAX_PER_REQUEST = 1000
DEFAULT_CACHE_DIR = Path(r"C:\trading-data\ihs-scanner-v2\data")
COLUMNS = ["timestamp", "open", "high", "low", "close", "volume", "close_time"]

IP_WEIGHT_LIMIT_1M = 6000
DEFAULT_WEIGHT_THRESHOLD = 4500  # self-throttle well below the 6000/min IP limit
EXCHANGE_INFO_WEIGHT = 20
TICKER_24H_ALL_WEIGHT = 80
MAX_RETRY_AFTER = 300.0  # seconds; a longer 418 ban is reported as an error, not waited out
WEIGHT_HEADER = "X-MBX-USED-WEIGHT-1M"

# Base assets that are stablecoins / fiat: a USD-pegged pair has no chart pattern worth scanning.
# Gold tokens (PAXG, XAUT) are NOT excluded: they move like a commodity.
EXCLUDED_BASE_ASSETS = frozenset({
    "USDC", "FDUSD", "TUSD", "USDP", "DAI", "BUSD", "USDS", "USDE", "USDEB", "XUSD", "USD1",
    "PYUSD", "RLUSD", "BFUSD", "RWUSD", "UST", "PAX", "SUSD", "USDD", "GUSD", "LUSD", "FRAX",
    "EUR", "AEUR", "EURI", "EURC", "GBP", "AUD", "TRY", "BRL", "JPY", "MXN", "ARS", "PLN", "RON",
    "UAH", "ZAR", "NGN", "RUB", "COP", "CZK", "IDRT", "BIDR", "BVND",
})
# Leveraged tokens (e.g. BTCUP, ETHDOWN). A base only counts as leveraged when the part before
# the suffix is itself a listed USDT base of 2+ characters, so JUP and SYRUP are kept.
LEVERAGED_SUFFIXES = ("UP", "DOWN", "BULL", "BEAR")


class BinanceFetchError(RuntimeError):
    pass


def kline_weight(limit: int) -> int:
    """Request weight of one GET /api/v3/klines call (2 for any limit 1..1000, per current docs)."""
    if limit < 1:
        raise ValueError("limit must be >= 1")
    return 2


def _header(headers: Any, name: str) -> str | None:
    if not headers:
        return None
    try:
        value = headers.get(name)
    except AttributeError:
        return None
    if value is not None:
        return value
    low = name.lower()
    for k, v in dict(headers).items():
        if str(k).lower() == low:
            return v
    return None


class WeightLimiter:
    """Shared, thread-safe request-weight budget for one IP.

    acquire(weight) before each request: if this minute's used weight (the larger of our own count
    and the last X-MBX-USED-WEIGHT-1M seen) plus `weight` would exceed `threshold`, sleep until the
    next UTC minute (+`margin` seconds for clock skew). The lock is held while sleeping, so every
    worker thread waits. observe(headers) after each response. pause(seconds) after a 429/418.
    """

    def __init__(self, threshold: int = DEFAULT_WEIGHT_THRESHOLD, *, margin: float = 1.0,
                 clock: Callable[[], float] = time.time, sleep: Callable[[float], None] = time.sleep):
        if threshold < 1:
            raise ValueError("threshold must be >= 1")
        self.threshold = threshold
        self.margin = margin
        self._clock = clock
        self._sleep = sleep
        self._lock = threading.Lock()
        self._window: int | None = None
        self.used = 0
        self.max_seen = 0  # largest X-MBX-USED-WEIGHT-1M observed
        self.throttle_waits = 0
        self.slept = 0.0
        self._blocked_until = 0.0

    def _roll(self, now: float) -> None:
        minute = int(now // 60)
        if self._window != minute:
            self._window = minute
            self.used = 0

    def _do_sleep(self, seconds: float) -> None:
        if seconds > 0:
            self._sleep(seconds)
            self.slept += seconds

    def acquire(self, weight: int) -> None:
        with self._lock:
            now = self._clock()
            if now < self._blocked_until:
                self._do_sleep(self._blocked_until - now)
                now = self._clock()
            self._roll(now)
            if self.used + weight > self.threshold:
                wait = (int(now // 60) + 1) * 60 - now + self.margin
                self.throttle_waits += 1
                self._do_sleep(wait)
                self._roll(self._clock())
            self.used += weight

    def observe(self, headers: Any) -> int | None:
        raw = _header(headers, WEIGHT_HEADER)
        try:
            value = int(raw) if raw is not None else None
        except (TypeError, ValueError):
            value = None
        if value is None:
            return None
        with self._lock:
            self._roll(self._clock())
            self.used = max(self.used, value)
            self.max_seen = max(self.max_seen, value)
        return value

    def pause(self, seconds: float) -> None:
        with self._lock:
            self._blocked_until = max(self._blocked_until, self._clock() + seconds)


def _retry_after(headers: Any, default: float = 60.0) -> float:
    raw = _header(headers, "Retry-After")
    try:
        return max(0.0, float(raw)) if raw is not None else default
    except (TypeError, ValueError):
        return default


def _get_json(session, path: str, params: Mapping[str, Any] | None, bases: Sequence[str], timeout: float,
              *, limiter: WeightLimiter | None = None, weight: int = 1, retries: int = 2):
    errors = []
    for base in bases:
        url = base + path
        for attempt in range(retries + 1):
            try:
                if limiter is not None:
                    limiter.acquire(weight)
                resp = session.get(url, params=dict(params or {}), timeout=timeout)
                headers = getattr(resp, "headers", None)
                if limiter is not None:
                    limiter.observe(headers)
                status = getattr(resp, "status_code", 200)
                if status in (429, 418):
                    wait = _retry_after(headers)
                    if wait > MAX_RETRY_AFTER or attempt == retries:
                        raise BinanceFetchError(f"HTTP {status} rate limited (Retry-After {wait:.0f}s)")
                    if limiter is not None:
                        limiter.pause(wait)
                    else:
                        time.sleep(wait)
                    continue
                resp.raise_for_status()
                return resp.json()
            except Exception as exc:  # network error, HTTP 451, bad JSON, rate limit...
                errors.append(f"{url}: {exc!r}")
                break
    raise BinanceFetchError("all endpoints failed: " + " | ".join(errors))


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
                 bases: Sequence[str] = (PRIMARY_BASE, FALLBACK_BASE),
                 limiter: WeightLimiter | None = None) -> pd.DataFrame:
    """Return the most recent `limit` CLOSED klines, oldest first.

    Paginates backwards with endTime when limit > 1000. If cache_dir is given the
    result is also written to <cache_dir>/<SYMBOL>_<tf>.csv. Pass a shared `limiter`
    when fetching many series so the IP weight budget is respected.
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
        data = _get_json(session, KLINES_PATH, params, bases, timeout, limiter=limiter,
                         weight=kline_weight(batch))
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


def is_leveraged_base(base: str, usdt_bases: Iterable[str]) -> bool:
    known = set(usdt_bases)
    for suffix in LEVERAGED_SUFFIXES:
        stem = base[: -len(suffix)]
        if base.endswith(suffix) and len(stem) >= 2 and stem in known:
            return True
    return False


def filter_usdt_symbols(exchange_info: Mapping[str, Any],
                        excluded_bases: Iterable[str] = EXCLUDED_BASE_ASSETS) -> list[str]:
    """TRADING + quoteAsset USDT + isSpotTradingAllowed, minus stablecoin/fiat bases and
    leveraged tokens. Returns symbols sorted."""
    excluded = {b.upper() for b in excluded_bases}
    usdt = [s for s in exchange_info.get("symbols", [])
            if s.get("status") == "TRADING" and s.get("quoteAsset") == "USDT"
            and s.get("isSpotTradingAllowed", False)]
    bases = {s.get("baseAsset", "") for s in usdt}
    keep = [s["symbol"] for s in usdt
            if s.get("baseAsset", "").upper() not in excluded
            and not is_leveraged_base(s.get("baseAsset", ""), bases)]
    return sorted(set(keep))


def list_usdt_symbols(session=None, *, bases: Sequence[str] = (PRIMARY_BASE, FALLBACK_BASE),
                      timeout: float = 30.0, limiter: WeightLimiter | None = None,
                      excluded_bases: Iterable[str] = EXCLUDED_BASE_ASSETS, verbose: bool = True) -> list[str]:
    """Every spot USDT pair currently trading on Binance (weight 20, see filter_usdt_symbols)."""
    session = session or requests.Session()
    params = {"symbolStatus": "TRADING", "showPermissionSets": "false"}  # 2.5 MB instead of ~18 MB
    info = _get_json(session, EXCHANGE_INFO_PATH, params, bases, timeout, limiter=limiter,
                     weight=EXCHANGE_INFO_WEIGHT)
    symbols = filter_usdt_symbols(info, excluded_bases)
    if verbose:
        print(f"[symbols] {len(symbols)} Binance spot USDT pairs trading "
              f"(stablecoin/fiat bases and leveraged tokens excluded)")
    return symbols


def fetch_quote_volumes(session=None, *, bases: Sequence[str] = (PRIMARY_BASE, FALLBACK_BASE),
                        timeout: float = 30.0, limiter: WeightLimiter | None = None) -> dict[str, float]:
    """{symbol: 24h quoteVolume} for every symbol (one GET /api/v3/ticker/24hr, weight 80)."""
    session = session or requests.Session()
    data = _get_json(session, TICKER_24H_PATH, {"type": "MINI"}, bases, timeout, limiter=limiter,
                     weight=TICKER_24H_ALL_WEIGHT)
    out: dict[str, float] = {}
    for row in data:
        try:
            out[row["symbol"]] = float(row["quoteVolume"])
        except (KeyError, TypeError, ValueError):
            continue
    return out
