"""Binance fetcher with a fake session (no network)."""
import pandas as pd
import pytest

from ihs_scanner import data_binance as db

H4 = 4 * 3600 * 1000
T0 = 1_700_000_000_000 - (1_700_000_000_000 % H4)


def _kline(open_ms):
    return [open_ms, "1.0", "2.0", "0.5", "1.5", "10.0", open_ms + H4 - 1, "0", 1, "0", "0", "0"]


class FakeResp:
    def __init__(self, data, status=200):
        self._data, self.status_code = data, status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._data


class FakeSession:
    """Serves 2500 consecutive 4h klines; the last one is still open at NOW."""

    def __init__(self, fail_primary=False, total=2500):
        self.calls = []
        self.fail_primary = fail_primary
        self.opens = [T0 + i * H4 for i in range(total)]

    def get(self, url, params, timeout):
        self.calls.append((url, dict(params)))
        if self.fail_primary and url.startswith(db.PRIMARY_BASE):
            return FakeResp({"code": 0, "msg": "blocked"}, status=451)
        end = params.get("endTime")
        opens = [o for o in self.opens if end is None or o <= end]
        return FakeResp([_kline(o) for o in opens[-params["limit"]:]])


NOW = T0 + 2499 * H4 + 1000  # inside the last (open) candle


def test_fetch_paginates_and_drops_open_candle(tmp_path):
    s = FakeSession()
    df = db.fetch_klines("btcusdt", "4h", limit=1500, session=s, now_ms=NOW, cache_dir=tmp_path)
    assert len(df) == 1500
    assert len(s.calls) == 2 and "endTime" in s.calls[1][1]
    assert s.calls[0][1]["symbol"] == "BTCUSDT" and s.calls[0][1]["interval"] == "4h"
    assert df["timestamp"].is_monotonic_increasing and df["timestamp"].is_unique
    assert str(df["timestamp"].dt.tz) == "UTC"
    assert df["timestamp"].iloc[-1] == pd.Timestamp(T0 + 2498 * H4, unit="ms", tz="UTC")
    assert (df["close_time"] < pd.Timestamp(NOW, unit="ms", tz="UTC")).all()
    assert df["close"].dtype == float
    cached = db.load_cached_klines("BTCUSDT", "4h", tmp_path)
    assert len(cached) == 1500 and cached["timestamp"].iloc[0] == df["timestamp"].iloc[0]


def test_fallback_endpoint_used_when_primary_fails():
    s = FakeSession(fail_primary=True)
    df = db.fetch_klines("ETHUSDT", "4h", limit=10, session=s, now_ms=NOW)
    assert len(df) == 10
    assert s.calls[0][0].startswith(db.PRIMARY_BASE)
    assert s.calls[1][0].startswith(db.FALLBACK_BASE)


def test_all_endpoints_fail_raises():
    class Dead:
        def get(self, *a, **k):
            raise ConnectionError("no route")

    with pytest.raises(db.BinanceFetchError):
        db.fetch_klines("BTCUSDT", "1d", limit=5, session=Dead(), now_ms=NOW)


def test_bad_timeframe():
    with pytest.raises(ValueError):
        db.fetch_klines("BTCUSDT", "15m", session=FakeSession())
