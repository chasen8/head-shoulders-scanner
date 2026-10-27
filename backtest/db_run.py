"""D49 double bottom (W): dry-run on the cached K lines and the backtest driver pieces.

  python -m backtest.db_run dryrun [--workers 15] [--out DIR] [--charts DIR]
      Detection as of the LAST cached bar of every series (the live-scan view: same 1000-bar / 1w 500-bar window,
      market config), counts per market x timeframe x status, rows to <out>/dryrun_rows.csv, three PNG charts
      (wb-*.png, drawn with chart_image.render_triangle_chart) to --charts.
  The walk-forward backtest itself is the D40 driver:
      python -m backtest.run run --market crypto --families db --tag db
      python -m backtest.run run --market tw     --families db --tag db
  and the comparison report: python -m backtest.db_report (docs/backtest-double-bottom.md).
"""
from __future__ import annotations

import argparse
import multiprocessing as mp
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

from backtest import data as D
from backtest import engine as E
from ihs_scanner.chart_image import render_triangle_chart
from ihs_scanner.double_bottom import detect_double_bottoms

OUT_DEFAULT = Path(r"C:\trading-data\ihs-scanner-v2\output\double-bottom")
TFS = ("4h", "1d", "1w")


def _unix(ts) -> int:
    return int(pd.Timestamp(ts).timestamp())


def _dry_worker(task: tuple) -> tuple[list[dict], dict]:
    market, inst_d = task
    from ihs_scanner.sources import Instrument
    inst = Instrument(**inst_d)
    src = D.source_of(inst)
    rows, seconds = [], {}
    for tf in TFS:
        p = D.cache_file(market, src, inst.symbol, tf)
        if not p.exists():
            continue
        df = D.load_frame(p)
        if len(df) < E.START[tf] + 1:
            continue
        df = df.iloc[-E.WINDOW[tf]:]
        t0 = time.perf_counter()
        res = detect_double_bottoms(df, tf, {"include_stale": False}, symbol=inst.symbol, market=market)
        seconds[tf] = time.perf_counter() - t0
        for r in res:
            rows.append({"market": market, "source": src, **r})
        # stale ones (older W's still inside the window) are counted from a separate include_stale run, so they do
        # not take part in the live de-duplication
        for r in detect_double_bottoms(df, tf, {"include_stale": True}, symbol=inst.symbol, market=market):
            if r["status"] == "stale":
                rows.append({"market": market, "source": src, **r})
    return rows, seconds


def doc_for(row: dict, df: pd.DataFrame) -> dict:
    """tri_bars-style chart document for chart_image.render_triangle_chart: the neckline as the 'resistance' line,
    the line through the two bottoms as the 'support' line, B1 / P / B2 as touches."""
    ts = pd.to_datetime(df["timestamp"], utc=True)
    i1 = int(ts.searchsorted(pd.Timestamp(row["B1_time"])))
    start = max(0, i1 - 25)
    seg = df.iloc[start:]
    bars = [[_unix(t), float(o), float(h), float(lo), float(c), float(v)]
            for t, o, h, lo, c, v in zip(ts.iloc[start:], seg["open"], seg["high"], seg["low"], seg["close"],
                                         seg["volume"])]
    t1, tp, t2, tl = (_unix(row[k]) for k in ("B1_time", "P_time", "B2_time", "as_of_time"))
    end = max(tl, t2)
    doc = {
        "bars": bars,
        "resistance_line": [{"time": t1, "price": row["P_price"]}, {"time": end, "price": row["P_price"]}],
        "support_line": [{"time": t1, "price": row["B1_price"]}, {"time": t2, "price": row["B2_price"]}],
        "resistance_ext": [], "support_ext": [],
        "touches": [{"time": t1, "price": row["B1_price"], "side": "support"},
                    {"time": tp, "price": row["P_price"], "side": "resistance"},
                    {"time": t2, "price": row["B2_price"], "side": "support"}],
        "breakout": ({"time": _unix(row["breakout_time"]), "price": row["breakout_price"], "direction": "up"}
                     if pd.notna(row.get("breakout_time")) else None),
        "levels": [{"key": "target_conservative", "label": "TP1", "price": row["tp1"]},
                   {"key": "target", "label": "TP2", "price": row["tp2"]},
                   {"key": "invalidation_price", "label": "INVAL", "price": row["invalidation_price"]}],
        "timeframe": row["timeframe"],
    }
    return doc


def render_row(row: dict, out: Path) -> Path:
    """Draw one dry-run row from its cached series (title reads '<sym> <tf> double bottom triangle <status>': the
    triangle renderer's fixed wording; only the lines / points / levels are the W's)."""
    p = D.cache_file(row["market"], row["source"], row["symbol"], row["timeframe"])
    df = D.load_frame(p).iloc[-E.WINDOW[row["timeframe"]]:]
    png = render_triangle_chart({**row, "triangle_type": "double bottom"}, doc_for(row, df))
    out.write_bytes(png)
    return out


def cmd_dryrun(args) -> int:
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    rows, secs = [], {tf: [] for tf in TFS}
    for market in ("crypto", "tw"):
        insts = D.load_instruments(market)
        tasks = [(market, {"base": i.base, "symbol": i.symbol, "native": i.native, "exchange": i.exchange,
                           "market": i.market, "name": i.name}) for i in insts]
        with mp.Pool(args.workers) as pool:
            for r, s in pool.imap_unordered(_dry_worker, tasks, chunksize=8):
                rows.extend(r)
                for tf, v in s.items():
                    secs[tf].append(v)
        print(f"[dryrun] {market}: {len(insts)} instruments, {time.time() - t0:.0f}s", flush=True)
    df = pd.DataFrame(rows)
    df.to_csv(out / "dryrun_rows.csv", index=False, encoding="utf-8")
    print(f"\ndetection per series: " + ", ".join(f"{tf} mean {1e3 * np.mean(v):.1f} ms max {1e3 * np.max(v):.0f} ms "
                                                  f"(n={len(v)})" for tf, v in secs.items() if v))
    if df.empty:
        print("no W found")
        return 0
    tab = (df.groupby(["market", "timeframe", "status"]).size().unstack("status", fill_value=0)
           .reindex(columns=["forming", "watchlist", "confirmed", "stale"], fill_value=0))
    print(tab.to_string())
    print("\ntotal non-stale:", int((df["status"] != "stale").sum()), " stale:", int((df["status"] == "stale").sum()))
    if args.charts:
        cdir = Path(args.charts)
        cdir.mkdir(parents=True, exist_ok=True)
        live = df[df["status"] != "stale"].sort_values("pattern_score", ascending=False)
        picks = []
        for status, market in (("confirmed", "crypto"), ("watchlist", "tw"), ("confirmed", "tw"), ("watchlist", "crypto")):
            g = live[(live["status"] == status) & (live["market"] == market) & (live["timeframe"] != "4h")]
            if len(g) and len(picks) < 3:
                picks.append(g.iloc[len(g) // 2 if len(g) > 4 else 0].to_dict())
        for r in picks:
            name = f"wb-{r['market']}-{r['symbol']}-{r['timeframe']}-{r['status']}.png"
            render_row(r, cdir / name)
            print("chart:", cdir / name)
    return 0


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("dryrun")
    d.add_argument("--workers", type=int, default=15)
    d.add_argument("--out", default=str(OUT_DEFAULT))
    d.add_argument("--charts", default="")
    args = p.parse_args(argv)
    return {"dryrun": cmd_dryrun}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
