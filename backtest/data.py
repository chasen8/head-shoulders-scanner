"""Long-history kline fetch + cache for the backtest (D40). Public endpoints only: no key, no trading permission.

Universe = the live scanner's current lists (survivorship bias: delisted coins / stocks are not included):
  crypto  build_site DEFAULT_SOURCES (binance-spot, okx-spot, okx-perp, gate-perp), one source per base coin
          (sources.merge_instruments, same as the live scan)
  tw      TWSE + TPEx common stocks (data_tw.list_instruments)

History per source (as long as each endpoint allows, capped by LIMITS):
  binance-spot  data_binance.fetch_klines (pages backwards with endTime)
  okx-*         /market/candles (<= ~1440 bars) then /market/history-candles (100 per call) with `after`
  gate-perp     /futures/usdt/candlesticks with from/to windows of <= 1999 bars
  tw            Yahoo 1d range=10y (range=max returns MONTHLY bars; 1w resampled from 1d), 60m range=730d (Yahoo's maximum) -> TW 4h
Cache: <root>/<market>/<source>/<symbol>_<tf>.csv (UTF-8). A cached file is reused, never refetched.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Sequence

import pandas as pd
import requests

from ihs_scanner import data_binance, data_gate, data_okx, data_tw
from ihs_scanner.data_binance import COLUMNS
from ihs_scanner.sources import (TIMEFRAME_MS, Instrument, IntervalLimiter, SourceFetchError, get_json,
                                 merge_instruments, ohlcv_frame)

ROOT = Path(r"C:\trading-data\ihs-scanner-v2\backtest")
DATA_DIR = ROOT / "data"
RESULTS_DIR = ROOT / "results"
CRYPTO_SOURCES = ("binance-spot", "okx-spot", "okx-perp", "gate-perp")  # = build_site.DEFAULT_SOURCES
LIMITS = {"4h": 6000, "1d": 3000, "1w": 1000}  # bars per crypto series (newest first, closed only)
OKX_HISTORY_PATH = "/api/v5/market/history-candles"
OKX_HISTORY_MAX = 100
GATE_WINDOW = 1999
GATE_EARLIEST_S = 1514764800  # 2018-01-01 UTC: older than any Gate USDT perpetual


def cache_file(market: str, source: str, symbol: str, timeframe: str, root: Path = DATA_DIR) -> Path:
    return Path(root) / market / source / f"{symbol}_{timeframe}.csv"


def save_frame(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    df[COLUMNS].to_csv(tmp, index=False, encoding="utf-8")
    tmp.replace(path)


def load_frame(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, encoding="utf-8")
    for col in ("timestamp", "close_time"):
        df[col] = pd.to_datetime(df[col], utc=True, format="ISO8601")
    for col in ("open", "high", "low", "close", "volume"):
        df[col] = df[col].astype(float)
    return df


# ------------------------------------------------------------------ crypto fetchers

def fetch_okx_long(inst_id: str, timeframe: str, limit: int, *, session, limiter: IntervalLimiter) -> pd.DataFrame:
    """Newest `limit` closed bars: /market/candles first (300 per call), then /market/history-candles."""
    rows: list = []
    after = None
    for path, per in ((data_okx.CANDLES_PATH, data_okx.MAX_PER_REQUEST), (OKX_HISTORY_PATH, OKX_HISTORY_MAX)):
        while len(rows) < limit + 1:
            params: dict[str, Any] = {"instId": inst_id, "bar": data_okx.BARS[timeframe], "limit": per}
            if after is not None:
                params["after"] = after
            payload = get_json(session, data_okx.BASE_URL + path, params, limiter=limiter, timeout=20.0,
                               rate_limited=data_okx._rate_limited)
            data = data_okx._data(payload, f"{path} {inst_id}")
            if not data:
                break
            rows.extend(r[:6] for r in data if str(r[8] if len(r) > 8 else "1") == "1")
            after = data[-1][0]
            if len(data) < per:
                break  # candles exhausted -> continue with history-candles from `after` (or done)
    return ohlcv_frame(rows, timeframe, limit=limit)


def fetch_gate_long(contract: str, timeframe: str, limit: int, *, session, limiter: IntervalLimiter) -> pd.DataFrame:
    """Newest `limit` closed bars with from/to windows of GATE_WINDOW bars, walking back until empty."""
    step_s = TIMEFRAME_MS[timeframe] // 1000
    to = int(time.time()) // step_s * step_s + step_s
    rows: dict[int, list] = {}
    floor = GATE_EARLIEST_S
    while len(rows) < limit + 1 and to > floor:
        frm = max(floor, to - GATE_WINDOW * step_s)  # Gate rejects a negative / pre-history `from`
        params = {"contract": contract, "interval": data_gate.INTERVALS[timeframe], "from": frm, "to": to}
        data = get_json(session, data_gate.BASE_URL + data_gate.CANDLES_PATH, params, limiter=limiter, timeout=20.0)
        if not isinstance(data, list):
            raise SourceFetchError(f"Gate candlesticks {contract}: {str(data)[:160]}")
        new = 0
        for r in data:
            t = int(r["t"])
            if t not in rows:
                rows[t] = [t * 1000, r["o"], r["h"], r["l"], r["c"], r["v"]]
                new += 1
        if new == 0:
            break
        to = frm - step_s
    return ohlcv_frame(list(rows.values()), timeframe, limit=limit)


def list_crypto(session=None) -> tuple[list[Instrument], dict[str, str]]:
    """The live scanner's crypto universe (merge_instruments over CRYPTO_SOURCES). A source that cannot be
    listed (OKX is unreachable from the local network, 2026-10-22) is skipped: its coins then come from the next
    source in priority order that lists them (gate-perp). Returns (instruments, {source: error})."""
    session = session or requests.Session()
    okx_lim = data_okx.new_limiter()
    listers = {
        "binance-spot": lambda: [Instrument(base=s[:-4], symbol=s, native=s, exchange="binance", market="spot")
                                 for s in data_binance.list_usdt_symbols(session)],
        "okx-spot": lambda: data_okx.list_instruments(session, "spot", limiter=okx_lim),
        "okx-perp": lambda: data_okx.list_instruments(session, "perp", limiter=okx_lim),
        "gate-perp": lambda: data_gate.list_instruments(session),
    }
    by_source, errors = {}, {}
    for key, fn in listers.items():
        try:
            by_source[key] = fn()
        except Exception as exc:  # network / geo block
            errors[key] = f"{type(exc).__name__}: {str(exc)[:200]}"
    return merge_instruments(by_source), errors


class CryptoFetcher:
    """One fetch function per source with its own rate limiter (thread-safe)."""

    def __init__(self, limits: dict[str, int] | None = None):
        self.limits = dict(LIMITS, **(limits or {}))
        self.binance_limiter = data_binance.WeightLimiter()
        self.okx_limiter = IntervalLimiter(1.0 / 9.0)  # history-candles: 20 req / 2 s
        self.gate_limiter = IntervalLimiter(1.0 / 9.0)

    def fetch(self, inst: Instrument, timeframe: str, session) -> pd.DataFrame:
        limit = self.limits[timeframe]
        if inst.source == "binance-spot":
            return data_binance.fetch_klines(inst.native, timeframe, limit, session=session,
                                             limiter=self.binance_limiter)
        if inst.exchange == "okx":
            return fetch_okx_long(inst.native, timeframe, limit, session=session, limiter=self.okx_limiter)
        if inst.source == "gate-perp":
            return fetch_gate_long(inst.native, timeframe, limit, session=session, limiter=self.gate_limiter)
        raise ValueError(f"unsupported source {inst.source}")


# ------------------------------------------------------------------ Taiwan

def list_tw(session=None) -> list[Instrument]:
    session = session or requests.Session()
    out: list[Instrument] = []
    for ex in ("twse", "tpex"):
        out.extend(data_tw.list_instruments(session, ex))
    return out


def fetch_tw_long(inst: Instrument, client: data_tw.YahooClient) -> tuple[dict[str, pd.DataFrame], dict[str, str]]:
    """{tf: frame} with 1d = range 10y, 1w resampled from it, 4h from 60m range 730d (fallbacks 2y / 1y)."""
    frames: dict[str, pd.DataFrame] = {}
    errors: dict[str, str] = {}
    daily_raw = None
    try:
        daily_raw = data_tw.parse_chart(client.chart(inst.native, "1d", data_tw.DAILY_RANGE))
        daily = data_tw.daily_bars(daily_raw)
        frames["1d"] = daily
        frames["1w"] = data_tw.weekly_bars(daily)
    except (SourceFetchError, ValueError, KeyError, TypeError) as exc:
        errors["1d"] = errors["1w"] = f"{type(exc).__name__}: {exc}"
    try:
        hourly = None
        ranges = (data_tw.HOURLY_RANGE,) + data_tw.HOURLY_FALLBACK_RANGES
        for i, rng in enumerate(ranges):
            try:
                hourly = data_tw.parse_chart(client.chart(inst.native, "60m", rng))
                break
            except SourceFetchError as exc:
                if "HTTP 422" not in str(exc) or i == len(ranges) - 1:
                    raise
        frames["4h"] = data_tw.four_hour_bars(hourly, daily_raw)
    except (SourceFetchError, ValueError, KeyError, TypeError) as exc:
        errors["4h"] = f"{type(exc).__name__}: {exc}"
    return frames, errors


def source_of(inst: Instrument) -> str:
    return inst.source if inst.market != "stock" else inst.exchange


def instruments_file(market: str, root: Path = DATA_DIR) -> Path:
    return Path(root) / market / "instruments.csv"


def save_instruments(market: str, insts: Sequence[Instrument], root: Path = DATA_DIR) -> Path:
    path = instruments_file(market, root)
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([{"base": i.base, "symbol": i.symbol, "native": i.native, "exchange": i.exchange,
                   "market": i.market, "name": i.name} for i in insts]).to_csv(path, index=False, encoding="utf-8")
    return path


def load_instruments(market: str, root: Path = DATA_DIR) -> list[Instrument]:
    df = pd.read_csv(instruments_file(market, root), encoding="utf-8", dtype=str, keep_default_na=False)
    return [Instrument(base=r.base, symbol=r.symbol, native=r.native, exchange=r.exchange, market=r.market,
                       name=r.name) for r in df.itertuples(index=False)]
