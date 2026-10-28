"""Fundamentals data for the TW IHS study (charter D51): monthly revenue (MOPS) and institutional net buy/sell
(TWSE T86 / TPEx daily). Public endpoints only, no key, no account.

Raw files (resumable, never redownloaded when present) live under C:\\trading-data\\ihs-scanner-v2\\fundamentals\\raw:
  revenue/{sii|otc}_{0|1}/<ROC year>_<month>.html      MOPS monthly summary (big5), _0 domestic, _1 foreign-listed
  t86_twse/<YYYYMMDD>.json                             TWSE T86
  t86_tpex/<YYYYMMDD>.json                             TPEx dailyTrade

  python -m backtest.fund_data revenue            # ~ 4 x 120 requests
  python -m backtest.fund_data inst --market twse # ~ 2300 trading days, 3 s apart
  python -m backtest.fund_data inst --market tpex

Alignment helpers (pure, unit-tested) are at the bottom: revenue_available_month(), cutoff_trading_day().
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

FUND_ROOT = Path(r"C:\trading-data\ihs-scanner-v2\fundamentals")
RAW = FUND_ROOT / "raw"
TW_DATA = Path(r"C:\trading-data\ihs-scanner-v2\backtest\data\tw")
UA = {"User-Agent": "Mozilla/5.0 (research script; public data; contact via GitHub chasen8)"}
THROTTLE_S = 3.0

REV_URL = "https://mopsov.twse.com.tw/nas/t21/{kind}/t21sc03_{y}_{m}_{part}.html"
T86_TWSE = "https://www.twse.com.tw/rwd/zh/fund/T86?date={d}&selectType=ALLBUT0999&response=json"
T86_TPEX = "https://www.tpex.org.tw/www/zh-tw/insti/dailyTrade?type=Daily&sect=AL&date={d}&id=&response=json"

REV_START = (2016, 10)  # ROC 105/10: 12 months of history before the first (2017-11) training signal
INST_START = dt.date(2017, 9, 1)


class Blocked(RuntimeError):
    """Source answered with an access-control status; stop instead of working around it."""


# ------------------------------------------------------------------ fetching

class Throttle:
    def __init__(self, gap: float = THROTTLE_S):
        self.gap, self.last = gap, 0.0

    def wait(self) -> None:
        d = self.last + self.gap - time.monotonic()
        if d > 0:
            time.sleep(d)
        self.last = time.monotonic()


def _get(session, url: str, th: Throttle, tries: int = 4) -> bytes | None:
    for k in range(tries):
        th.wait()
        try:
            r = session.get(url, headers=UA, timeout=40)
        except Exception as exc:  # network hiccup
            print(f"  net error {type(exc).__name__}; retry {k + 1}", flush=True)
            time.sleep(10 * (k + 1))
            continue
        if r.status_code in (403, 429):
            if k == tries - 1:
                raise Blocked(f"{r.status_code} for {url}")
            time.sleep(60 * (k + 1))
            continue
        if r.status_code == 404:
            return None
        if r.status_code == 200:
            return r.content
        time.sleep(10 * (k + 1))
    return None


def month_list(start=REV_START, end: tuple[int, int] | None = None) -> list[tuple[int, int]]:
    """(western year, month) from start through the previous calendar month (last published batch)."""
    if end is None:
        t = dt.date.today()
        end = (t.year, t.month - 1) if t.month > 1 else (t.year - 1, 12)
    out, (y, m) = [], start
    while (y, m) <= end:
        out.append((y, m))
        y, m = (y, m + 1) if m < 12 else (y + 1, 1)
    return out


def fetch_revenue() -> None:
    import requests
    s, th = requests.Session(), Throttle()
    todo = [(k, p, y, m) for (y, m) in month_list() for k in ("sii", "otc") for p in (0, 1)]
    for i, (kind, part, y, m) in enumerate(todo, 1):
        f = RAW / "revenue" / f"{kind}_{part}" / f"{y - 1911}_{m}.html"
        if f.exists():
            continue
        b = _get(s, REV_URL.format(kind=kind, y=y - 1911, m=m, part=part), th)
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(b if b is not None else b"")  # empty file = source has no page for that month
        print(f"{i}/{len(todo)} {kind}_{part} {y}-{m:02d} {0 if b is None else len(b)} bytes", flush=True)


def trading_days(start: dt.date = INST_START) -> list[dt.date]:
    """Taipei trading dates from the 2330 daily cache (bar timestamp 01:00 UTC = 09:00 Taipei)."""
    df = pd.read_csv(TW_DATA / "twse" / "2330_1d.csv", encoding="utf-8")
    ts = pd.to_datetime(df["timestamp"], utc=True, format="ISO8601") + pd.Timedelta(hours=8)
    return [d for d in sorted(set(ts.dt.date)) if d >= start]


def _inst_ok(market: str, raw: bytes) -> bool:
    try:
        j = json.loads(raw.decode("utf-8"))
    except Exception:
        return False
    if market == "twse":
        st = str(j.get("stat", ""))
        return st == "OK" or "沒有符合條件" in st
    return isinstance(j, dict) and "tables" in j


def fetch_inst(market: str) -> None:
    import requests
    s, th = requests.Session(), Throttle()
    days = trading_days()
    miss = 0
    for i, d in enumerate(days, 1):
        f = RAW / f"t86_{market}" / f"{d:%Y%m%d}.json"
        if f.exists():
            continue
        url = T86_TWSE.format(d=f"{d:%Y%m%d}") if market == "twse" else T86_TPEX.format(d=f"{d:%Y/%m/%d}")
        b = _get(s, url, th)
        if b is None or not _inst_ok(market, b):
            miss += 1
            print(f"{i}/{len(days)} {d} FAILED (will retry on rerun)", flush=True)
            if miss > 30:
                raise Blocked("too many consecutive failures")
            continue
        miss = 0
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(b)
        if i % 25 == 0:
            print(f"{i}/{len(days)} {d}", flush=True)


# ------------------------------------------------------------------ parsing

_ROW = re.compile(r"<tr align=right>(.*?)</tr>", re.I | re.S)
_CELL = re.compile(r"<td[^>]*>(.*?)</td>", re.I | re.S)


def _num(s: str) -> float:
    s = s.replace(",", "").replace("&nbsp;", "").strip()
    try:
        return float(s)
    except ValueError:
        return float("nan")


def parse_revenue_html(raw: bytes) -> list[dict]:
    """One dict per company: code, name, rev (千元), rev_prev, rev_ly, yoy_pct (as reported, by the company)."""
    text = raw.decode("big5", errors="replace")
    out = []
    for row in _ROW.findall(text):
        cells = [re.sub(r"<[^>]+>", "", c).strip() for c in _CELL.findall(row)]
        if len(cells) < 7 or not re.fullmatch(r"[0-9A-Za-z]{4,6}", cells[0]):
            continue
        out.append({"code": cells[0], "name": cells[1], "rev": _num(cells[2]), "rev_prev": _num(cells[3]),
                    "rev_ly": _num(cells[4]), "yoy_pct": _num(cells[6])})
    return out


def load_revenue() -> pd.DataFrame:
    """Long table: code, ym (pandas Period M, the revenue month), rev, rev_ly, yoy (fraction), yoy_calc (fraction)."""
    rows = []
    for f in sorted((RAW / "revenue").glob("*/*.html")):
        b = f.read_bytes()
        if not b:
            continue
        roc, m = (int(x) for x in f.stem.split("_"))
        for r in parse_revenue_html(b):
            r["ym"] = pd.Period(year=roc + 1911, month=m, freq="M")
            rows.append(r)
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df["yoy"] = df["yoy_pct"] / 100.0
    df["yoy_calc"] = np.where(df["rev_ly"] > 0, df["rev"] / df["rev_ly"] - 1.0, np.nan)
    # a company may appear in sii and otc pages over time; one row per (code, month)
    return df.drop_duplicates(["code", "ym"], keep="first").sort_values(["code", "ym"]).reset_index(drop=True)


def parse_t86(market: str, raw: bytes) -> list[dict]:
    """Rows of (code, foreign_net, trust_net) in shares for one day; [] when the day has no data."""
    j = json.loads(raw.decode("utf-8"))
    out = []
    if market == "twse":
        if j.get("stat") != "OK":
            return out
        fields = j["fields"]
        f_idx = [i for i, n in enumerate(fields) if "外" in n and "買賣超股數" in n]
        t_idx = [i for i, n in enumerate(fields) if n.startswith("投信買賣超")]
        for row in j["data"]:
            out.append({"code": row[0].strip(), "foreign_net": sum(_num(row[i]) for i in f_idx),
                        "trust_net": sum(_num(row[i]) for i in t_idx)})
        return out
    for t in j.get("tables", []):
        for row in t.get("data", []) or []:
            n = len(row)
            if n >= 24:  # 25-col layout: foreign(ex-dealer) 2-4, foreign dealer 5-7, foreign total 8-10, trust 11-13
                fi, ti = 10, 13
            elif n >= 16:  # older 17-col layout: foreign 2-4, trust 5-7
                fi, ti = 4, 7
            else:
                continue
            out.append({"code": str(row[0]).strip(), "foreign_net": _num(row[fi]), "trust_net": _num(row[ti])})
    return out


def load_inst() -> pd.DataFrame:
    """Long table: date, code, foreign_net, trust_net (shares) from both markets, plus the set of dates seen."""
    rows = []
    for mk in ("twse", "tpex"):
        for f in sorted((RAW / f"t86_{mk}").glob("*.json")):
            d = dt.datetime.strptime(f.stem, "%Y%m%d").date()
            for r in parse_t86(mk, f.read_bytes()):
                r["date"], r["market"] = d, mk
                rows.append(r)
    return pd.DataFrame(rows)


# ------------------------------------------------------------------ time alignment (locked by tests)

def revenue_available_month(d: dt.date) -> pd.Period:
    """Latest revenue month usable on calendar date d. Monthly revenue is due by the 10th of the following month, so
    from the 11th of month M on the month M-1 figure is usable; on days 1-10 only M-2 is."""
    p = pd.Period(year=d.year, month=d.month, freq="M")
    return p - 1 if d.day >= 11 else p - 2


def cutoff_trading_day(cal: list[dt.date], d: dt.date, lag: int = 1) -> dt.date | None:
    """Last calendar-trading day usable for a decision whose entry is on date d: the lag-th trading day strictly before
    d (lag=1: the previous trading day, whose 3-institution report is published after its close; lag=2: one more
    day of safety). None when the calendar does not reach back far enough."""
    i = int(np.searchsorted(np.asarray(cal, dtype="datetime64[D]"), np.datetime64(d), side="left"))  # first day >= d
    j = i - lag
    return cal[j] if j >= 0 else None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("stage", choices=["revenue", "inst"])
    ap.add_argument("--market", choices=["twse", "tpex"], default="twse")
    a = ap.parse_args(argv)
    if a.stage == "revenue":
        fetch_revenue()
    else:
        fetch_inst(a.market)
    return 0


if __name__ == "__main__":
    sys.exit(main())
