"""Download the D22 golden-example and D36 case K lines into tests/fixtures/<name>.csv (public, no key).

    /c/trading-data/ihs-scanner-v2/venv/Scripts/python.exe scripts/fetch_golden_fixtures.py [--force]

Sources (all public market data):
  binance_spot  https://data-api.binance.vision/api/v3/klines
  binance_usdm  https://fapi.binance.com/fapi/v1/klines
  okx_spot      https://www.okx.com/api/v5/market/history-candles  (bar 1Dutc / 4H, UTC aligned)

Each fixture keeps up to BARS_BEFORE closed bars before the screenshot time plus up to BARS_AFTER
bars after it (only bars that are closed now). Columns: timestamp (ISO UTC open time), open, high,
low, close, volume.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tests.golden_examples import BARS_AFTER, BARS_BEFORE, D36_CASES, GOLDEN_EXAMPLES  # noqa: E402

FIXTURE_DIR = ROOT / "tests" / "fixtures"
BAR_MS = {"4h": 4 * 3600 * 1000, "1d": 86400 * 1000, "1w": 7 * 86400 * 1000}
# D36: bar open times are multiples of the bar length from this offset; Binance / OKX weekly bars open on
# Monday 00:00 UTC and 1970-01-01 was a Thursday -> the first Monday is 4 days later
BAR_OFFSET_MS = {"4h": 0, "1d": 0, "1w": 4 * 86400 * 1000}
TIMEOUT = 20


def _get(url: str, params: dict) -> list:
    for attempt in range(4):
        resp = requests.get(url, params=params, timeout=TIMEOUT)
        if resp.status_code == 429:
            time.sleep(float(resp.headers.get("Retry-After", 2)) + 1)
            continue
        resp.raise_for_status()
        return resp.json()
    raise RuntimeError(f"rate limited: {url}")


def _binance(base: str, path: str, symbol: str, tf: str, start_ms: int, end_ms: int) -> list[list]:
    rows: list[list] = []
    cursor = start_ms
    while cursor <= end_ms:
        data = _get(base + path, {"symbol": symbol, "interval": tf, "startTime": cursor,
                                  "endTime": end_ms, "limit": 1000})
        if not data:
            break
        rows.extend([int(k[0]), float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[5])] for k in data)
        nxt = int(data[-1][0]) + BAR_MS[tf]
        if nxt <= cursor:
            break
        cursor = nxt
        time.sleep(0.2)
    return rows


def _okx(inst: str, tf: str, start_ms: int, end_ms: int) -> list[list]:
    bar = {"4h": "4Hutc", "1d": "1Dutc", "1w": "1Wutc"}[tf]
    rows: list[list] = []
    after = end_ms + 1  # OKX "after" = records with ts < after (newest first, 100 per page)
    while True:
        data = _get("https://www.okx.com/api/v5/market/history-candles",
                    {"instId": inst, "bar": bar, "after": str(after), "limit": "100"})
        payload = data.get("data", []) if isinstance(data, dict) else []
        if not payload:
            break
        for k in payload:
            ts = int(k[0])
            if start_ms <= ts <= end_ms:
                rows.append([ts, float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[5])])
        oldest = min(int(k[0]) for k in payload)
        if oldest <= start_ms or oldest >= after:
            break
        after = oldest
        time.sleep(0.25)
    return rows


def fetch_example(name: str, spec: dict) -> pd.DataFrame:
    tf = spec["timeframe"]
    bar = BAR_MS[tf]
    shot = int(pd.Timestamp(spec["screenshot_utc"]).value // 1_000_000)
    now = int(time.time() * 1000)
    off = BAR_OFFSET_MS[tf]
    last_closed_open = ((shot - off) // bar) * bar + off - bar
    start = last_closed_open - (BARS_BEFORE - 1) * bar
    end = min(last_closed_open + BARS_AFTER * bar, now - bar)
    src = spec["source"]
    if src == "binance_spot":
        rows = _binance("https://data-api.binance.vision", "/api/v3/klines", spec["symbol"], tf, start, end)
    elif src == "binance_usdm":
        rows = _binance("https://fapi.binance.com", "/fapi/v1/klines", spec["symbol"], tf, start, end)
    elif src == "okx_spot":
        rows = _okx(spec["symbol"], tf, start, end)
    else:
        raise ValueError(f"unknown source {src!r}")
    df = pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df = df.drop_duplicates("timestamp").sort_values("timestamp").reset_index(drop=True)
    df = df[df["timestamp"] + bar <= now]  # closed bars only
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True).dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    return df


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--force", action="store_true", help="re-download existing fixtures")
    args = ap.parse_args(argv)
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    for name, spec in {**GOLDEN_EXAMPLES, **D36_CASES}.items():
        path = FIXTURE_DIR / f"{name}.csv"
        if path.exists() and not args.force:
            print(f"[skip] {path.name} exists")
            continue
        df = fetch_example(name, spec)
        df.to_csv(path, index=False, encoding="utf-8")
        print(f"[ok] {path.name}: {len(df)} bars {df['timestamp'].iloc[0]} .. {df['timestamp'].iloc[-1]}"
              f" ({path.stat().st_size // 1024} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
