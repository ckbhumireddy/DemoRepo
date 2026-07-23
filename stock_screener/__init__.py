"""Earnings-crash quality screener with options-strategy ideas.

A tool that finds fundamentally strong stocks that recently sold off on an
earnings report -- potential mean-reversion candidates -- and suggests
educational options strategies to express a recovery thesis.

This package is for research and education only. Nothing here is financial
advice, and it does not place orders.
"""

from .config import ScreenConfig
from .analysis.screener import Screener

__all__ = ["ScreenConfig", "Screener"]
__version__ = "0.1.0"
