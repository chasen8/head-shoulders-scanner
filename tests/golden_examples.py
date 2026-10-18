"""D22 golden examples: the user's five hand-marked inverse head-and-shoulders (TradingView screenshots).

The user's chart times are UTC+8; every time below is already converted to UTC (minus 8 hours).
Daily bars are UTC-aligned (Binance 1d, OKX "1Dutc"), labelled by their UTC open date.
`screenshot_utc` is when the screenshot was taken: the test keeps only bars that had CLOSED by then
(open_time + bar length <= screenshot_utc), i.e. exactly what a live scan would have seen.

Prices are the values the user wrote on the chart. "mid" is the neckline touch between N2 and N1
that the user drew through (not one of the five points). `user_*` are the user's own annotations
(drop % of each shoulder from its neckline high, head-to-neckline rise %, target price).
"""
from __future__ import annotations

GOLDEN_EXAMPLES = {
    "HMSTRUSDT_binance_spot_4h": {
        "source": "binance_spot", "symbol": "HMSTRUSDT", "timeframe": "4h",
        "screenshot_utc": "2026-08-17T16:52:00Z",  # 2026-08-18 00:52 UTC+8
        "points": {
            "LS": ("2026-07-10T04:00:00Z", 0.0001837),
            "N1": ("2026-07-11T08:00:00Z", 0.0002430),
            "H": ("2026-07-30T16:00:00Z", 0.0001634),
            "N2": ("2026-08-09T04:00:00Z", 0.0002320),
            "RS": ("2026-08-12T20:00:00Z", 0.0001750),
        },
        "mid": ("2026-07-25T20:00:00Z", 0.0002370),
        "user_drop_left": 0.2440, "user_drop_right": 0.2457, "user_head_rise": 0.450,
        "user_target": 0.0003299,
    },
    "TRUSTUSDT_binance_usdm_4h": {
        "source": "binance_usdm", "symbol": "TRUSTUSDT", "timeframe": "4h",
        "screenshot_utc": "2026-10-09T06:57:00Z",  # 2026-10-09 14:57 UTC+8
        "points": {
            "LS": ("2026-05-23T08:00:00Z", 0.06307),
            "N1": ("2026-05-24T12:00:00Z", 0.08109),
            "H": ("2026-06-10T20:00:00Z", 0.04536),
            "N2": ("2026-09-28T00:00:00Z", 0.07111),
            "RS": ("2026-10-08T00:00:00Z", 0.05285),
        },
        "mid": ("2026-07-27T00:00:00Z", 0.07548),
        "user_drop_left": 0.2222, "user_drop_right": 0.2568, "user_head_rise": 0.6641,
        "user_target": 0.11497,  # = user's breakout point 0.06909 x 1.6641
    },
    "GRAM-USDT_okx_spot_1d": {
        "source": "okx_spot", "symbol": "GRAM-USDT", "timeframe": "1d",
        "screenshot_utc": "2026-10-09T06:58:00Z",  # 2026-10-09 14:58 UTC+8
        "points": {
            "LS": ("2026-06-30T00:00:00Z", 1.485),
            "N1": ("2026-07-04T00:00:00Z", 1.844),
            "H": ("2026-09-16T00:00:00Z", 1.286),
            "N2": ("2026-09-27T00:00:00Z", 1.725),
            "RS": ("2026-10-07T00:00:00Z", 1.388),
        },
        "mid": ("2026-08-16T00:00:00Z", 1.782),
        "user_drop_left": 0.1947, "user_drop_right": 0.1956, "user_head_rise": 0.3860,
        "user_target": 2.327,
    },
    "AZTECUSDT_binance_usdm_1d": {
        "source": "binance_usdm", "symbol": "AZTECUSDT", "timeframe": "1d",
        # the user gave only the date; same session as the other 2026-10-09 screenshots
        "screenshot_utc": "2026-10-09T06:58:00Z",
        "points": {
            "LS": ("2026-05-20T00:00:00Z", 0.01905),
            "N1": ("2026-05-26T00:00:00Z", 0.02847),
            "H": ("2026-08-18T00:00:00Z", 0.01134),
            "N2": ("2026-09-28T00:00:00Z", 0.02128),
            # RS was still forming; the user marked it at 0.01422 (price at the time ~0.01486)
            "RS": (None, 0.01422),
        },
        "mid": ("2026-07-28T00:00:00Z", 0.02456),
        "user_drop_left": 0.3309, "user_drop_right": 0.3318, "user_head_rise": 1.1659,
        "user_target": 0.04140,
    },
    "PENDLEUSDT_binance_spot_1d": {
        "source": "binance_spot", "symbol": "PENDLEUSDT", "timeframe": "1d",
        "screenshot_utc": "2026-10-09T06:58:00Z",
        "points": {
            "LS": ("2025-11-21T00:00:00Z", 2.022),
            "N1": ("2025-11-28T00:00:00Z", 2.888),
            "H": ("2026-04-05T00:00:00Z", 0.969),
            "N2": ("2026-09-19T00:00:00Z", 2.789),
            "RS": ("2026-10-08T00:00:00Z", 2.006),
        },
        "mid": ("2026-04-27T00:00:00Z", 2.824),
        "user_drop_left": 0.2999, "user_drop_right": 0.2807, "user_head_rise": 1.9143,
        "user_target": 8.082,
    },
}

# bars kept in each fixture: up to this many closed bars before the screenshot ...
BARS_BEFORE = 1000
# ... plus a few bars after it (the test cuts them off; kept so truncation is really exercised)
BARS_AFTER = 30
