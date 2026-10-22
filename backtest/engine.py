"""Walk-forward backtest engine (charter D40). Pure functions, no network; ihs_scanner is only imported.

No look-ahead (the most important rule):
  * Signals: at eval bar t (t = start, start + step, ...; the grid does not depend on the series length) the
    detectors run on df[max(0, t - window + 1) : t + 1] only -- the same 1000-bar (1w: 500) window and the same
    config the live scanner uses (market_config / triangle_config, D33, D36). A pattern_id's FIRST appearance with
    the wanted status at t is an entry at close[t]; stop and targets are the values output at t (a later bar never
    updates them). tests/test_backtest.py rewrites every bar after t and checks the entries up to t do not change.
  * ATR(14) for the ATR stop is Wilder's ATR[t] (bars <= t only, detector.wilder_atr).
  * Exits: bars t+1 .. t+max_hold only.

Online view (what the live site would list at bar t):
  IHS        find_pattern_candidates(include_stale=True) -> drop stale -> deduplicate_patterns(D36 priority) ->
             min_pattern_score -> 1d: supersede_by_higher_timeframe against the 1w view as of the same time
             (= detect_inverse_head_shoulders with include_stale=False, then build_payload's D36 step).
  triangles  detect_triangles(triangle_config(market), include_stale=False) (stale filter + dedup inside).

Exit rules per bar i > t (long; a short mirrors them):
  low <= stop -> stop (fill at min(open, stop): a gap through the stop fills at the open);
  else high >= TP -> take profit at TP; same bar both -> stop (conservative);
  no exit after max_hold bars -> close[t + max_hold] ("timeout"); data ends first -> "open" (not counted).
  half: 50% at the nearer target, 50% at the farther; after the first target the rest's stop moves to the entry
  price (breakeven) from the NEXT bar on; a bar that reaches both targets closes both halves.
Costs (per side): crypto 0.1% / 0.1%; TW buy 0.1425%, sell 0.1425% + 0.3% tax.
One open position per (symbol, timeframe, strategy): a signal at t <= the previous trade's exit bar is skipped.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping, Sequence

import numpy as np
import pandas as pd

from ihs_scanner.config import market_config, tf_value, triangle_config
from ihs_scanner.detector import deduplicate_patterns, find_pattern_candidates, wilder_atr
from ihs_scanner.scanner import supersede_by_higher_timeframe
from ihs_scanner.triangles import detect_triangles

TIMEFRAMES = ("4h", "1d", "1w")
MAX_HOLD = {"4h": 180, "1d": 120, "1w": 52}  # = tp_tracker.TP_CONFIG["max_bars"] (D39)
WINDOW = {"4h": 1000, "1d": 1000, "1w": 500}  # live scan: --limit 1000, 1w capped at 500 (build_site / data_tw)
START = {"4h": 24, "1d": 20, "1w": 10}  # first eval bar (no pattern can be complete earlier)
ATR_MULT = 1.5
COSTS = {"crypto": {"buy": 0.001, "sell": 0.001},
         "tw": {"buy": 0.001425, "sell": 0.001425 + 0.003}}
EXITS = ("TP1", "TP2", "half")
IHS_MODES = {"confirmed": "A", "watchlist": "B"}  # A 突破確認, B 右肩確認
TRI_DIRS = {"breakout_up": 1, "breakout_down": -1}
TRI_STOPS = ("inval", "atr")


def default_step(timeframe: str) -> int:
    return int(tf_value(market_config("crypto"), "pivot_window_by_timeframe", timeframe))


def eval_points(n: int, timeframe: str, step: int) -> range:
    return range(START[timeframe], n, max(1, int(step)))


def window(df: pd.DataFrame, t: int, timeframe: str) -> pd.DataFrame:
    return df.iloc[max(0, t - WINDOW[timeframe] + 1): t + 1]


# ------------------------------------------------------------------ online views

def ihs_config(market: str) -> dict[str, Any]:
    return market_config(market, {"include_stale": True})


def ihs_view(df_win: pd.DataFrame, timeframe: str, cfg: Mapping[str, Any], symbol: str) -> list[dict[str, Any]]:
    """What the live scan lists for this series as of the window's last bar (before the cross-timeframe step)."""
    cands = find_pattern_candidates(df_win, timeframe, cfg, symbol)
    fresh = [r for r in cands if r["status"] != "stale"]
    kept = deduplicate_patterns(fresh, str(cfg["dedup_priority"]))
    return [r for r in kept if r["pattern_score"] >= float(cfg["min_pattern_score"])]


