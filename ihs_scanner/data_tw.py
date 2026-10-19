"""Taiwan stocks (TWSE listed + TPEx OTC), charter D29. Public endpoints only: no key, no account.

Stock list and Chinese short names:
  TWSE  GET https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL     [{Code, Name, ...}]
  TPEx  GET https://www.tpex.org.tw/openapi/v1/tpex_mainboard_quotes          [{SecuritiesCompanyCode, CompanyName, ...}]
  Kept: common stocks = 4-digit codes not starting with 0, minus depositary receipts (name "-DR").
  Excluded by that rule: ETF (00xx / 00xxxx[A-Z]), ETN (02xxxx), warrants (6-digit), preferred shares
  (2881A), REIT / beneficiary certificates (01xxxT). `include_etf=True` adds 00xx ETFs.

Klines: Yahoo Finance chart API  GET https://query1.finance.yahoo.com/v8/finance/chart/<code>.TW|.TWO
  1d  interval=1d  range=10y    -> 1d bars; 1w bars are resampled from these (Monday-anchored weeks)
  4h  interval=60m range=730d   -> resampled into TradingView's Taiwan 4h session split:
                                   09:00-13:00 and 13:00-13:30 (Asia/Taipei), two bars per trading day
  So one symbol costs two requests. Yahoo answers 429 to GitHub's US runners unless the request carries
  the finance.yahoo.com cookie and its "crumb" (probed 2026-10-19), so `YahooClient` fetches those first.
  Prices are Yahoo's raw quote OHLC (split-adjusted, NOT dividend-adjusted), the same as TradingView's default.

Yahoo's 60m data has two quirks that the 4h resampling repairs from the daily bar of the same date:
  * the 09:00 hour usually reports volume 0, and the 13:25-13:30 closing auction is missing, so
    residual = daily volume - sum(hourly volume) is added to the 09:00 bar (when its first hour shows 0)
    or to the 13:00 bar (otherwise);
  * the last hourly close is the price before the closing auction, so the 13:00 bar's close is replaced
    by the daily close (and its high / low widened to include it).

Closed bars only: a bar is kept when its session end (13:00 / 13:30 Taipei; weekly: Friday 13:30)
plus CLOSE_BUFFER has passed. Times are stored in UTC: bar timestamp = session start (09:00 Taipei =
01:00 UTC; the 13:00 bar = 05:00 UTC; weekly = Monday 09:00 Taipei).
"""
from __future__ import annotations

import re
import threading
import time
from typing import Any, Callable, Iterable, Mapping, Sequence

import pandas as pd
import requests

from .data_binance import COLUMNS
from .sources import IntervalLimiter, Instrument, SourceFetchError, get_json

TZ = "Asia/Taipei"
TWSE_LIST_URL = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
TPEX_LIST_URL = "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_quotes"
YAHOO_HOME = "https://finance.yahoo.com/"
YAHOO_COOKIE_FALLBACK = "https://fc.yahoo.com/"
YAHOO_CRUMB_URL = "https://query1.finance.yahoo.com/v1/test/getcrumb"
YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/126.0 Safari/537.36")
YAHOO_SUFFIX = {"twse": ".TW", "tpex": ".TWO"}
DAILY_RANGE = "10y"
HOURLY_RANGE = "730d"  # Yahoo's maximum for 60m bars
# Stocks listed within the last ~2.4 years get HTTP 422 for range=730d ("requested range must be within the
# last 730 days", Yahoo starts at the first trade date); "2y" / "1y" work for them (2026-10-19: 56 of 1973 stocks).
HOURLY_FALLBACK_RANGES = ("2y", "1y")
REQUESTS_PER_SECOND = 5.0
CLOSE_BUFFER = pd.Timedelta(minutes=30)  # Yahoo publishes the final close a few minutes after 13:30
SESSION_START = pd.Timedelta(hours=9)
PM_START = pd.Timedelta(hours=13)
SESSION_END = pd.Timedelta(hours=13, minutes=30)
WEEKLY_LIMIT_CAP = 500

