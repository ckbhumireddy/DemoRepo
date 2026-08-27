"""Strategy registry: name -> Strategy instance.

A strategy registers here to become visible to the CLI, the backtest and
the scan. The service layer looks strategies up by name and never imports
one directly, so adding strategy 2 is: write the module, register it.
"""

from __future__ import annotations

import copy
from typing import Dict, List

from .base import Strategy
from .distressed_sr import DistressedSupportResistance

_REGISTRY: Dict[str, Strategy] = {}


def register(strategy: Strategy) -> Strategy:
    _REGISTRY[strategy.name] = strategy
    return strategy


def get_strategy(name: str) -> Strategy:
    """A FRESH copy every time: callers tune parameters on what they get
    back, and a shared instance would leak one caller's tuning into the
    next scan or research trial."""
    try:
        return copy.copy(_REGISTRY[name])
    except KeyError:
        known = ", ".join(sorted(_REGISTRY)) or "(none)"
        raise KeyError(f"unknown strategy {name!r}; registered: {known}") from None


def strategy_names() -> List[str]:
    return sorted(_REGISTRY)


register(DistressedSupportResistance())