def tri_config(market: str) -> dict[str, Any]:
    return triangle_config({"include_stale": False}, market=market)


def weekly_views(df_w: pd.DataFrame, cfg: Mapping[str, Any], symbol: str) -> tuple[np.ndarray, list[list[dict]]]:
    """IHS 1w views at EVERY 1w bar (for the D36 1w -> 1d supersede step): (close_time ns array, views)."""
    times, views = [], []
    ct = pd.to_datetime(df_w["close_time"], utc=True)
    for t in range(START["1w"], len(df_w)):
        try:
            rows = ihs_view(window(df_w, t, "1w"), "1w", cfg, symbol)
        except Exception:  # same as an empty live result for that week
            rows = []
        views.append([r for r in rows if r.get("neckline_change") is not None
                      and r["neckline_change"] < 0])  # only falling-neckline 1w rows can supersede
        times.append(ct.iloc[t].value)
    return np.array(times, dtype=np.int64), views


def _weekly_at(wk: tuple[np.ndarray, list] | None, close_time_ns: int) -> list[dict]:
    """The 1w view of the newest 1w bar closed by `close_time_ns` (no 1w bar closing later is used)."""
    if wk is None or not len(wk[0]):
        return []
    j = int(np.searchsorted(wk[0], close_time_ns, side="right")) - 1
    return wk[1][j] if j >= 0 else []


# ------------------------------------------------------------------ signals

IHS_KEEP = ("pattern_id", "status", "pattern_score", "LS_time", "H_time", "RS_time", "H_price", "RS_price",
            "breakout_level", "breakout_time", "signal_time", "tp1", "tp2", "invalidation_price", "neckline_change",
            "neckline_touches", "head_rise_pct")
TRI_KEEP = ("pattern_id", "status", "triangle_type", "score", "start_time", "breakout_time", "breakout_price",
            "target", "target_conservative", "invalidation_price", "touches_total", "converge_ratio")


def _plain(v: Any) -> Any:
    if isinstance(v, pd.Timestamp):
        return v.tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%SZ") if v.tzinfo else v.strftime("%Y-%m-%dT%H:%M:%SZ")
    if isinstance(v, (np.floating, np.integer)):
        return v.item()
    return v


def ihs_signals(df: pd.DataFrame, timeframe: str, market: str, *, step: int | None = None, symbol: str = "",
                weekly: tuple[np.ndarray, list] | None = None,
                view_fn: Callable | None = None, errors: list | None = None) -> list[dict[str, Any]]:
    """First appearance of each pattern_id as confirmed (mode A) / watchlist (mode B) on the eval grid.
    A detector exception at one eval bar = an empty view there (the live build records the series as a failure
    and lists nothing for it); (t, message) is appended to `errors`."""
    cfg = ihs_config(market)
    step = step or default_step(timeframe)
    view_fn = view_fn or ihs_view
    ct = pd.to_datetime(df["close_time"], utc=True) if "close_time" in df.columns else None
    seen: dict[str, set] = {"A": set(), "B": set()}
    out = []
    for t in eval_points(len(df), timeframe, step):
        try:
            rows = view_fn(window(df, t, timeframe), timeframe, cfg, symbol)
        except Exception as exc:  # e.g. ZeroDivisionError on a zero-priced bar
            if errors is not None:
                errors.append((t, f"{type(exc).__name__}: {exc}"))
            continue
        if timeframe == "1d" and weekly is not None and rows and ct is not None:
            big = _weekly_at(weekly, ct.iloc[t].value)
            if big:
                kept, _gone = supersede_by_higher_timeframe(rows + big)
                rows = [r for r in kept if r["timeframe"] == "1d"]
        for r in rows:
            mode = IHS_MODES.get(r["status"])
            if mode is None or r["pattern_id"] in seen[mode]:
                continue
            seen[mode].add(r["pattern_id"])
            out.append({"family": "ihs", "mode": mode, "t": t, **{k: _plain(r.get(k)) for k in IHS_KEEP}})
    return out