COMMON_STOCK = re.compile(r"^[1-9]\d{3}$")
ETF_CODE = re.compile(r"^00\d{2,4}[A-Z]?$")


def new_limiter(requests_per_second: float = REQUESTS_PER_SECOND) -> IntervalLimiter:
    return IntervalLimiter(1.0 / requests_per_second)


# ---------------------------------------------------------------- stock list

def is_common_stock(code: str, name: str) -> bool:
    name = (name or "").strip().upper()
    return bool(COMMON_STOCK.match(code)) and not name.endswith("-DR")


def filter_listing(rows: Iterable[Mapping[str, Any]], exchange: str, *, include_etf: bool = False
                   ) -> list[Instrument]:
    """TWSE STOCK_DAY_ALL / TPEx mainboard rows -> instruments (common stocks, optionally ETFs)."""
    code_key, name_key = ("Code", "Name") if exchange == "twse" else ("SecuritiesCompanyCode", "CompanyName")
    out: dict[str, Instrument] = {}
    for r in rows:
        code = str(r.get(code_key) or "").strip().upper()
        name = " ".join(str(r.get(name_key) or "").split())
        if not code or not name:
            continue
        if not (is_common_stock(code, name) or (include_etf and ETF_CODE.match(code))):
            continue
        out[code] = Instrument(base=code, symbol=code, native=code + YAHOO_SUFFIX[exchange], exchange=exchange,
                               market="stock", name=name)
    return [out[c] for c in sorted(out)]


def list_instruments(session, exchange: str, *, include_etf: bool = False, verbose: bool = True,
                     timeout: float = 90.0) -> list[Instrument]:
    url = TWSE_LIST_URL if exchange == "twse" else TPEX_LIST_URL
    payload = get_json(session, url, None, timeout=timeout, retries=2, backoff=3.0)
    if not isinstance(payload, list) or not payload:
        raise SourceFetchError(f"{exchange} stock list: unexpected payload {str(payload)[:160]}")
    out = filter_listing(payload, exchange, include_etf=include_etf)
    if verbose:
        kind = "common stocks + ETFs" if include_etf else "common stocks"
        print(f"[symbols] {exchange}: {len(out)} {kind} (of {len(payload)} listed securities)")
    return out


# ---------------------------------------------------------------- Yahoo client

