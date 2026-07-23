"""Analysis modules: fundamentals, earnings crashes, options, screening."""

from .earnings import detect_earnings_crash
from .fundamentals import score_fundamentals
from .options import suggest_option_strategies
from .screener import Screener

__all__ = [
    "detect_earnings_crash",
    "score_fundamentals",
    "suggest_option_strategies",
    "Screener",
]
