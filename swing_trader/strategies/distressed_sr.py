"""Strategy 1 — distressed large-cap, traded between local S/R.

The thesis: a large-cap stock that has fallen hard does not move in a
straight line to its bottom — it swings, and the swings respect levels
because every trapped holder remembers their price. This strategy only
takes the long side of that corridor: buy near a tested support, target the
next resistance, and let the stop answer "what if the floor breaks".

The screen, in order:

1. **Large cap.** The universe is the S&P 500 — membership is the market-cap
   filter, since no free feed in this repo carries fundamentals. A price
   floor keeps out post-collapse penny cases.
2. **Distressed.** Price at least ``min_drawdown`` (default 30%) below its
   52-week high AND below its 200-day average. Both, because a stock 30%
   off a spike but above a rising 200-day is a pullback, not distress.
3. **At support.** A support level with ``min_touches`` pivots sits within
   ``entry_band`` below the close, and the last bar closed strong (upper
   half of its range) — the difference between "at support" and "falling
   through it".
4. **Worth taking.** The stop goes ``stop_buffer`` below the support; the
   target is the nearest tested resistance. If reward/risk < ``min_rr``
   there is no trade, however pretty the level.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence

from earnings_analyzer.models import PriceBar

from ..levels import bracket, find_levels
from .base import Signal

NAME = "distressed-sr"


@dataclass
class DistressedSupportResistance:
    """Parameters double as the backtest's degrees of freedom."""

    name: str = NAME
    description: str = (
        "Large caps 30%+ off their 52-week high, bought near tested local "
        "support with the next resistance as target."
    )

    min_drawdown: float = 0.30      # fall from the 52-week high
    min_price: float = 10.0         # distressed, not destroyed
    pivot_span: int = 3
    cluster_tolerance: float = 0.015
    min_touches: int = 2            # pivots a level needs to be tradeable
    entry_band: float = 0.02        # how close to support "near" means
    stop_buffer: float = 0.03       # stop distance below the support level
    min_rr: float = 2.0             # reward/risk floor
    year_window: int = 252

    def min_history(self) -> int:
        # A 200-day average plus enough room for pivots to form.
        return 220

    # ------------------------------------------------------------------ #
    # Screen pieces, exposed for tests and for the scan's reporting.
    # ------------------------------------------------------------------ #
    def drawdown(self, bars: Sequence[PriceBar]) -> Optional[float]:
        """Fall from the 52-week high as a positive decimal."""
        if not bars:
            return None
        year = bars[-self.year_window:]
        high = max(b.high for b in year)
        price = bars[-1].close
        if high <= 0 or price <= 0:
            return None
        return 1.0 - price / high

    def is_distressed(self, bars: Sequence[PriceBar]) -> bool:
        ordered = sorted(bars, key=lambda b: b.day)
        if len(ordered) < self.min_history():
            return False
        price = ordered[-1].close
        if price < self.min_price:
            return False
        fall = self.drawdown(ordered)
        if fall is None or fall < self.min_drawdown:
            return False
        closes = [b.close for b in ordered]
        ma200 = sum(closes[-200:]) / 200
        return price < ma200

    @staticmethod
    def _closed_strong(bar: PriceBar) -> bool:
        """Close in the upper half of the bar — buyers finished the day."""
        span = bar.high - bar.low
        if span <= 0:
            return True
        return (bar.close - bar.low) / span >= 0.5

    # ------------------------------------------------------------------ #
    # The contract method.
    # ------------------------------------------------------------------ #
    def evaluate(self, ticker: str, bars: Sequence[PriceBar]) -> Optional[Signal]:
        ordered = sorted(bars, key=lambda b: b.day)
        if not self.is_distressed(ordered):
            return None
        last = ordered[-1]
        price = last.close

        levels = find_levels(ordered, self.pivot_span, self.cluster_tolerance)
        support, resistance = bracket(levels, price, self.min_touches)
        if support is None or resistance is None:
            return None
        if price > support.price * (1 + self.entry_band):
            return None                      # not near the floor
        if not self._closed_strong(last):
            return None                      # at the floor but still falling

        stop = support.price * (1 - self.stop_buffer)
        target = resistance.price
        risk = price - stop
        if risk <= 0:
            return None
        rr = (target - price) / risk
        if rr < self.min_rr:
            return None

        fall = self.drawdown(ordered) or 0.0
        return Signal(
            strategy=self.name,
            ticker=ticker,
            day=last.day,
            price=price,
            entry=price,
            stop=stop,
            target=target,
            support=support.price,
            resistance=resistance.price,
            note=(
                f"{fall * 100:.0f}% off its 52-week high; support "
                f"{support.touches}x tested, resistance {resistance.touches}x"
            ),
            context={
                "drawdown": fall,
                "support_touches": float(support.touches),
                "resistance_touches": float(resistance.touches),
            },
        )
