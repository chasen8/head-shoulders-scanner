"""D23 golden examples: the user's five hand-marked inverse head-and-shoulders (TradingView screenshots).

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

# D36: three cases the user checked by hand in TradingView (2026-10-22, Taipei time; converted to UTC here,
# daily / weekly bars labelled by their UTC open date; Binance weekly bars open Monday 00:00 UTC).
# Prices are the K-line values at the user's bars (the user's chart labels agree within rounding).
# They are NOT added to GOLDEN_EXAMPLES: those five feed the V4 templates (scripts/build_templates.py).
D36_SCREENSHOT_UTC = "2026-10-21T17:00:00Z"  # 2026-10-22 01:00 UTC+8: last closed 4h bar opens 10-21 12:00 UTC
D36_CASES = {
    # we showed "RS confirmed, not broken out" with N2 = the post-breakout high; the user's pattern broke out
    "BTCUSDT_binance_spot_1d": {
        "source": "binance_spot", "symbol": "BTCUSDT", "timeframe": "1d",
        "screenshot_utc": D36_SCREENSHOT_UTC,
        "points": {
            "LS": ("2026-04-29T00:00:00Z", 74937.52),
            "N1": ("2026-05-06T00:00:00Z", 82850.0),  # user: "LS 後約 7 天的高點, ~83,000"
            "N2": ("2026-09-03T00:00:00Z", 82300.0),  # user: ~82,300 (RS -8.79%)
            "RS": ("2026-09-16T00:00:00Z", 75064.82),  # user's bar; the swing low is 09-15 (74,967.97)
        },
    },
    # the newest closed bar's wick (high 0.1653, user ~0.1645) is far above the neckline (~0.142), close 0.1417
    "ATUSDT_binance_spot_4h": {
        "source": "binance_spot", "symbol": "ATUSDT", "timeframe": "4h",
        "screenshot_utc": D36_SCREENSHOT_UTC,
        "wick_bar": ("2026-10-21T08:00:00Z", 0.1653, 0.1417),  # (open time, high, close)
        "H": "2026-10-03T16:00:00Z",
    },
    # the user's weekly pattern with a falling neckline (we only showed a rising 1d one)
    "LDOUSDT_binance_spot_1w": {
        "source": "binance_spot", "symbol": "LDOUSDT", "timeframe": "1w",
        "screenshot_utc": D36_SCREENSHOT_UTC,
        "points": {
            "LS": ("2025-12-15T00:00:00Z", 0.4913),
            "N1": ("2026-01-05T00:00:00Z", 0.6808),  # (0.6808 - 0.4913) / 0.6808 = the user's -27.83%
            "H": ("2026-06-22T00:00:00Z", 0.2341),
            "N2": ("2026-09-21T00:00:00Z", 0.5066),  # user ~0.507
            "RS": ("2026-10-12T00:00:00Z", 0.3630),  # (0.5066 - 0.3630) / 0.5066 = the user's -28.35%
        },
        "user_head_rise": 1.5057, "user_target": 1.1329,
    },
    "LDOUSDT_binance_spot_1d": {
        "source": "binance_spot", "symbol": "LDOUSDT", "timeframe": "1d",
        "screenshot_utc": D36_SCREENSHOT_UTC,
    },
}

# D45: the user's SKYAIUSDT Gate perpetual 4h case (site showed "RS confirmed", score 82.6 -- reproduced as of the bar
# opening 2026-10-25 00:00 UTC). Our LS was the 2026-10-04 12:00 flash-crash bar (open 0.04064, close 0.03509,
# low 0.03103: a lone lower wick of 0.38 head heights); the user: "左肩很明顯是錯的". N1 ~10-06, H 10-18 20:00
# (0.02509), N2 ~10-21, RS ~10-22/23. Not added to GOLDEN_EXAMPLES (those feed the V4 templates).
D45_CASES = {
    "SKYAIUSDT_gate_perp_4h": {
        "source": "gate_perp", "symbol": "SKYAI_USDT", "timeframe": "4h",
        "screenshot_utc": "2026-10-25T04:00:00Z",  # last closed bar opens 10-25 00:00 UTC
        "wick_ls": ("2026-10-04T12:00:00Z", 0.03103),
        "H": ("2026-10-18T20:00:00Z", 0.02509),
    },
}