def triangle_signals(df: pd.DataFrame, timeframe: str, market: str, *, step: int | None = None,
                     symbol: str = "", errors: list | None = None) -> list[dict[str, Any]]:
    """First appearance of each triangle pattern_id as breakout_up (long) / breakout_down (short).
    A detector exception at one eval bar = an empty view there (see ihs_signals)."""
    cfg = tri_config(market)
    step = step or default_step(timeframe)
    seen: dict[int, set] = {1: set(), -1: set()}
    out = []
    for t in eval_points(len(df), timeframe, step):
        try:
            rows = detect_triangles(window(df, t, timeframe), timeframe, cfg, symbol=symbol)
        except Exception as exc:
            if errors is not None:
                errors.append((t, f"{type(exc).__name__}: {exc}"))
            continue
        for r in rows:
            d = TRI_DIRS.get(r["status"])
            if d is None or r["pattern_id"] in seen[d]:
                continue
            seen[d].add(r["pattern_id"])
            out.append({"family": "tri", "direction": d, "t": t, **{k: _plain(r.get(k)) for k in TRI_KEEP}})
    return out


# ------------------------------------------------------------------ exits and costs

@dataclass(frozen=True)
class Bars:
    open: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray

    @classmethod
    def of(cls, df: pd.DataFrame) -> "Bars":
        return cls(*(df[c].to_numpy(dtype=float) for c in ("open", "high", "low", "close")))

    @property
    def n(self) -> int:
        return self.close.shape[0]


def _stop_hit(b: Bars, i: int, stop: float, d: int) -> float | None:
    if d > 0:
        return min(b.open[i], stop) if b.low[i] <= stop else None
    return max(b.open[i], stop) if b.high[i] >= stop else None


def _tp_hit(b: Bars, i: int, tp: float, d: int) -> bool:
    return b.high[i] >= tp if d > 0 else b.low[i] <= tp


def simulate_single(b: Bars, t: int, stop: float, tp: float, max_hold: int, d: int = 1) -> tuple[int, float, str]:
    """(exit bar, exit price, reason) with reason stop / tp / timeout / open. Bars t+1 .. t+max_hold only."""
    end = min(t + max_hold, b.n - 1)
    for i in range(t + 1, end + 1):
        px = _stop_hit(b, i, stop, d)
        if px is not None:
            return i, float(px), "stop"
        if _tp_hit(b, i, tp, d):
            return i, float(tp), "tp"
    if t + max_hold <= b.n - 1:
        return t + max_hold, float(b.close[t + max_hold]), "timeout"
    return b.n - 1, float(b.close[b.n - 1]), "open"


def simulate_half(b: Bars, t: int, entry: float, stop: float, tp_a: float, tp_b: float, max_hold: int,
                  d: int = 1) -> list[tuple[int, float, str]]:
    """Two halves: [(bar, price, reason) for the tp_a half, for the tp_b half]. tp_a is the nearer target.
    After tp_a the remaining half's stop is the entry price, active from the next bar."""
    end = min(t + max_hold, b.n - 1)
    legs: list[tuple[int, float, str] | None] = [None, None]
    for i in range(t + 1, end + 1):
        if legs[0] is None:
            px = _stop_hit(b, i, stop, d)
            if px is not None:
                return [(i, float(px), "stop"), (i, float(px), "stop")]
            if _tp_hit(b, i, tp_a, d):
                legs[0] = (i, float(tp_a), "tp")
                if _tp_hit(b, i, tp_b, d):
                    return [legs[0], (i, float(tp_b), "tp")]
            continue
        px = _stop_hit(b, i, entry, d)
        if px is not None:
            return [legs[0], (i, float(px), "breakeven")]
        if _tp_hit(b, i, tp_b, d):
            return [legs[0], (i, float(tp_b), "tp")]
    if t + max_hold <= b.n - 1:
        last, reason = t + max_hold, "timeout"
    else:
        last, reason = b.n - 1, "open"
    fill = (last, float(b.close[last]), reason)
    return [legs[0] or fill, fill]