class YahooClient:
    """Cookie + crumb holder shared by worker threads; every request goes through one IntervalLimiter.

    `session_factory` makes one requests-like session per thread (cookies are copied into it)."""

    def __init__(self, *, limiter: IntervalLimiter | None = None, session_factory: Callable[[], Any] = requests.Session,
                 sleep: Callable[[float], None] = time.sleep, timeout: float = 30.0, retries: int = 4,
                 backoff: float = 2.0, clock: Callable[[], float] = time.monotonic):
        self.limiter = limiter or new_limiter()
        self._factory, self._sleep, self._clock = session_factory, sleep, clock
        self.timeout, self.retries, self.backoff = timeout, retries, backoff
        self._lock = threading.Lock()
        self._local = threading.local()
        self.cookies: dict[str, str] = {}
        self.crumb: str | None = None
        self._crumb_at = -1e9
        self.generation = 0
        self.requests = 0
        self.rate_limited = 0

    def _session(self):
        s = getattr(self._local, "s", None)
        if s is None or getattr(self._local, "gen", -1) != self.generation:
            s = self._factory()
            headers = getattr(s, "headers", None)
            if headers is not None:
                headers.update({"User-Agent": USER_AGENT, "Accept": "application/json,text/plain,*/*"})
            jar = getattr(s, "cookies", None)
            if jar is not None:
                for k, v in self.cookies.items():
                    jar.set(k, v)
            self._local.s, self._local.gen = s, self.generation
        return s

    def refresh_crumb(self, *, min_age: float = 30.0) -> bool:
        """Get cookies from finance.yahoo.com (fallback fc.yahoo.com), then the crumb. Rate-limited to one
        refresh per `min_age` seconds across threads. True when a crumb is available."""
        with self._lock:
            if self._clock() - self._crumb_at < min_age:
                return self.crumb is not None
            self._crumb_at = self._clock()
            s = self._factory()
            headers = getattr(s, "headers", None)
            if headers is not None:
                headers.update({"User-Agent": USER_AGENT})
            crumb = None
            for home in (YAHOO_HOME, YAHOO_COOKIE_FALLBACK):
                try:
                    self.limiter.acquire()
                    s.get(home, timeout=self.timeout)
                    self.limiter.acquire()
                    resp = s.get(YAHOO_CRUMB_URL, timeout=self.timeout)
                    text = str(getattr(resp, "text", "") or "").strip()
                    if getattr(resp, "status_code", 200) == 200 and text and "<" not in text and len(text) < 64:
                        crumb = text
                        break
                except Exception:  # network error: try the fallback page
                    continue
            jar = getattr(s, "cookies", None)
            self.cookies = dict(jar.items()) if jar is not None and hasattr(jar, "items") else {}
            self.crumb = crumb
            self.generation += 1
            return crumb is not None

    def chart(self, yahoo_symbol: str, interval: str, range_: str) -> dict[str, Any]:
        """GET chart JSON -> chart.result[0]. 429 / 401 / 5xx: refresh the crumb and back off (bounded)."""
        url = YAHOO_CHART_URL.format(symbol=yahoo_symbol)
        last = "no attempt"
        for attempt in range(self.retries + 1):
            params = {"interval": interval, "range": range_, "includePrePost": "false", "events": "div,split"}
            if self.crumb:
                params["crumb"] = self.crumb
            self.limiter.acquire()
            self.requests += 1
            try:
                resp = self._session().get(url, params=params, timeout=self.timeout)
                status = getattr(resp, "status_code", 200)
                if status in (401, 403, 429) or status >= 500:
                    last = f"HTTP {status}"
                    if status == 429:
                        self.rate_limited += 1
                    if status < 500:
                        self.refresh_crumb()
                elif status >= 400:
                    raise SourceFetchError(f"{yahoo_symbol} {interval}: HTTP {status} {str(resp.text)[:100]!r}")
                else:
                    return chart_result(resp.json(), yahoo_symbol)
            except SourceFetchError:
                raise
            except Exception as exc:  # network error, bad JSON
                last = repr(exc)
            if attempt < self.retries:
                self._sleep(self.backoff * (2 ** attempt))
        raise SourceFetchError(f"{yahoo_symbol} {interval}: {last} (after {self.retries + 1} attempts)")


def chart_result(payload: Mapping[str, Any], label: str = "") -> dict[str, Any]:
    chart = (payload or {}).get("chart") or {}
    if chart.get("error"):
        err = chart["error"]
        raise SourceFetchError(f"{label}: Yahoo error {err.get('code') if isinstance(err, dict) else err}: "
                               f"{(err.get('description') if isinstance(err, dict) else '')}"[:200])
    result = chart.get("result") or []
    if not result:
        raise SourceFetchError(f"{label}: Yahoo returned no result")
    return result[0]


# ---------------------------------------------------------------- parsing / resampling (pure)

def parse_chart(result: Mapping[str, Any]) -> pd.DataFrame:
    """chart.result[0] -> [timestamp(UTC), open, high, low, close, volume]; rows with a missing price dropped."""
    ts = result.get("timestamp") or []
    quote = ((result.get("indicators") or {}).get("quote") or [{}])[0] or {}
    if not ts:
        return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])
    n = len(ts)

    def col(name):
        vals = list(quote.get(name) or [])
        vals += [None] * (n - len(vals))
        return pd.to_numeric(pd.Series(vals[:n], dtype="object"), errors="coerce")

    df = pd.DataFrame({
        "timestamp": pd.to_datetime(pd.Series(ts, dtype="int64"), unit="s", utc=True),
        "open": col("open"), "high": col("high"), "low": col("low"), "close": col("close"),
        "volume": col("volume").fillna(0.0),
    })
    df = df.dropna(subset=["open", "high", "low", "close"])
    df = df[(df["high"] > 0) & (df["low"] > 0)]
    return df.sort_values("timestamp").reset_index(drop=True)


