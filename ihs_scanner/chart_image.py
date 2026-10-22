"""PNG chart of one detected pattern for the Discord notification (D32).

Draws ONLY values already stored in the bars file written by build_site.py (bars, points, neckline, levels,
breakout, rs_provisional) -- nothing is re-detected (D17). Style follows the light site theme
(docs/site-design.md): white background, hollow-up / solid-down black candles, blue five points and neckline,
green TP lines, grey invalidation line, red breakout dot.

All text inside the image is ASCII (the GitHub ubuntu runner has no CJK font); the Chinese description lives
in the embed. The x axis is the bar index (no weekend / overnight gaps, like TradingView).
"""
from __future__ import annotations

import io
import math
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib.gridspec import GridSpec  # noqa: E402

WIDTH, HEIGHT, DPI = 1200, 675, 100
BG, TEXT, MUTED, FAINT = "#FFFFFF", "#131722", "#6A6D78", "#787B86"
GRID, LINE = "#F0F3FA", "#E0E3EB"
ACCENT, ACCENT_SOFT = "#2962FF", "#9DB6FF"
UP, TP2, DOWN, STOP = "#089981", "#22AB94", "#F23645", "#787B86"
STATUS_EN = {"forming": "forming", "watchlist": "watchlist", "confirmed": "breakout", "stale": "stale"}
MAX_BARS_SHOWN = 160


def _decimals(x: float) -> int:
    a = abs(x)
    if a >= 1000:
        return 2
    if a >= 1:
        return 3
    if a >= 0.01:
        return 4
    if a == 0:
        return 2
    return min(12, math.ceil(-math.log10(a)) + 3)


def _is_tw(row: Mapping[str, Any]) -> bool:
    return row.get("market") == "stock" or row.get("exchange") in ("twse", "tpex")


def chart_title(row: Mapping[str, Any]) -> str:
    """ASCII title, e.g. "STARUSDT  4h  forming  score 83.0" / "2330  1d  forming  score 71.2"."""
    status = STATUS_EN.get(str(row.get("status")), str(row.get("status")))
    score = row.get("pattern_score")
    tail = f"  score {float(score):.1f}" if score is not None else ""
    return f"{row.get('symbol', '?')}  {row.get('timeframe', '?')}  {status}{tail}"


def _fmt_ticks(times: np.ndarray, tf: str, tz: timezone) -> list[str]:
    fmt = "%Y-%m-%d" if tf in ("1d", "1w") else "%m-%d %H:%M"
    return [datetime.fromtimestamp(float(t), tz).strftime(fmt) for t in times]


def _spread(ys: list[float], lo: float, hi: float, gap: float) -> list[float]:
    """Move label y positions apart so neighbours are >= gap apart (keeps order, stays inside [lo, hi])."""
    order = sorted(range(len(ys)), key=lambda i: ys[i])
    out = list(ys)
    for a, b in zip(order, order[1:]):
        if out[b] - out[a] < gap:
            out[b] = out[a] + gap
    if order and out[order[-1]] > hi:  # shift the stack back down
        shift = out[order[-1]] - hi
        out = [y - shift for y in out]
    return [max(lo, y) for y in out]


def _draw_marks(ax, doc: Mapping[str, Any], t2x) -> None:
    """D39: optional doc["marks"] = [{time, price, label}] (e.g. the bar where TP1 was reached): green star + label."""
    for m in doc.get("marks") or []:
        try:
            mx, my = t2x(float(m["time"])), float(m["price"])
        except (KeyError, TypeError, ValueError):
            continue
        ax.plot([mx], [my], marker="*", markersize=15, markerfacecolor=UP, markeredgecolor=BG,
                markeredgewidth=1.0, zorder=10)
        ax.annotate(str(m.get("label") or ""), (mx, my), xytext=(0, 12), textcoords="offset points", ha="center",
                    va="bottom", fontsize=10, color=UP, fontweight="bold", zorder=10)


