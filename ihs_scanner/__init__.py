"""Inverse Head and Shoulders scanner (pure OHLCV rules)."""
from .config import DEFAULT_CONFIG, TIMEFRAMES, merge_config
from .detector import deduplicate_patterns, detect_inverse_head_shoulders, find_pattern_candidates
from .neckline import Neckline, calculate_neckline
from .scanner import export_results, scan_symbols, sort_results
from .scoring import calculate_pattern_score
from .swings import SwingPoint, find_swing_points

__all__ = [
    "DEFAULT_CONFIG", "TIMEFRAMES", "merge_config",
    "detect_inverse_head_shoulders", "find_pattern_candidates", "deduplicate_patterns",
    "Neckline", "calculate_neckline", "calculate_pattern_score",
    "scan_symbols", "sort_results", "export_results",
    "SwingPoint", "find_swing_points",
]