def net_return(entry: float, exit_px: float, market: str, d: int = 1) -> float:
    """Return after costs. Long: exit*(1-sell) / (entry*(1+buy)) - 1. Short: (entry*(1-sell) - exit*(1+buy)) / entry."""
    c = COSTS[market]
    if d > 0:
        return exit_px * (1.0 - c["sell"]) / (entry * (1.0 + c["buy"])) - 1.0
    return (entry * (1.0 - c["sell"]) - exit_px * (1.0 + c["buy"])) / entry


# ------------------------------------------------------------------ trades

def _strategies(family: str) -> list[dict[str, Any]]:
    if family == "ihs":
        return [{"mode": m, "stop_kind": "rs", "direction": 1, "exit": e} for m in ("A", "B") for e in EXITS]
    return [{"mode": "", "stop_kind": s, "direction": d, "exit": e} for d in (1, -1) for s in TRI_STOPS
            for e in EXITS]


def _levels(sig: Mapping[str, Any], strat: Mapping[str, Any], entry: float, atr_t: float):
    """(stop, tp1, tp2) for a signal, or a skip reason string."""
    d = strat["direction"]
    if sig["family"] == "ihs":
        stop, tp1, tp2 = sig["RS_price"], sig["tp1"], sig["tp2"]
    else:
        tp1, tp2 = sig["target_conservative"], sig["target"]
        if strat["stop_kind"] == "atr":
            if not (atr_t is not None and math.isfinite(atr_t) and atr_t > 0):
                return "no_atr"
            stop = entry - d * ATR_MULT * atr_t
        else:
            stop = sig["invalidation_price"]
    if stop is None or tp1 is None or tp2 is None or not all(math.isfinite(float(x)) for x in (stop, tp1, tp2)):
        return "missing_level"
    stop, tp1, tp2 = float(stop), float(tp1), float(tp2)
    if d * (entry - stop) <= 0:
        return "stop_beyond_entry"
    if stop <= 0:
        return "stop_not_positive"
    return stop, tp1, tp2