def render_chart(row: Mapping[str, Any], doc: Mapping[str, Any]) -> bytes:
    """PNG bytes (1200x675). Raises ValueError when the bars file has no usable bars/points."""
    bars = np.asarray(doc.get("bars") or [], dtype=float)
    points = list(doc.get("points") or [])
    if bars.ndim != 2 or bars.shape[0] < 5 or bars.shape[1] < 5 or len(points) < 3:
        raise ValueError("bars file has no usable bars / points")
    times, o, h, low, c = bars[:, 0], bars[:, 1], bars[:, 2], bars[:, 3], bars[:, 4]
    vol = bars[:, 5] if bars.shape[1] > 5 else np.zeros(len(bars))
    n = len(bars)
    last = n - 1
    step = float(np.median(np.diff(times))) if n > 1 else 86400.0
    tf = str(row.get("timeframe") or doc.get("timeframe") or "1d")
    tz = timezone(timedelta(hours=8)) if _is_tw(row) else timezone.utc

    def t2x(t: float) -> float:
        if t >= times[-1]:
            return last + (t - times[-1]) / step
        return float(np.interp(t, times, np.arange(n)))

    pts = [(p, t2x(float(p["time"])), float(p["price"])) for p in points]
    x_first = min(x for _, x, _ in pts)
    span = max(last - x_first, 10.0)
    start = int(max(0, math.floor(x_first - max(6.0, 0.12 * span))))
    start = max(start, last - MAX_BARS_SHOWN)
    start = max(0, min(start, int(math.floor(x_first)) - 3))
    pad = max(8.0, (last - start) * 0.15)  # right margin for the price labels
    x_left, x_right = start - max(2.0, 0.035 * (last - start)), last + pad

    # ---- y range from what is drawn
    win = slice(start, n)
    ymin = float(min(low[win].min(), min(p for _, _, p in pts)))
    ymax = float(max(h[win].max(), max(p for _, _, p in pts)))
    levels = [lv for lv in (doc.get("levels") or []) if lv.get("price") is not None]
    inval = next((float(lv["price"]) for lv in levels if lv.get("key") == "invalidation_price"), None)
    if inval is not None:
        ymin = min(ymin, inval)
    base_span = ymax - ymin
    clipped: dict[str, int] = {}
    for lv in levels:
        p = float(lv["price"])
        if lv.get("key") == "invalidation_price":
            continue
        if p <= ymax + 0.9 * base_span:  # far-away targets would squash the candles: label at the edge instead
            ymax = max(ymax, p)
        else:
            clipped[lv["key"]] = 1
    rng = max(ymax - ymin, abs(ymax) * 1e-6, 1e-12)
    y_lo, y_hi = ymin - 0.12 * rng, ymax + 0.09 * rng
    rng = y_hi - y_lo

    fig = plt.figure(figsize=(WIDTH / DPI, HEIGHT / DPI), dpi=DPI, facecolor=BG)
    gs = GridSpec(2, 1, height_ratios=[5, 1], hspace=0.04, left=0.012, right=0.915, top=0.90, bottom=0.075,
                  figure=fig)
    ax = fig.add_subplot(gs[0])
    axv = fig.add_subplot(gs[1], sharex=ax)
    try:
        for a in (ax, axv):
            a.set_facecolor(BG)
            a.grid(True, color=GRID, linewidth=0.8, zorder=0)
            a.set_axisbelow(True)
            for side in ("top", "left", "right", "bottom"):
                a.spines[side].set_color(LINE)
            a.tick_params(colors=MUTED, labelsize=9, length=3, color=LINE)
        ax.yaxis.tick_right()
        axv.yaxis.tick_right()
        axv.set_yticks([])

        # ---- candles (x = bar index)
        xs = np.arange(start, n)
        sl = slice(start, n)
        body_lo, body_hi = np.minimum(o[sl], c[sl]), np.maximum(o[sl], c[sl])
        min_body = rng * 0.0015
        height = np.maximum(body_hi - body_lo, min_body)
        up = c[sl] >= o[sl]
        ax.vlines(xs, low[sl], h[sl], colors=TEXT, linewidth=0.9, zorder=2)
        ax.bar(xs, height, bottom=body_lo, width=0.62, color=np.where(up, BG, TEXT), edgecolor=TEXT,
               linewidth=0.9, zorder=3)
        vcol = np.where(up, UP, DOWN)
        axv.bar(xs, vol[sl], width=0.7, color=vcol, alpha=0.35, zorder=2)

        # ---- pattern polyline (soft blue); provisional last leg dashed
        prov = bool(doc.get("rs_provisional"))
        px = [x for _, x, _ in pts]
        py = [p for _, _, p in pts]
        if prov and len(pts) >= 2:
            ax.plot(px[:-1], py[:-1], color=ACCENT_SOFT, linewidth=1.8, zorder=4)
            ax.plot(px[-2:], py[-2:], color=ACCENT_SOFT, linewidth=1.5, linestyle=(0, (3, 3)), zorder=4)
        else:
            ax.plot(px, py, color=ACCENT_SOFT, linewidth=1.8, zorder=4)

        # ---- neckline (dashed blue) from its first point to the last bar
        neck = doc.get("neckline") or []
        if len(neck) >= 2:
            x0, y0 = t2x(float(neck[0]["time"])), float(neck[0]["price"])
            x1, y1 = t2x(float(neck[1]["time"])), float(neck[1]["price"])
            if x1 != x0:
                slope = (y1 - y0) / (x1 - x0)
                x_end = last + 0.5
                ax.plot([x0, x_end], [y0, y0 + slope * (x_end - x0)], color=ACCENT, linewidth=1.6,
                        linestyle=(0, (5, 3)), zorder=5)

        # ---- five points + labels
        for p, x, y in pts:
            label = str(p.get("label"))
            is_prov = bool(p.get("provisional")) or (prov and label == "RS")
            low_point = label in ("LS", "H", "RS")
            if is_prov:
                ax.plot([x], [y], marker="o", markersize=9, markerfacecolor=BG, markeredgecolor=ACCENT_SOFT,
                        markeredgewidth=2.0, zorder=7)
                label = "RS?"
            else:
                ax.plot([x], [y], marker="o", markersize=8, markerfacecolor=ACCENT, markeredgecolor=BG,
                        markeredgewidth=1.2, zorder=7)
            ax.annotate(label, (x, y), xytext=(0, -15 if low_point else 11), textcoords="offset points",
                        ha="center", va="top" if low_point else "bottom", fontsize=11, fontweight="bold",
                        color=ACCENT_SOFT if is_prov else ACCENT, zorder=8)

        # ---- breakout dot
        bo = doc.get("breakout")
        if bo and bo.get("time") is not None:
            bx, by = t2x(float(bo["time"])), float(bo["price"])
            ax.plot([bx], [by], marker="o", markersize=8, markerfacecolor=DOWN, markeredgecolor=BG,
                    markeredgewidth=1.2, zorder=9)
            ax.annotate("breakout", (bx, by), xytext=(0, 11), textcoords="offset points", ha="center",
                        va="bottom", fontsize=9, color=DOWN, fontweight="bold", zorder=9)
        _draw_marks(ax, doc, t2x)

        # ---- levels: TP1 / TP2 green, invalidation grey; values on the right
        dec = _decimals(float(np.median(c[sl])))
        est = bool(doc.get("levels_estimated"))
        style = {"tp1": (UP, (0, (4, 3)), "TP1"), "tp2": (TP2, (0, (7, 3)), "TP2"),
                 "invalidation_price": (STOP, (0, (2, 3)), "INVAL")}
        entries = []
        for lv in levels:
            key = lv.get("key")
            if key not in style:
                continue
            color, ls, name = style[key]
            p = float(lv["price"])
            if key in clipped:
                entries.append((y_hi - 0.02 * rng, f"{name} {p:.{dec}f} ↑", color))
                continue
            ax.hlines(p, start, last + 0.8, colors=color, linestyles=ls, linewidth=1.4, zorder=2)
            suffix = "*" if (est and key == "tp2") else ""
            entries.append((p, f"{name} {p:.{dec}f}{suffix}", color))
        gap = 0.05 * rng
        ys = _spread([e[0] for e in entries], y_lo + 0.02 * rng, y_hi - 0.02 * rng, gap)
        for (_, text, color), y in zip(entries, ys):
            ax.text(last + 1.1, y, text, color=color, fontsize=10, fontweight="bold", va="center", ha="left",
                    zorder=8, clip_on=False)

        ax.set_xlim(x_left, x_right)
        ax.set_ylim(y_lo, y_hi)
        ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _p: f"{v:.{dec}f}"))
        plt.setp(ax.get_xticklabels(), visible=False)
        tick_idx = np.unique(np.linspace(start + 0.07 * (last - start), last - 0.02 * (last - start), 6).round().astype(int))
        axv.set_xticks(tick_idx)
        axv.set_xticklabels(_fmt_ticks(times[tick_idx], tf, tz))

        # ---- header
        fig.text(0.014, 0.955, chart_title(row), fontsize=16, fontweight="bold", color=TEXT, va="center")
        ex = f"{row.get('exchange', '')} {row.get('market', '')}".strip()
        note = "  *estimated (neckline)" if est else ""
        fig.text(0.986, 0.955, f"{ex}{note}", fontsize=10, color=MUTED, va="center", ha="right")

        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=DPI, facecolor=BG)
        return buf.getvalue()
    finally:
        plt.close(fig)


