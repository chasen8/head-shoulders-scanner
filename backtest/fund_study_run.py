"""D51 fundamentals study runner.

  python -m backtest.fund_study_run features   # join revenue / institutional features onto the D40 TW signals
  python -m backtest.fund_study_run analyze    # quintile tables, rho, rules -> OUT_DIR/*.md (input of docs/fundamentals-study.md)
  python -m backtest.fund_study_run equity     # D44 account simulation for the rules (needs the D50 equity inputs)

Prerequisite: python -m backtest.fund_data revenue / inst --market twse / inst --market tpex (raw files in
C:\\trading-data\\ihs-scanner-v2\\fundamentals\\raw). Inputs from D50: output/score-study/merged_with_scores.csv.gz.
Outputs (not in git): C:\\trading-data\\ihs-scanner-v2\\output\\fundamentals-study\\.
"""
from __future__ import annotations

import argparse
import datetime as dt
import math
import sys

import numpy as np
import pandas as pd

from backtest import data as D
from backtest import fund_data as FD
from backtest import fund_study as FS
from backtest import score_study as S

OUT_DIR = D.ROOT.parent / "output" / "fundamentals-study"
SCORED = D.ROOT.parent / "output" / "score-study" / "merged_with_scores.csv.gz"
FEATURES = OUT_DIR / "fund_features.csv.gz"
CACHE = FD.FUND_ROOT / "cache"
KEY = ["market", "source", "symbol", "timeframe", "pattern_id"]
LAGS = {"": 2, "l1_": 1}  # main = conservative (<= t-1 for 1d); l1_ = previous trading day (= the signal day)


def load_signals() -> pd.DataFrame:
    sc = pd.read_csv(SCORED, low_memory=False)
    sc = sc[(sc["market"] == "tw") & sc["exit"].isin(["TP1", "TP2"])].copy()
    sc["symbol"] = sc["symbol"].astype(str)
    return sc


def cached_inst() -> pd.DataFrame:
    CACHE.mkdir(parents=True, exist_ok=True)
    p = CACHE / "inst_long.pkl.gz"
    if p.exists():
        return pd.read_pickle(p)
    df = FD.load_inst()
    df.to_pickle(p)
    return df


def fetched_dates(market: str) -> set[dt.date]:
    out = set()
    for f in (FD.RAW / f"t86_{market}").glob("*.json"):
        if FD.parse_t86(market, f.read_bytes()):
            out.add(dt.datetime.strptime(f.stem, "%Y%m%d").date())
    return out


def run_features() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    sc = load_signals()
    sig = sc.drop_duplicates(KEY)[KEY + ["entry_time"]].reset_index(drop=True)
    sig["entry_date"] = [FS.tw_date(pd.Timestamp(x)) for x in pd.to_datetime(sig["entry_time"], utc=True)]
    print(f"{len(sig)} signals, {sig['symbol'].nunique()} symbols", flush=True)
    rev = FD.load_revenue()
    inst = cached_inst()
    cal = FD.trading_days()
    fetched = {m: fetched_dates(m) for m in ("twse", "tpex")}
    print(f"revenue rows {len(rev)}, inst rows {len(inst)}, calendar {len(cal)} days, "
          f"fetched twse {len(fetched['twse'])} tpex {len(fetched['tpex'])}", flush=True)
    rev_by = {c: g.set_index("ym")["yoy"].fillna(g.set_index("ym")["yoy_calc"]).sort_index()
              for c, g in rev.groupby("code")}
    inst_by = {k: g for k, g in inst.groupby(["market", "code"])}
    rows = []
    for (source, symbol), g in sig.groupby(["source", "symbol"]):
        p = D.cache_file("tw", source, symbol, "1d")
        if not p.exists():
            continue
        px = D.load_frame(p)
        vol = pd.Series(px["volume"].to_numpy(float),
                        index=[FS.tw_date(t) for t in px["timestamp"]]).groupby(level=0).last()
        net = inst_by.get((source, symbol))
        net = net[["date", "foreign_net", "trust_net"]] if net is not None else pd.DataFrame(
            columns=["date", "foreign_net", "trust_net"])
        panel = FS.inst_panel(cal, fetched[source], net, vol)
        yoy = rev_by.get(symbol)
        for _, r in g.iterrows():
            rec = r[KEY].to_dict()
            rec["entry_date"] = r["entry_date"].isoformat()
            rec["has_inst_rows"] = net is not None and len(net) > 0
            rec["has_rev_rows"] = yoy is not None
            for pre, lag in LAGS.items():
                f = FS.signal_row(r["entry_date"], yoy, panel, cal, lag)
                rec.update({pre + k: v for k, v in f.items()})
            rows.append(rec)
    out = pd.DataFrame(rows)
    out.to_csv(FEATURES, index=False, encoding="utf-8")
    print(f"wrote {FEATURES} rows={len(out)}", flush=True)
    for c in FS.ALL_FEATS:
        print(f"  {c}: non-NaN {out[c].notna().mean() * 100:.1f}%", flush=True)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("stage", choices=["features", "analyze", "equity", "examples"])
    a = ap.parse_args(argv)
    if a.stage == "features":
        run_features()
    else:
        from backtest import fund_study_report as R
        {"analyze": R.main, "equity": R.equity, "examples": R.examples}[a.stage]()
    return 0


if __name__ == "__main__":
    sys.exit(main())
