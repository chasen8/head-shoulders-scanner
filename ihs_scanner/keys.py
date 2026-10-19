"""Stable per-pattern key shared by notify_discord.py (dedup) and build_site.py (watchlist stars).

The key leaves RS out on purpose: a forming right shoulder moves with every new low, so a key that
included it would change on each scan (charter D24). Expects JSON-style rows (times already strings)."""
from __future__ import annotations

from typing import Any, Mapping


def pattern_key(r: Mapping[str, Any]) -> str:
    """exchange|market|symbol|timeframe|LS_time|H_time"""
    return "|".join(str(x) for x in (r.get("exchange") or "binance", r.get("market") or "spot", r["symbol"],
                                     r["timeframe"], r.get("LS_time"), r.get("H_time")))
