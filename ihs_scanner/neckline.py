"""Neckline through N1 and N2 (spec steps 5 and 6)."""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Neckline:
    n1_index: int
    n1_price: float
    n2_index: int
    n2_price: float
    angle_scale: float = 100.0

    @property
    def bars_between(self) -> int:
        return self.n2_index - self.n1_index

    @property
    def slope_pct_per_bar(self) -> float:
        """((N2.high - N1.high) / N1.high) / bars_between"""
        return ((self.n2_price - self.n1_price) / self.n1_price) / self.bars_between

    @property
    def angle(self) -> float:
        """atan(slope_pct_per_bar * angle_scale) in degrees."""
        return math.degrees(math.atan(self.slope_pct_per_bar * self.angle_scale))

    def price_at(self, i):
        """N1.high + (N2.high - N1.high) * ((i - N1.index) / (N2.index - N1.index)).

        Works for a scalar index or a numpy array of indices (extrapolates beyond N2).
        """
        return self.n1_price + (self.n2_price - self.n1_price) * (
            (i - self.n1_index) / (self.n2_index - self.n1_index))

    neckline_price_at = price_at


def _index_price(point: Any) -> tuple[int, float]:
    if hasattr(point, "index") and hasattr(point, "price"):
        return int(point.index), float(point.price)
    if isinstance(point, dict):
        return int(point["index"]), float(point["price"])
    idx, price = point
    return int(idx), float(price)


def calculate_neckline(N1: Any, N2: Any, angle_scale: float = 100.0) -> Neckline:
    """N1 / N2: SwingPoint, {"index", "price"} dict, or (index, price) tuple."""
    i1, p1 = _index_price(N1)
    i2, p2 = _index_price(N2)
    if i2 <= i1:
        raise ValueError("N2 must come after N1")
    if p1 <= 0:
        raise ValueError("N1 price must be positive")
    return Neckline(i1, p1, i2, p2, float(angle_scale))