def build_trades(df: pd.DataFrame, signals: Sequence[Mapping[str, Any]], timeframe: str, market: str, *,
                 meta: Mapping[str, Any] | None = None, max_hold: int | None = None,
                 atr: np.ndarray | None = None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Simulate every strategy for one series. Returns (trades, skips)."""
    if not signals:
        return [], []
    b = Bars.of(df)
    ts = pd.to_datetime(df["timestamp"], utc=True)
    max_hold = max_hold or MAX_HOLD[timeframe]
    family = signals[0]["family"]
    if atr is None and family == "tri":
        atr = wilder_atr(df, 14)
    meta = dict(meta or {})
    trades, skips = [], []
    for strat in _strategies(family):
        mine = [s for s in signals if (s.get("mode", "") == strat["mode"] if family == "ihs"
                                       else s["direction"] == strat["direction"])]
        last_exit = -1
        for sig in sorted(mine, key=lambda s: (s["t"], s["pattern_id"])):
            t = int(sig["t"])
            base = {**meta, "family": family, "timeframe": timeframe, "mode": strat["mode"],
                    "stop_kind": strat["stop_kind"], "direction": strat["direction"], "exit": strat["exit"],
                    "pattern_id": sig["pattern_id"], "t": t}
            if t <= last_exit:
                skips.append({**base, "reason": "position_open"})
                continue
            entry = float(b.close[t])
            atr_t = float(atr[t]) if atr is not None else None
            lv = _levels(sig, strat, entry, atr_t)
            if isinstance(lv, str):
                skips.append({**base, "reason": lv})
                continue
            stop, tp1, tp2 = lv
            d = strat["direction"]
            if strat["exit"] == "half":
                tp_a, tp_b = (min(tp1, tp2), max(tp1, tp2)) if d > 0 else (max(tp1, tp2), min(tp1, tp2))
                if d * (tp_a - entry) <= 0:
                    skips.append({**base, "reason": "target_reached_at_entry"})
                    continue
                legs = simulate_half(b, t, entry, stop, tp_a, tp_b, max_hold, d)
                rets = [net_return(entry, px, market, d) for _i, px, _r in legs]
                ret = 0.5 * (rets[0] + rets[1])
                gross = 0.5 * sum(d * (px / entry - 1.0) for _i, px, _r in legs)
                exit_i = max(legs[0][0], legs[1][0])
                exit_px = 0.5 * (legs[0][1] + legs[1][1])
                reason = legs[1][2] if legs[0][2] == "tp" else legs[0][2]
                tp_hit = legs[0][2] == "tp"
                extra = {"leg1_reason": legs[0][2], "leg2_reason": legs[1][2], "leg1_bar": legs[0][0],
                         "leg2_bar": legs[1][0], "leg1_price": legs[0][1], "leg2_price": legs[1][1],
                         "tp2_hit": legs[1][2] == "tp", "tp_used": None}
                status_open = legs[1][2] == "open" or legs[0][2] == "open"
            else:
                tp = tp1 if strat["exit"] == "TP1" else tp2
                if d * (tp - entry) <= 0:
                    skips.append({**base, "reason": "target_reached_at_entry"})
                    continue
                exit_i, exit_px, reason = simulate_single(b, t, stop, tp, max_hold, d)
                ret = net_return(entry, exit_px, market, d)
                gross = d * (exit_px / entry - 1.0)
                tp_hit = reason == "tp"
                extra = {"tp_used": tp, "tp2_hit": None}
                status_open = reason == "open"
            risk = d * (entry - stop) / entry
            trades.append({**base, **extra,
                           "entry_time": ts.iloc[t], "entry_price": entry, "stop": stop, "tp1": tp1, "tp2": tp2,
                           "atr": atr_t, "risk_pct": risk, "exit_bar": exit_i, "exit_time": ts.iloc[exit_i],
                           "exit_price": exit_px, "exit_reason": reason, "tp_hit": tp_hit, "is_open": status_open,
                           "bars_held": exit_i - t,
                           "days_held": (ts.iloc[exit_i] - ts.iloc[t]).total_seconds() / 86400.0,
                           "gross_ret": gross, "net_ret": ret, "R": ret / risk if risk > 0 else None,
                           **{f"sig_{k}": v for k, v in sig.items() if k not in ("family", "t", "pattern_id")}})
            last_exit = exit_i
    return trades, skips


# ------------------------------------------------------------------ one symbol (all its timeframes)

def run_symbol(frames: Mapping[str, pd.DataFrame], market: str, *, symbol: str, meta: Mapping[str, Any],
               families: Iterable[str] = ("ihs", "tri"), timeframes: Iterable[str] = TIMEFRAMES,
               steps: Mapping[str, int] | None = None) -> dict[str, Any]:
    """Signals + trades for every requested family x timeframe of one symbol."""
    steps = dict(steps or {})
    families, timeframes = list(families), [tf for tf in TIMEFRAMES if tf in set(timeframes) and tf in frames]
    trades, skips, signals, errors = [], [], [], []
    weekly = None
    if "ihs" in families and "1d" in timeframes and "1w" in frames and len(frames["1w"]):
        weekly = weekly_views(frames["1w"], ihs_config(market), symbol)
    for tf in timeframes:
        df = frames[tf].reset_index(drop=True)
        if len(df) < START[tf] + 1:
            continue
        step = steps.get(tf) or default_step(tf)
        m = {**meta, "symbol": symbol, "market": market}
        for fam in families:
            err: list = []
            if fam == "ihs":
                sig = ihs_signals(df, tf, market, step=step, symbol=symbol, weekly=weekly if tf == "1d" else None,
                                  errors=err)
            else:
                sig = triangle_signals(df, tf, market, step=step, symbol=symbol, errors=err)
            errors.extend({"family": fam, "timeframe": tf, "t": t, "error": e} for t, e in err)
            for s in sig:
                signals.append({**m, "timeframe": tf, **s})
            tr, sk = build_trades(df, sig, tf, market, meta=m)
            trades.extend(tr)
            skips.extend(sk)
    return {"trades": trades, "skips": skips, "signals": signals, "eval_errors": errors}