def _local_day(ts: pd.Series) -> pd.Series:
    """Midnight (Asia/Taipei, tz-aware) of each UTC timestamp's Taipei calendar day."""
    return ts.dt.tz_convert(TZ).dt.normalize()


def _now(now: pd.Timestamp | None) -> pd.Timestamp:
    t = pd.Timestamp.now(tz="UTC") if now is None else pd.Timestamp(now)
    return t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")


def _agg(df: pd.DataFrame, key: str) -> pd.DataFrame:
    return df.groupby(key, sort=True).agg(open=("open", "first"), high=("high", "max"), low=("low", "min"),
                                          close=("close", "last"), volume=("volume", "sum")).reset_index()


def _finish(out: pd.DataFrame, now: pd.Timestamp | None, buffer: pd.Timedelta, limit: int | None) -> pd.DataFrame:
    out = out[out["close_time"] + buffer <= _now(now)]
    if limit is not None:
        out = out.tail(limit)
    out = out.copy()
    for c in ("open", "high", "low", "close", "volume"):
        out[c] = out[c].astype(float)
    return out.reset_index(drop=True)[COLUMNS]


def daily_bars(raw: pd.DataFrame, *, now: pd.Timestamp | None = None, buffer: pd.Timedelta = CLOSE_BUFFER,
               limit: int | None = None) -> pd.DataFrame:
    """One bar per Taipei trading day: timestamp = 09:00 Taipei (UTC), close_time = 13:30 Taipei (UTC).
    Kept only when close_time + buffer <= now (today's bar is dropped until the close is final)."""
    if raw.empty:
        return pd.DataFrame(columns=COLUMNS)
    df = raw.copy()
    df["day"] = _local_day(df["timestamp"])
    out = _agg(df, "day")
    out["timestamp"] = (out["day"] + SESSION_START).dt.tz_convert("UTC")
    out["close_time"] = (out["day"] + SESSION_END).dt.tz_convert("UTC")
    return _finish(out, now, buffer, limit)


def weekly_bars(daily: pd.DataFrame, *, now: pd.Timestamp | None = None, buffer: pd.Timedelta = CLOSE_BUFFER,
                limit: int | None = None) -> pd.DataFrame:
    """Monday-anchored weeks from CLOSED daily bars: open = first day's open, close = last day's close.
    timestamp = Monday 09:00 Taipei, close_time = Friday 13:30 Taipei; the current week is kept only after
    Friday's close (+buffer), even when Friday is a holiday."""
    if daily.empty:
        return pd.DataFrame(columns=COLUMNS)
    df = daily.copy()
    day = _local_day(df["timestamp"])
    df["week"] = day - pd.to_timedelta(day.dt.weekday, unit="D")
    out = _agg(df, "week")
    out["timestamp"] = (out["week"] + SESSION_START).dt.tz_convert("UTC")
    out["close_time"] = (out["week"] + pd.Timedelta(days=4) + SESSION_END).dt.tz_convert("UTC")
    return _finish(out, now, buffer, limit)


