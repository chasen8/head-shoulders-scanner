"""PNG table of the newly confirmed right shoulders (status "watchlist") for one scan (D34).

One image holds up to MAX_ROWS_PER_IMAGE rows; more rows are split into several images by the caller. Light theme
like chart_image.py (white background, zebra rows). All text is ASCII unless a CJK font is installed (Taiwan stock
names are then shown next to the code); the runner usually has none, so the Chinese name also lives in the Discord
select labels.
"""
from __future__ import annotations

import io
import math
from typing import Any, Mapping, Sequence

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib import font_manager  # noqa: E402

MAX_ROWS_PER_IMAGE = 25
BG, TEXT, MUTED, ZEBRA, HEAD_BG, LINE = "#FFFFFF", "#131722", "#6A6D78", "#F5F7FB", "#E8EDF8", "#D5DAE6"
POS, NEG = "#089981", "#F23645"
CJK_FONTS = ("Microsoft JhengHei", "Microsoft YaHei", "Noto Sans CJK TC", "Noto Sans CJK JP", "Noto Sans CJK SC",
             "PingFang TC", "WenQuanYi Zen Hei", "WenQuanYi Micro Hei", "Droid Sans Fallback", "SimHei")
HEADERS = ["#", "Symbol", "TF", "Score", "Price", "To neckline", "Neckline N1>N2", "TP1", "TP2"]
WIDTHS = [0.045, 0.215, 0.05, 0.075, 0.12, 0.11, 0.115, 0.135, 0.135]  # sums to 1.0
WIDTH_PX, ROW_PX, HEAD_PX, TITLE_PX, DPI = 1200, 34, 40, 52, 100


def cjk_font() -> str | None:
    """Name of an installed CJK font, or None (the Chinese name is then left out of the picture)."""
    installed = {f.name for f in font_manager.fontManager.ttflist}
    for name in CJK_FONTS:
        if name in installed:
            return name
    return None


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


def _price(x: Any, ref: Any = None, *, tw: bool = False) -> str:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return "-"
    if not math.isfinite(v):
        return "-"
    if tw:
        return f"{v:,.2f}"
    r = float(ref) if ref not in (None, 0) else v
    return f"{v:,.{_decimals(r)}f}"


def _signed_pct(x: Any, digits: int = 1) -> str:
    try:
        v = float(x) * 100
    except (TypeError, ValueError):
        return "-"
    return f"{v:+.{digits}f}%" if math.isfinite(v) else "-"


def table_cells(index: int, row: Mapping[str, Any], *, tw: bool, with_name: bool) -> list[str]:
    """One table row as strings. "To neckline" is signed: negative = still below the neckline."""
    sym = str(row.get("symbol", "?"))
    if tw and with_name and row.get("name"):
        sym = f"{sym} {row['name']}"
    dist = row.get("distance_to_neckline")
    if dist is not None:
        try:
            dist = float(dist) * (1.0 if row.get("neckline_side") == "above" else -1.0)
        except (TypeError, ValueError):
            dist = None
    close = row.get("current_close")
    return [str(index), sym, str(row.get("timeframe", "")), f"{float(row.get('pattern_score') or 0):.1f}",
            _price(close, tw=tw), _signed_pct(dist), _signed_pct(row.get("neckline_change_pct"), 1),  # D27 total neckline change, same as the site filter
            _price(row.get("tp1"), close, tw=tw), _price(row.get("tp2"), close, tw=tw)]


def render_table(items: Sequence[tuple[int, Mapping[str, Any]]], title: str, *, tw: bool = False,
                 font: str | None = None) -> bytes:
    """PNG bytes for `items` = [(running number, scan row), ...] (<= MAX_ROWS_PER_IMAGE). `title` should be ASCII
    unless `font` (a CJK font name) is given. Raises ValueError for no rows."""
    if not items:
        raise ValueError("no rows")
    n = len(items)
    height = TITLE_PX + HEAD_PX + ROW_PX * n + 14
    fig = plt.figure(figsize=(WIDTH_PX / DPI, height / DPI), dpi=DPI, facecolor=BG)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, 1)
    ax.set_ylim(height, 0)
    ax.axis("off")
    kw = {"fontfamily": font} if font else {}
    ax.text(0.015, TITLE_PX / 2, title, fontsize=15, fontweight="bold", color=TEXT, va="center", **kw)
    xs = [sum(WIDTHS[:i]) for i in range(len(WIDTHS))]
    right_cols = {3, 4, 5, 6, 7, 8}  # numeric columns: right aligned
    ax.add_patch(plt.Rectangle((0, TITLE_PX), 1, HEAD_PX, color=HEAD_BG, lw=0))
    for i, h in enumerate(HEADERS):
        x = xs[i] + (WIDTHS[i] - 0.012 if i in right_cols else 0.008)
        ax.text(x, TITLE_PX + HEAD_PX / 2, h, fontsize=11, fontweight="bold", color=MUTED, va="center",
                ha="right" if i in right_cols else "left")
    y0 = TITLE_PX + HEAD_PX
    for r, (num, row) in enumerate(items):
        top = y0 + r * ROW_PX
        if r % 2:
            ax.add_patch(plt.Rectangle((0, top), 1, ROW_PX, color=ZEBRA, lw=0))
        cells = table_cells(num, row, tw=tw, with_name=bool(font))
        for i, c in enumerate(cells):
            color = TEXT
            if i == 5 and c != "-":
                color = POS if c.startswith("+") else NEG
            x = xs[i] + (WIDTHS[i] - 0.012 if i in right_cols else 0.008)
            extra = kw if (i == 1 and font) else {}
            ax.text(x, top + ROW_PX / 2, c, fontsize=12, color=color, va="center",
                    ha="right" if i in right_cols else "left", fontweight="bold" if i == 1 else "normal", **extra)
    ax.plot([0, 1], [y0 + n * ROW_PX] * 2, color=LINE, lw=1)
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=DPI, facecolor=BG)
    plt.close(fig)
    return buf.getvalue()
