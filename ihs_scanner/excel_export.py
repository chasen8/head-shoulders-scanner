"""Taiwan stocks "右肩已確認" (status watchlist) -> Excel, written by every TW build (charter D39).

build_site_tw.main_tw() calls write_rs_confirmed(payload, site/data/tw/rs_confirmed.xlsx) with the payload it has
just written as scan.json (nothing is downloaded again). Two sheets: 「右肩已確認」 (one row per pattern, best score
first) and 「說明」 (source, time, column definitions). Every number is the scanner's own output value: no formulas.
A failure here never fails the build (the caller catches and logs it).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import quote

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

TPE = timezone(timedelta(hours=8))
FONT = "Arial"
SITE = "https://chasen8.github.io/head-shoulders-site/"
FILE_NAME = "rs_confirmed.xlsx"
SHEET_ROWS, SHEET_NOTES = "右肩已確認", "說明"
TF_NAME = {"4h": "4 小時", "1d": "日線", "1w": "週線"}
TF_ORDER = {"1w": 0, "1d": 1, "4h": 2}
TV_INTERVAL = {"4h": "240", "1d": "1D", "1w": "1W"}
EXCHANGE_NAME = {"twse": "上市", "tpex": "上櫃"}
TIME_KEYS = {"LS_time", "H_time", "N2_time", "RS_time", "RS_confirm_time"}
LINK_TV, LINK_SITE = "link_tv", "link_site"


def _signed_distance(r: Mapping[str, Any]) -> float | None:
    if r.get("distance_to_neckline") is None:
        return None
    return (-1 if r.get("neckline_side") == "below" else 1) * float(r["distance_to_neckline"])


# header, width, number format, getter (a TIME_KEYS name or LINK_* means a time / hyperlink column)
COLUMNS: list[tuple[str, int, str | None, Callable[[Mapping[str, Any]], Any]]] = [
    ("代號", 8, "@", lambda r: r["symbol"]),
    ("名稱", 14, "@", lambda r: r.get("name") or ""),
    ("市場", 7, "@", lambda r: EXCHANGE_NAME.get(r.get("exchange"), r.get("exchange"))),
    ("週期", 8, "@", lambda r: TF_NAME.get(r["timeframe"], r["timeframe"])),
    ("分數", 7, "0.0", lambda r: r.get("pattern_score")),
    ("形狀分", 8, "0.0", lambda r: r.get("shape_score")),
    ("目前價", 10, "#,##0.00", lambda r: r.get("current_close")),
    ("目前頸線價", 11, "#,##0.00", lambda r: r.get("current_neckline_price")),
    ("距頸線", 9, "0.0%", _signed_distance),
    ("頸線斜率\nN1→N2", 10, "0.0%", lambda r: r.get("neckline_change_pct")),
    ("TP1", 10, "#,##0.00", lambda r: r.get("tp1")),
    ("TP2\n(預估)", 10, "#,##0.00", lambda r: r.get("tp2")),
    ("失效價\n(頭部低點)", 11, "#,##0.00", lambda r: r.get("invalidation_price")),
    ("左肩跌幅", 9, "0.0%", lambda r: r.get("drop_left_pct")),
    ("右肩跌幅", 9, "0.0%", lambda r: r.get("drop_right_pct")),
    ("頭到頸線\n漲幅", 10, "0.0%", lambda r: r.get("head_rise_pct")),
    ("碰頸線\n次數", 8, "0", lambda r: r.get("neckline_touches")),
    ("左肩 LS", 12, None, lambda r: "LS_time"),
    ("頭 H", 12, None, lambda r: "H_time"),
    ("右頸 N2", 12, None, lambda r: "N2_time"),
    ("右肩 RS", 12, None, lambda r: "RS_time"),
    ("右肩確認", 16, "yyyy-mm-dd hh:mm", lambda r: "RS_confirm_time"),
    ("TradingView", 12, None, lambda r: LINK_TV),
    ("網站", 8, None, lambda r: LINK_SITE),
]
HEADERS = [c[0] for c in COLUMNS]


def tpe_time(iso: str | None, with_time: bool) -> datetime | None:
    """UTC ISO text -> naive Taipei datetime (Excel has no time zones); date only unless with_time."""
    if not iso:
        return None
    t = datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).astimezone(TPE)
    return t.replace(tzinfo=None) if with_time else t.replace(tzinfo=None, hour=0, minute=0)


def tradingview_link(r: Mapping[str, Any]) -> str:
    tv = r.get("tv_symbol") or f"{str(r.get('exchange', 'twse')).upper()}:{r['symbol']}"
    return (f"https://www.tradingview.com/chart/?symbol={quote(str(tv), safe='')}"
            f"&interval={TV_INTERVAL.get(r['timeframe'], '1D')}")


def site_link(r: Mapping[str, Any]) -> str:
    return f"{SITE}?market=tw#{quote(str(r['pattern_id']), safe='')}"


def confirmed_rows(payload: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """status watchlist (右肩已確認) rows, best score first, then 1w / 1d / 4h, then code."""
    rows = [r for r in payload.get("results") or [] if r.get("status") == "watchlist"]
    rows.sort(key=lambda r: (-float(r.get("pattern_score") or 0), TF_ORDER.get(r["timeframe"], 9), r["symbol"]))
    return rows


def build_workbook(payload: Mapping[str, Any]) -> tuple[Workbook, int]:
    rows = confirmed_rows(payload)
    wb = Workbook()
    ws = wb.active
    ws.title = SHEET_ROWS
    head_font = Font(name=FONT, bold=True, color="FFFFFF", size=10)
    head_fill = PatternFill("solid", fgColor="2962FF")
    body_font = Font(name=FONT, size=10)
    link_font = Font(name=FONT, size=10, color="2962FF", underline="single")
    zebra = PatternFill("solid", fgColor="F0F3FA")
    border = Border(bottom=Side(style="thin", color="E0E3EB"))

    for c, (title, width, _, _) in enumerate(COLUMNS, 1):
        cell = ws.cell(row=1, column=c, value=title)
        cell.font, cell.fill = head_font, head_fill
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        ws.column_dimensions[get_column_letter(c)].width = width
    ws.row_dimensions[1].height = 30

    for i, r in enumerate(rows, 2):
        for c, (_title, _w, fmt, get) in enumerate(COLUMNS, 1):
            v = get(r)
            cell = ws.cell(row=i, column=c)
            link = False
            if isinstance(v, str) and v in TIME_KEYS and fmt != "@":
                with_time = v == "RS_confirm_time" or r["timeframe"] == "4h"
                cell.value = tpe_time(r.get(v), with_time)
                cell.number_format = fmt or ("yyyy-mm-dd hh:mm" if with_time else "yyyy-mm-dd")
            elif v == LINK_TV and fmt is None:
                cell.value, cell.hyperlink, link = "開啟圖表", tradingview_link(r), True
            elif v == LINK_SITE and fmt is None:
                cell.value, cell.hyperlink, link = "網站", site_link(r), True
            else:
                if isinstance(v, float):  # store what is displayed: prices 2 dp, percentages 0.1%, scores 0.1
                    v = round(v, 4 if fmt == "0.0%" else 2 if fmt == "#,##0.00" else 1)
                cell.value = v
                if fmt:
                    cell.number_format = fmt
            cell.font = link_font if link else body_font
            cell.border = border
            if i % 2 == 0:
                cell.fill = zebra
            if c <= 4:
                cell.alignment = Alignment(horizontal="left")
    ws.freeze_panes = "C2"
    ws.auto_filter.ref = f"A1:{get_column_letter(len(COLUMNS))}{len(rows) + 1}"

    ns = wb.create_sheet(SHEET_NOTES)
    gen = tpe_time(payload.get("generated_at"), True)
    gen_text = f"{gen:%Y-%m-%d %H:%M}" if gen else "-"
    notes = [
        ("台股頭肩底：右肩已確認清單", True),
        (f"資料時間：{gen_text}（台北時間）；共 {len(rows)} 筆，依分數由高到低排序。", False),
        (f"資料來源：頭肩底掃描器公開資料 {SITE}data/tw/scan.json（status = watchlist）。"
         "所有數字是掃描器的輸出值，表內沒有公式。", False),
        ("型態偵測工具，不是投資建議。", True),
        ("", False),
        ("欄位說明", True),
        ("右肩已確認：右肩低點已被確認（右邊走完 pivot 根數），但還沒有突破頸線。", False),
        ("分數：0–100（左右肩對稱 30、頭部深度 25、頸線平緩 25、接近／突破頸線 20）。形狀分：離形狀驗證門檻多遠，越高越乾淨。", False),
        ("距頸線：目前收盤相對目前頸線價，負數代表在頸線下方（右肩已確認的型態都還在頸線下）。", False),
        ("頸線斜率 N1→N2：頸線右點相對左點的漲跌；≤ −3% 下降、≥ +3% 上升、其餘水平。", False),
        ("TP1：H ×（頸線中點 ÷ H）^1.414（對數斐波那契 1.414）。", False),
        ("TP2（預估）：突破價 ×（1 + 頭到頸線漲幅）；還沒突破，突破價先用目前頸線價，所以是預估、會隨頸線移動。", False),
        ("失效價：頭部低點；收盤跌破代表型態不成立。", False),
        ("左／右肩跌幅：從各自頸線點（N1／N2）跌到肩部低點的幅度。頭到頸線漲幅：頭部低點漲到頸線的幅度。", False),
        ("時間欄位：台北時間；4 小時型態含時分，日線／週線只顯示日期（週線為該週週一）。"
         "台股 4 小時 K 線一天兩根（09:00–13:00、13:00–13:30）。", False),
        ("連結：TradingView 開啟對應週期的圖表；「網站」開啟掃描器網站上這一筆的詳細頁。", False),
    ]
    ns.column_dimensions["A"].width = 120
    for i, (text, bold) in enumerate(notes, 1):
        cell = ns.cell(row=i, column=1, value=text)
        cell.font = Font(name=FONT, size=12 if i == 1 else 10, bold=bold)
        cell.alignment = Alignment(wrap_text=True, vertical="top")
    return wb, len(rows)


def write_rs_confirmed(payload: Mapping[str, Any], out: Path) -> int:
    """Write the workbook atomically (temp file, then replace). Returns the number of rows."""
    wb, n = build_workbook(payload)
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.stem + ".tmp.xlsx")
    wb.save(tmp)
    tmp.replace(out)
    return n
