"""The strategy contract: history in, signals out — nothing else.

A strategy is deliberately blind to everything except price bars and its
own parameters: no clocks, no network, no state. That is what makes the
backtest honest — the engine replays history through ``evaluate`` one bar
at a time, and the live scan calls the very same method on today's bars, so
a strategy cannot behave differently under test than in practice.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Dict, Optional, Protocol, Sequence

from earnings_analyzer.models import PriceBar


@dataclass
class Signal:
    """A fully specified trade plan — entry, stop and target, no discretion."""

    strategy: str
    ticker: str
    day: dt.date                  # the bar whose close triggered the signal
    price: float                  # reference price (that close)
    entry: float                  # limit/entry price
    stop: float
    target: float
    support: Optional[float] = None
    resistance: Optional[float] = None
    note: str = ""
    context: Dict[str, float] = field(default_factory=dict)

    @property
    def risk(self) -> float:
        return self.entry - self.stop

    @property
    def reward(self) -> float:
        return self.target - self.entry

    @property
    def rr(self) -> float:
        """Reward-to-risk; the first number a swing trader reads."""
        return self.reward / self.risk if self.risk > 0 else 0.0


class Strategy(Protocol):
    """What the backtest engine and the live scan both program against."""

    name: str
    description: str

    def min_history(self) -> int:
        """Bars required before evaluate() can produce a signal."""
        ...

    def evaluate(self, ticker: str, bars: Sequence[PriceBar]) -> Optional[Signal]:
        """A signal as of the LAST bar in ``bars``, or None.

        Must treat ``bars`` as the entire knowable world: the last bar is
        "today", and using anything beyond it is lookahead by definition.
        """
        ...