TRI_LEVEL_STYLE = {"target_conservative": (UP, (0, (4, 3)), "TP1"), "target": (TP2, (0, (7, 3)), "TP2"),
                   "invalidation_price": (STOP, (0, (2, 3)), "INVAL")}


def render_triangle_chart(row: Mapping[str, Any], doc: Mapping[str, Any]) -> bytes:
    """D39: PNG (1200x675) of one converging triangle from its tri_bars file (bars, resistance / support lines and
    their dashed extensions, touches, breakout, TP1 = conservative target, TP2 = target, invalidation, optional
    marks). Same style and ASCII-only text as render_chart. Raises ValueError without usable bars / lines."""
    bars = np.asarray(doc.get("bars") or [], dtype=float)
    res = list(doc.get("resistance_line") or [])
    sup = list(doc.get("support_line") or [])
    if bars.ndim != 2 or bars.shape[0] < 5 or bars.shape[1] < 5 or len(res) < 2 or len(sup) < 2:
        raise ValueError("tri_bars file has no usable bars / lines")
    times, o, h, low, c = bars[:, 0], bars[:, 1], bars[:, 2], bars[:, 3], bars[:, 4]
    vol = bars[:, 5] if bars.shape[1] > 5 else np.zeros(len(bars))
    n = len(bars)
    last = n - 1
    step = float(np.median(np.diff(times))) if n > 1 else 86400.0
    tf = str(row.get("timeframe") or doc.get("timeframe") or "1d")
    tz = timezone(timedelta(hours=8)) if _is_tw(row) else timezone.utc

    def t2x(t: float) -> float:
        if t >= times[-1]:
            return last + (t - times[-1]) / step
        return float(np.interp(t, times, np.arange(n)))

    x_first = t2x(float(res[0]["time"]))
    span = max(last - x_first, 10.0)
    start = int(max(0, math.floor(x_first - max(6.0, 0.12 * span))))
    start = max(start, last - MAX_BARS_SHOWN)
    start = max(0, min(start, int(math.floor(x_first)) - 3))
    ext = list(doc.get("resistance_ext") or []) + list(doc.get("support_ext") or [])
    x_ext = max([t2x(float(p["time"])) for p in ext], default=float(last))
    x_lines_end = min(max(last + 0.5, x_ext), last + max(8.0, (last - start) * 0.25))
    pad = max(12.0, (last - start) * 0.24)  # room for "INVAL 1049.01" right of the line extensions
    x_left, x_right = start - max(2.0, 0.035 * (last - start)), max(float(last), x_lines_end) + pad

    win = slice(start, n)
    line_prices = [float(p["price"]) for p in res + sup]
    ymin = float(min(low[win].min(), min(line_prices)))
    ymax = float(max(h[win].max(), max(line_prices)))
    levels = [lv for lv in (doc.get("levels") or []) if lv.get("price") is not None]
    base_span = ymax - ymin
    clipped: set[str] = set()
    for lv in levels:
        p = float(lv["price"])
        if lv.get("key") == "invalidation_price":
            ymin = min(ymin, p)
        elif p <= ymax + 0.9 * base_span:
            ymax = max(ymax, p)
        else:
            clipped.add(str(lv.get("key")))
    rng = max(ymax - ymin, abs(ymax) * 1e-6, 1e-12)
    y_lo, y_hi = ymin - 0.10 * rng, ymax + 0.09 * rng
    rng = y_hi - y_lo

    fig = plt.figure(figsize=(WIDTH / DPI, HEIGHT / DPI), dpi=DPI, facecolor=BG)
    gs = GridSpec(2, 1, height_ratios=[5, 1], hspace=0.04, left=0.012, right=0.915, top=0.90, bottom=0.075,
                  figure=fig)
    ax = fig.add_subplot(gs[0])
    axv = fig.add_subplot(gs[1], sharex=ax)
    try:
        for a in (ax, axv):
            a.set_facecolor(BG)
            a.grid(True, color=GRID, linewidth=0.8, zorder=0)
            a.set_axisbelow(True)
            for side in ("top", "left", "right", "bottom"):
                a.spines[side].set_color(LINE)
            a.tick_params(colors=MUTED, labelsize=9, length=3, color=LINE)
        ax.yaxis.tick_right()
        axv.yaxis.tick_right()
        axv.set_yticks([])

        xs = np.arange(start, n)
        sl = slice(start, n)
        body_lo, body_hi = np.minimum(o[sl], c[sl]), np.maximum(o[sl], c[sl])
        height = np.maximum(body_hi - body_lo, rng * 0.0015)
        up = c[sl] >= o[sl]
        ax.vlines(xs, low[sl], h[sl], colors=TEXT, linewidth=0.9, zorder=2)
        ax.bar(xs, height, bottom=body_lo, width=0.62, color=np.where(up, BG, TEXT), edgecolor=TEXT,
               linewidth=0.9, zorder=3)
        axv.bar(xs, vol[sl], width=0.7, color=np.where(up, UP, DOWN), alpha=0.35, zorder=2)

        for side in ("resistance", "support"):
            pts = doc.get(f"{side}_line") or []
            ax.plot([t2x(float(p["time"])) for p in pts], [float(p["price"]) for p in pts], color=ACCENT,
                    linewidth=1.8, zorder=5)
            e = [p for p in doc.get(f"{side}_ext") or [] if t2x(float(p["time"])) <= x_lines_end]
            if e:
                ax.plot([t2x(float(p["time"])) for p in e], [float(p["price"]) for p in e], color=ACCENT_SOFT,
                        linewidth=1.5, linestyle=(0, (5, 3)), zorder=5)
        for t in doc.get("touches") or []:
            ax.plot([t2x(float(t["time"]))], [float(t["price"])], marker="o", markersize=6, markerfacecolor=ACCENT,
                    markeredgecolor=BG, markeredgewidth=1.0, zorder=7)

        bo = doc.get("breakout")
        if bo and bo.get("time") is not None:
            bx, by = t2x(float(bo["time"])), float(bo["price"])
            ax.plot([bx], [by], marker="o", markersize=8, markerfacecolor=DOWN, markeredgecolor=BG,
                    markeredgewidth=1.2, zorder=9)
            ax.annotate("breakout", (bx, by), xytext=(0, 11), textcoords="offset points", ha="center",
                        va="bottom", fontsize=9, color=DOWN, fontweight="bold", zorder=9)
        _draw_marks(ax, doc, t2x)

        dec = _decimals(float(np.median(c[sl])))
        entries = []
        for lv in levels:
            key = str(lv.get("key"))
            if key not in TRI_LEVEL_STYLE:
                continue
            color, ls, name = TRI_LEVEL_STYLE[key]
            p = float(lv["price"])
            if key in clipped:
                entries.append((y_hi - 0.02 * rng, f"{name} {p:.{dec}f} ^", color))
                continue
            ax.hlines(p, start, last + 0.8, colors=color, linestyles=ls, linewidth=1.4, zorder=2)
            entries.append((p, f"{name} {p:.{dec}f}", color))
        ys = _spread([e[0] for e in entries], y_lo + 0.02 * rng, y_hi - 0.02 * rng, 0.05 * rng)
        for (_, text, color), y in zip(entries, ys):
            ax.text(x_right - 0.4, y, text, color=color, fontsize=10, fontweight="bold", va="center", ha="right",
                    zorder=8, clip_on=False)

        ax.set_xlim(x_left, x_right)
        ax.set_ylim(y_lo, y_hi)
        ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _p: f"{v:.{dec}f}"))
        plt.setp(ax.get_xticklabels(), visible=False)
        tick_idx = np.unique(np.linspace(start + 0.07 * (last - start), last - 0.02 * (last - start), 6)
                             .round().astype(int))
        axv.set_xticks(tick_idx)
        axv.set_xticklabels(_fmt_ticks(times[tick_idx], tf, tz))

        ttype = str(row.get("triangle_type") or doc.get("triangle_type") or "")
        status = str(row.get("status") or "")
        fig.text(0.014, 0.955, f"{row.get('symbol', '?')}  {tf}  {ttype} triangle  {status}".replace("   ", " "),
                 fontsize=16, fontweight="bold", color=TEXT, va="center")
        ex = f"{row.get('exchange', '')} {row.get('market', '')}".strip()
        fig.text(0.986, 0.955, ex, fontsize=10, color=MUTED, va="center", ha="right")

        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=DPI, facecolor=BG)
        return buf.getvalue()
    finally:
        plt.close(fig)
