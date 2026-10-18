"""Synthetic OHLCV builders for tests (deterministic)."""
from __future__ import annotations

import numpy as np
import pandas as pd

FREQ = {"4h": "4h", "1d": "1D", "1w": "7D"}

# low prices at waypoints: LS=20, N1=35, H=50, N2=65, RS=80
IHS_WATCH = [(0, 115), (20, 100), (35, 110), (50, 90), (65, 110), (80, 100), (99, 106)]
# same but rises through the neckline (N1/N2 high = 111) after RS; n = 97
IHS_BREAK = [(0, 115), (20, 100), (35, 110), (50, 90), (65, 110), (80, 100), (96, 116)]


def make_df(waypoints, n: int | None = None, spread: float = 1.0, noise: float = 0.0,
            seed: int = 0, volume=100.0, timeframe: str = "4h",
            start: str = "2024-01-01") -> pd.DataFrame:
    """Piecewise-linear lows through `waypoints` [(index, low)], high = low + spread,
    close = low + spread/2. Noise is zero exactly at waypoints, so the waypoint
    bars stay the strict extremes as long as |slope| > 2*noise."""
    idx = [w[0] for w in waypoints]
    px = [float(w[1]) for w in waypoints]
    n = n or idx[-1] + 1
    base = np.interp(np.arange(n), idx, px)
    if noise:
        rng = np.random.default_rng(seed)
        jitter = rng.uniform(-noise, noise, n)
        jitter[idx] = 0.0
        base = base + jitter
    low = base
    high = base + spread
    close = base + spread / 2
    open_ = np.concatenate([[close[0]], close[:-1]])
    vol = np.full(n, float(volume)) if np.isscalar(volume) else np.asarray(volume, dtype=float)
    ts = pd.date_range(start, periods=n, freq=FREQ[timeframe], tz="UTC")
    return pd.DataFrame({"timestamp": ts, "open": open_, "high": high, "low": low,
                         "close": close, "volume": vol})


def rs_low_for_diff(ls_low: float, diff: float) -> float:
    """RS.low (< LS.low) such that abs(LS-RS)/((LS+RS)/2) == diff."""
    return ls_low * (1 - diff / 2) / (1 + diff / 2)


def random_walk_df(n: int = 2000, seed: int = 7, timeframe: str = "4h") -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, n)))
    open_ = np.concatenate([[close[0]], close[:-1]])
    wick = np.abs(rng.normal(0, 0.004, n)) * close
    high = np.maximum(open_, close) + wick
    low = np.minimum(open_, close) - wick
    vol = rng.uniform(50, 150, n)
    ts = pd.date_range("2020-01-01", periods=n, freq=FREQ[timeframe], tz="UTC")
    return pd.DataFrame({"timestamp": ts, "open": open_, "high": high, "low": low,
                         "close": close, "volume": vol})