def four_hour_bars(hourly: pd.DataFrame, daily_raw: pd.DataFrame | None = None, *,
                   now: pd.Timestamp | None = None, buffer: pd.Timedelta = CLOSE_BUFFER,
                   limit: int | None = None) -> pd.DataFrame:
    """60m bars -> two bars per trading day like TradingView's Taiwan 4h: 09:00-13:00 and 13:00-13:30 (Taipei).

    `daily_raw` (parse_chart of the 1d request) repairs Yahoo's 60m quirks (module docstring): the 13:00
    bar closes at the daily close, and the volume the hourly bars miss goes to the 09:00 bar when its first
    hour reports 0, else to the 13:00 bar. close_time: 13:00 / 13:30 Taipei; kept when close_time+buffer <= now."""
    if hourly.empty:
        return pd.DataFrame(columns=COLUMNS)
    df = hourly.copy()
    local = df["timestamp"].dt.tz_convert(TZ)
    df["day"] = local.dt.normalize()
    offset = local - df["day"]
    df["pm"] = offset >= PM_START
    df["first_hour_zero"] = (offset < SESSION_START + pd.Timedelta(hours=1)) & (df["volume"] <= 0)
    df["seg"] = df["day"] + df["pm"].map({False: SESSION_START, True: PM_START})
    out = _agg(df, "seg")
    zero_first = df.groupby("seg")["first_hour_zero"].any()
    out["first_hour_zero"] = out["seg"].map(zero_first).fillna(False).astype(bool)
    out["day"] = out["seg"].dt.normalize()
    out["pm"] = (out["seg"] - out["day"]) >= PM_START

    if daily_raw is not None and not daily_raw.empty:
        d = daily_raw.copy()
        d["day"] = _local_day(d["timestamp"])
        d = _agg(d, "day").set_index("day")
        dclose = out["day"].map(d["close"])
        dvol = out["day"].map(d["volume"])
        fix = out["pm"] & dclose.notna()
        out.loc[fix, "close"] = dclose[fix]
        out.loc[fix, "high"] = out.loc[fix, ["high", "close"]].max(axis=1)
        out.loc[fix, "low"] = out.loc[fix, ["low", "close"]].min(axis=1)
        by_day = out.groupby("day")
        residual = (dvol - by_day["volume"].transform("sum")).clip(lower=0).fillna(0.0)
        has_pm = by_day["pm"].transform("any").astype(bool)
        has_am = (~out["pm"]).groupby(out["day"]).transform("any").astype(bool)
        am_zero = by_day["first_hour_zero"].transform("any").astype(bool)
        target = (~out["pm"] & (am_zero | ~has_pm)) | (out["pm"] & ~(has_am & am_zero))
        out.loc[target, "volume"] = out.loc[target, "volume"] + residual[target]

    out["timestamp"] = out["seg"].dt.tz_convert("UTC")
    out["close_time"] = (out["day"] + out["pm"].map({False: PM_START, True: SESSION_END})).dt.tz_convert("UTC")
    return _finish(out, now, buffer, limit)


# ---------------------------------------------------------------- per-symbol fetch

def fetch_series(inst: Instrument, timeframes: Sequence[str], limit: int, client: YahooClient, *,
                 now: pd.Timestamp | None = None) -> tuple[dict[str, pd.DataFrame], dict[str, str]]:
    """One symbol -> ({tf: closed frame}, {tf: error}). 1d and 1w share the daily request; 4h uses the
    60m request (and the daily one, when fetched, to repair the close / volume)."""
    frames: dict[str, pd.DataFrame] = {}
    errors: dict[str, str] = {}
    daily_raw = None
    if "1d" in timeframes or "1w" in timeframes:
        try:
            daily_raw = parse_chart(client.chart(inst.native, "1d", DAILY_RANGE))
        except (SourceFetchError, ValueError, KeyError, TypeError) as exc:
            for tf in ("1d", "1w"):
                if tf in timeframes:
                    errors[tf] = f"{type(exc).__name__}: {exc}"
        else:
            daily = daily_bars(daily_raw, now=now)
            if "1d" in timeframes:
                frames["1d"] = daily.tail(limit).reset_index(drop=True)
            if "1w" in timeframes:
                frames["1w"] = weekly_bars(daily, now=now, limit=min(limit, WEEKLY_LIMIT_CAP))
    if "4h" in timeframes:
        try:
            hourly = None
            for i, rng in enumerate((HOURLY_RANGE,) + HOURLY_FALLBACK_RANGES):
                try:
                    hourly = parse_chart(client.chart(inst.native, "60m", rng))
                    break
                except SourceFetchError as exc:
                    if "HTTP 422" not in str(exc) or i == len(HOURLY_FALLBACK_RANGES):
                        raise
            frames["4h"] = four_hour_bars(hourly, daily_raw, now=now, limit=limit)
        except (SourceFetchError, ValueError, KeyError, TypeError) as exc:
            errors["4h"] = f"{type(exc).__name__}: {exc}"
    return frames, errors
