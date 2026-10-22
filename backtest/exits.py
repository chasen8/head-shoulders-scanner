"""D43: exit / stop management tested on the D40 IHS A entries. Pure functions only (no network, no file IO);
backtest/exits_run.py does the IO, the per-symbol workers, the aggregation and the Excel workbook.

Entries are the D40 ones (signal bar t, entry close[t], initial stop = RS low, one open position per
series x rule: a signal at t <= the previous trade's last exit bar is skipped, engine.build_trades order).

Exit simulation (long only), bar by bar on t+1 .. t+max_hold, every level known before bar i uses bars <= i-1:
  hard stop   initial stop (RS low); after breakeven activation max(initial, entry). Always intrabar:
              low[i] <= level -> exit at min(open[i], level) (a gap through the stop fills at the open).
  trail       candidate = max(highest close of bars t .. i-1 - k * ATR14[i-1],  lowest low of bars i-N .. i-1);
              trail = running max of the candidates (it only moves up). trigger "low": part of the intrabar stop;
              trigger "close": close[i] < trail -> exit at close[i]. With trail_after_be the trail starts only
              after breakeven activation.
  targets     high[i] >= target -> that leg exits at the target; a bar that also hits the intrabar stop -> stop
              (the stop is checked first: conservative).
  breakeven   close[i] >= entry + be_R * risk activates it from bar i+1 on.
  time stop   at bar t+time_n: no close in t+1 .. t+time_n reached entry + time_R * risk -> exit at close.
  holding cap close[t+max_hold] ("timeout"); data ends first -> "open" (not counted in the statistics).
Per bar order: intrabar stop -> targets -> close trail -> time stop -> state update (trail high, breakeven).

Equity simulation (equity_curve): one account on a daily grid (benchmark trading days). Each trade risks
`risk` of the current equity (position = risk * equity / stop distance %), capped by the idle cash; at most
`max_pos` open positions; idle cash sits in the benchmark (0050 adjclose / BTC) or in 0% cash. Positions are
marked to market with the symbol's daily close (as-of the grid day). Costs: the strategy's per-side costs on
each leg, the benchmark's costs on every transfer between the benchmark and a position.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Sequence

import numpy as np

from backtest import engine as E

LOW_N = {"4h": 10, "1d": 10, "1w": 5}          # lowest-low (chandelier-style) lookback
TIME_N = {"4h": 20, "1d": 20, "1w": 8}         # time stop: bars allowed to reach +TIME_R
TIME_R = 0.5
BE_R = 1.0
LONG_HOLD = {"4h": 360, "1d": 250, "1w": 104}  # relaxed holding cap for the trailing rules


@dataclass(frozen=True)
class Rule:
    key: str
    label: str
    group: str
    engine_exit: str | None = None   # "TP1" / "TP2" / "half": D40 engine functions (baseline reproduction)
    target: str | None = None        # full-position target: "tp1" / "tp2"
    partial: str | None = None       # 50% at "near" (= min(tp1, tp2), the D40 half's first target) or "R2"
    atr_k: float | None = None
    chandelier: bool = False
    trigger: str = "close"
    be_R: float | None = None
    trail_after_be: bool = False
    time_stop: bool = False
    long_hold: bool = False

    def max_hold(self, tf: str) -> int:
        return LONG_HOLD[tf] if self.long_hold else E.MAX_HOLD[tf]


def _rules() -> list[Rule]:
    out = [Rule("TP1", "D40 TP1 出場", "1 基準", engine_exit="TP1"),
           Rule("TP2", "D40 TP2 出場", "1 基準", engine_exit="TP2"),
           Rule("HALF", "D40 半半保本", "1 基準", engine_exit="half")]
    trail = []
    for trig, tl in (("close", "收盤"), ("low", "盤中")):
        for k in (2, 3, 4):
            trail.append(Rule(f"ATR{k}_{trig[0].upper()}", f"ATR×{k} 移動停損（{tl}觸發）", "2 移動停損",
                              atr_k=float(k), trigger=trig))
    for trig, tl in (("close", "收盤"), ("low", "盤中")):
        trail.append(Rule(f"BE_ATR3_{trig[0].upper()}", f"+1R 保本後 ATR×3 移動（{tl}觸發）", "3 保本＋移動",
                          atr_k=3.0, trigger=trig, be_R=BE_R, trail_after_be=True))
    for trig, tl in (("low", "盤中"), ("close", "收盤")):
        trail.append(Rule(f"LOWN_{trig[0].upper()}", f"前 N 根最低價移動（{tl}觸發）", "4 前低移動",
                          chandelier=True, trigger=trig))
    for part, pl in (("near", "較近目標"), ("R2", "+2R")):
        for trig, tl in (("close", "收盤"), ("low", "盤中")):
            trail.append(Rule(f"HALF_{part}_ATR3_{trig[0].upper()}", f"一半在{pl}、一半 ATR×3（{tl}觸發）",
                              "5 半部位", partial=part, atr_k=3.0, trigger=trig))
    trail += [Rule("TIME_ATR3_C", "時間停損＋ATR×3（收盤）", "6 時間停損", atr_k=3.0, trigger="close", time_stop=True),
              Rule("TIME_ATR3_L", "時間停損＋ATR×3（盤中）", "6 時間停損", atr_k=3.0, trigger="low", time_stop=True),
              Rule("TIME_BE_ATR3_C", "時間停損＋保本＋ATR×3（收盤）", "6 時間停損", atr_k=3.0, trigger="close",
                   be_R=BE_R, trail_after_be=True, time_stop=True),
              Rule("TIME_TP2", "時間停損＋TP2", "6 時間停損", target="tp2", time_stop=True)]
    longs = [Rule(r.key + "_LH", r.label + "，持有上限放寬", "7 持有上限放寬", **{
        k: getattr(r, k) for k in ("target", "partial", "atr_k", "chandelier", "trigger", "be_R", "trail_after_be",
                                   "time_stop")}, long_hold=True) for r in trail if r.target is None]
    return out + trail + longs


RULES: list[Rule] = _rules()
RULE_BY_KEY = {r.key: r for r in RULES}


# ------------------------------------------------------------------ exit simulation

Leg = tuple  # (fraction, exit bar, exit price, reason)


def simulate(b: E.Bars, t: int, entry: float, stop0: float, max_hold: int, *,
             atr: np.ndarray | None = None, legs: Sequence[tuple[float, float | None]] = ((1.0, None),),
             atr_k: float | None = None, low_n: int | None = None, trigger: str = "close",
             be_R: float | None = None, trail_after_be: bool = False,
             time_n: int | None = None, time_R: float = TIME_R, trace: list | None = None) -> list[Leg]:
    """Generic long exit (module docstring). `legs` = [(fraction, target or None)]; returns one
    (fraction, bar, price, reason) per leg in the same order. Reasons: stop / breakeven / trail / tp / time /
    timeout / open. `trace` (optional) receives (bar, trail level, intrabar stop level) for every bar checked."""
    if trigger not in ("close", "low"):
        raise ValueError(f"trigger {trigger!r}")
    risk = entry - stop0
    out: list[Leg | None] = [None] * len(legs)
    open_idx = list(range(len(legs)))
    has_trail = atr_k is not None or low_n is not None
    trail = -math.inf
    hhc = float(b.close[t])
    max_close = -math.inf
    be = False
    end = min(t + max_hold, b.n - 1)

    def close_all(i: int, px: float, reason: str) -> list[Leg]:
        for j in open_idx:
            out[j] = (legs[j][0], i, float(px), reason)
        return out  # type: ignore[return-value]

    for i in range(t + 1, end + 1):
        trail_on = has_trail and (be or not trail_after_be)
        if trail_on:
            cand = -math.inf
            if atr_k is not None and atr is not None and math.isfinite(atr[i - 1]):
                cand = hhc - atr_k * float(atr[i - 1])
            if low_n is not None:
                cand = max(cand, float(b.low[max(0, i - low_n):i].min()))
            trail = max(trail, cand)
        hard = max(stop0, entry) if be else stop0
        s_low = max(hard, trail) if (trail_on and trigger == "low") else hard
        if trace is not None:
            trace.append((i, trail, s_low))
        if b.low[i] <= s_low:
            if trail_on and trigger == "low" and trail > hard:
                reason = "trail"
            else:
                reason = "breakeven" if be and entry > stop0 else "stop"
            return close_all(i, min(float(b.open[i]), s_low), reason)
        for j in list(open_idx):
            tgt = legs[j][1]
            if tgt is not None and b.high[i] >= tgt:
                out[j] = (legs[j][0], i, float(tgt), "tp")
                open_idx.remove(j)
        if not open_idx:
            return out  # type: ignore[return-value]
        if trail_on and trigger == "close" and b.close[i] < trail:
            return close_all(i, float(b.close[i]), "trail")
        max_close = max(max_close, float(b.close[i]))
        if time_n is not None and i == t + time_n and max_close < entry + time_R * risk:
            return close_all(i, float(b.close[i]), "time")
        hhc = max(hhc, float(b.close[i]))
        if be_R is not None and not be and b.close[i] >= entry + be_R * risk:
            be = True
    if t + max_hold <= b.n - 1:
        return close_all(t + max_hold, float(b.close[t + max_hold]), "timeout")
    return close_all(b.n - 1, float(b.close[b.n - 1]), "open")


def run_rule(rule: Rule, b: E.Bars, t: int, entry: float, stop0: float, tp1: float, tp2: float,
             atr: np.ndarray | None, tf: str) -> list[Leg] | str:
    """Legs of one trade under `rule`, or a skip reason (a target already reached at the entry, as D40)."""
    mh = rule.max_hold(tf)
    if rule.engine_exit in ("TP1", "TP2"):
        tp = tp1 if rule.engine_exit == "TP1" else tp2
        if tp <= entry:
            return "target_reached_at_entry"
        i, px, reason = E.simulate_single(b, t, stop0, tp, mh, 1)
        return [(1.0, i, px, reason)]
    if rule.engine_exit == "half":
        tp_a, tp_b = min(tp1, tp2), max(tp1, tp2)
        if tp_a <= entry:
            return "target_reached_at_entry"
        (i1, p1, r1), (i2, p2, r2) = E.simulate_half(b, t, entry, stop0, tp_a, tp_b, mh, 1)
        return [(0.5, i1, p1, r1), (0.5, i2, p2, r2)]
    if rule.target is not None:
        tgt = tp1 if rule.target == "tp1" else tp2
        if tgt <= entry:
            return "target_reached_at_entry"
        legs: list[tuple[float, float | None]] = [(1.0, tgt)]
    elif rule.partial is not None:
        tgt = min(tp1, tp2) if rule.partial == "near" else entry + 2.0 * (entry - stop0)
        if tgt <= entry:
            return "target_reached_at_entry"
        legs = [(0.5, tgt), (0.5, None)]
    else:
        legs = [(1.0, None)]
    return simulate(b, t, entry, stop0, mh, atr=atr, legs=legs, atr_k=rule.atr_k,
                    low_n=LOW_N[tf] if rule.chandelier else None, trigger=rule.trigger, be_R=rule.be_R,
                    trail_after_be=rule.trail_after_be, time_n=TIME_N[tf] if rule.time_stop else None)


def legs_return(legs: Sequence[Leg], entry: float, market: str) -> float:
    """Net return of the whole position after costs (fraction-weighted legs)."""
    return float(sum(f * E.net_return(entry, px, market, 1) for f, _i, px, _r in legs))


def final_reason(legs: Sequence[Leg]) -> str:
    """'open' if any leg is still open, else the reason of the last leg to close (ties: the later leg)."""
    if any(r == "open" for _f, _i, _p, r in legs):
        return "open"
    return max(enumerate(legs), key=lambda kv: (kv[1][1], kv[0]))[1][3]


def series_trades(b: E.Bars, signals: Sequence[dict[str, Any]], rule: Rule, tf: str, market: str,
                  atr: np.ndarray | None) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """One series, one rule: engine.build_trades' one-open-position loop. Signals need t, pattern_id,
    RS_price, tp1, tp2. Returns (trades, skip counts)."""
    trades, skips = [], {}
    last_exit = -1
    for sig in sorted(signals, key=lambda s: (int(s["t"]), str(s["pattern_id"]))):
        t = int(sig["t"])
        if t <= last_exit:
            skips["position_open"] = skips.get("position_open", 0) + 1
            continue
        entry = float(b.close[t])
        try:
            stop0, tp1, tp2 = float(sig["RS_price"]), float(sig["tp1"]), float(sig["tp2"])
        except (TypeError, ValueError):
            stop0 = tp1 = tp2 = float("nan")
        if not all(math.isfinite(x) for x in (stop0, tp1, tp2)):
            skips["missing_level"] = skips.get("missing_level", 0) + 1
            continue
        if entry - stop0 <= 0 or stop0 <= 0:
            skips["stop_beyond_entry"] = skips.get("stop_beyond_entry", 0) + 1
            continue
        legs = run_rule(rule, b, t, entry, stop0, tp1, tp2, atr, tf)
        if isinstance(legs, str):
            skips[legs] = skips.get(legs, 0) + 1
            continue
        risk = (entry - stop0) / entry
        ret = legs_return(legs, entry, market)
        exit_i = max(i for _f, i, _p, _r in legs)
        trades.append({"t": t, "pattern_id": sig["pattern_id"], "entry_price": entry, "stop": stop0, "tp1": tp1,
                       "tp2": tp2, "risk_pct": risk, "exit_bar": exit_i, "bars_held": exit_i - t,
                       "net_ret": ret, "R": ret / risk, "exit_reason": final_reason(legs),
                       "is_open": any(r == "open" for _f, _i, _p, r in legs), "legs": [tuple(x) for x in legs]})
        last_exit = exit_i
    return trades, skips


# ------------------------------------------------------------------ statistics helpers

def max_losing_streak(rets: Sequence[float]) -> int:
    best = cur = 0
    for r in rets:
        cur = cur + 1 if r < 0 else 0
        best = max(best, cur)
    return best


def rr_stats(R: Sequence[float]) -> dict[str, float | None]:
    """Mean win R, mean loss R (as a positive number) and their ratio (the realised reward:risk)."""
    a = np.asarray(R, dtype=float)
    a = a[np.isfinite(a)]
    w, lo = a[a > 0], a[a < 0]
    aw = float(w.mean()) if w.size else None
    al = float(-lo.mean()) if lo.size else None
    return {"avg_win_R": aw, "avg_loss_R": al, "rr": (aw / al) if (aw is not None and al) else None}


# ------------------------------------------------------------------ equity simulation

@dataclass
class SimTrade:
    sym: int                 # row of the price matrix
    d0: int                  # grid day of the entry (entry at that day's close)
    entry: float
    risk_pct: float
    legs: list               # [(fraction, exit grid day or -1 when still open at the end, exit price)]
    key: tuple = field(default=())  # ordering key among same-day entries


def equity_curve(px: np.ndarray, trades: Sequence[SimTrade], *, bench: np.ndarray | None, risk: float = 0.01,
                 max_pos: int = 10, costs: dict[str, float], bench_costs: dict[str, float] | None = None,
                 start: int = 0, order: np.ndarray | None = None) -> dict[str, Any]:
    """Daily account value (start = 1.0, fully in the benchmark or in cash) on grid days start .. n-1.
    px[sym, d] = the symbol's close as of grid day d. bench[d] = benchmark total-return value (None = 0% cash).
    Same-day entries are taken in `order` (a permutation over `trades`; default: sorted by key)."""
    n = px.shape[1]
    bc = bench_costs or {"buy": 0.0, "sell": 0.0}
    if bench is None:
        bc = {"buy": 0.0, "sell": 0.0}
    idx = list(range(len(trades))) if order is None else list(order)
    if order is None:
        idx.sort(key=lambda k: (trades[k].d0, trades[k].key))
    else:
        rank = {k: r for r, k in enumerate(idx)}
        idx.sort(key=lambda k: (trades[k].d0, rank[k]))
    by_day: dict[int, list[int]] = {}
    for k in idx:
        if start <= trades[k].d0 < n:
            by_day.setdefault(trades[k].d0, []).append(k)
    sleeve = 1.0 - bc["buy"]  # money in the benchmark (or in cash); the initial benchmark purchase pays its cost
    open_pos: dict[int, list] = {}   # trade index -> [shares per leg, leg open flags]
    eq = np.full(n, np.nan)
    expo = np.zeros(n)
    taken = scaled = full = 0
    taken_idx: list[tuple[int, float]] = []  # (trade index, position size as a fraction of equity at entry)
    buy, sell = costs["buy"], costs["sell"]
    for d in range(start, n):
        if bench is not None and d > start and math.isfinite(bench[d]) and math.isfinite(bench[d - 1]):
            sleeve *= bench[d] / bench[d - 1]
        # exits at this day's close
        for k in list(open_pos):
            sh, alive = open_pos[k]
            for j, (_f, dx, xpx) in enumerate(trades[k].legs):
                if alive[j] and dx == d:
                    sleeve += sh[j] * xpx * (1.0 - sell) * (1.0 - bc["buy"])
                    alive[j] = False
            if not any(alive):
                del open_pos[k]

        def mtm() -> float:
            v = 0.0
            for kk, (sh, alive) in open_pos.items():
                p = px[trades[kk].sym, d]
                if not math.isfinite(p):
                    p = trades[kk].entry
                v += sum(s * p for s, a in zip(sh, alive) if a)
            return v
        pos_val = mtm()
        equity = sleeve + pos_val
        for k in by_day.get(d, []):
            if len(open_pos) >= max_pos:
                full += 1
                continue
            tr = trades[k]
            want = risk * equity / tr.risk_pct if tr.risk_pct > 0 else 0.0
            avail = sleeve * (1.0 - bc["sell"])
            size = min(want, avail)
            if size <= 1e-12:
                full += 1
                continue
            if size < want:
                scaled += 1
            sleeve -= size / (1.0 - bc["sell"])
            shares = size / (tr.entry * (1.0 + buy))
            sh = [shares * f for f, _dx, _p in tr.legs]
            alive = [True] * len(tr.legs)
            for j, (_f, dx, xpx) in enumerate(tr.legs):  # a leg exiting on the entry day itself (not expected)
                if dx == d:
                    sleeve += sh[j] * xpx * (1.0 - sell) * (1.0 - bc["buy"])
                    alive[j] = False
            if any(alive):
                open_pos[k] = [sh, alive]
            taken += 1
            taken_idx.append((k, size / equity if equity > 0 else 0.0))
        pos_val = mtm()
        eq[d] = sleeve + pos_val
        expo[d] = pos_val / eq[d] if eq[d] > 0 else 0.0
    return {"equity": eq, "exposure": expo, "taken": taken, "scaled": scaled, "skipped_full": full,
            "taken_idx": taken_idx}


def perf(equity: np.ndarray, days_ns: np.ndarray, lo: int = 0, hi: int | None = None) -> dict[str, float | None]:
    """CAGR, max drawdown, Sharpe (rf 0, annualised by the grid's own days per year) on equity[lo:hi+1]."""
    hi = len(equity) - 1 if hi is None else hi
    e = np.asarray(equity[lo:hi + 1], dtype=float)
    ok = np.isfinite(e)
    e, tns = e[ok], np.asarray(days_ns[lo:hi + 1])[ok]
    if e.size < 2 or e[0] <= 0:
        return {"cagr": None, "mdd": None, "sharpe": None, "total": None, "years": None}
    years = (tns[-1] - tns[0]) / 1e9 / 86400 / 365.25
    total = e[-1] / e[0] - 1.0
    cagr = (e[-1] / e[0]) ** (1.0 / years) - 1.0 if years > 0 else None
    peak = np.maximum.accumulate(e)
    mdd = float((e / peak - 1.0).min())
    r = e[1:] / e[:-1] - 1.0
    per_year = r.size / years if years > 0 else None
    sd = float(r.std(ddof=1)) if r.size > 1 else 0.0
    sharpe = float(r.mean()) / sd * math.sqrt(per_year) if sd > 0 and per_year else None
    return {"cagr": cagr, "mdd": mdd, "sharpe": sharpe, "total": total, "years": years}
