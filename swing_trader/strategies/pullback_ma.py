"""Strategy 2 — uptrend pullback ("pullback-ma").

The counterpart to strategy 1: where distressed-sr hunts broken stocks,
this one trades healthy ones. A stock in a long uptrend (50-day above
200-day, price above both) doesn't reward buying strength — it rewards
buying the routine dips back to its rising 50-day average, which acts as
the moving support the whole trend has been bought at.

The plan: when price pulls back into the 50-day band and closes strong,
enter at the close; the stop goes under the lowest low of the pullback
itself (if that breaks, the pullback was a reversal); the target is the
recent swing high the trend is presumed to retest.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence

from earnings_analyzer.models import PriceBar

from .base import Signal

NAME = "pullback-ma"


@dataclass
class UptrendPullback:
    name: str = NAME
    description: str = (
        "Healthy uptrends (50>200-day), bought on pullbacks into the 50-day "
        "band with the recent swing high as target."
    )

    ma_fast: int = 50
    ma_slow: int = 200
    band: float = 0.02              # how close to the 50-day counts as a touch
    stop_lookback: int = 10         # the pullback's own low guards the trade
    stop_buffer: float = 0.01
    target_lookback: int = 60       # the swing high the trend should retest
    min_rr: float = 1.5
    min_price: float = 10.0

    def min_history(self) -> int:
        return self.ma_slow + 20

    def research_grid(self) -> dict:
        return {
            "band": [0.01, 0.02, 0.035],
            "stop_lookback": [5, 10, 15],
            "min_rr": [1.0, 1.5, 2.0],
            "stop_buffer": [0.005, 0.01, 0.02],
        }

    @staticmethod
    def _closed_strong(bar: PriceBar) -> bool:
        span = bar.high - bar.low
        return span <= 0 or (bar.close - bar.low) / span >= 0.5

    def evaluate(self, ticker: str, bars: Sequence[PriceBar]) -> Optional[Signal]:
        ordered = sorted(bars, key=lambda b: b.day)
        if len(ordered) < self.min_history():
            return None
        last = ordered[-1]
        price = last.close
        if price < self.min_price:
            return None
        closes = [b.close for b in ordered]
        ma_fast = sum(closes[-self.ma_fast:]) / self.ma_fast
        ma_slow = sum(closes[-self.ma_slow:]) / self.ma_slow
        # The regime filter IS the strategy: no uptrend, no trade.
        if not (ma_fast > ma_slow and price > ma_slow):
            return None
        # A pullback means price came DOWN into the band, not up through it:
        # require the recent high to sit meaningfully above the average.
        if abs(price / ma_fast - 1.0) > self.band:
            return None
        recent_high = max(b.high for b in ordered[-self.target_lookback:])
        if recent_high <= price * (1 + self.band):
            return None                  # nothing above to retest
        if not self._closed_strong(last):
            return None

        pullback_low = min(b.low for b in ordered[-self.stop_lookback:])
        stop = pullback_low * (1 - self.stop_buffer)
        risk = price - stop
        if risk <= 0:
            return None
        rr = (recent_high - price) / risk
        if rr < self.min_rr:
            return None
        return Signal(
            strategy=self.name,
            ticker=ticker,
            day=last.day,
            price=price,
            entry=price,
            stop=stop,
            target=recent_high,
            support=ma_fast,
            resistance=recent_high,
            note=(
                f"pullback to the {self.ma_fast}-day in an uptrend; "
                f"targeting the {self.target_lookback}-bar high"
            ),
            context={"ma_fast": ma_fast, "ma_slow": ma_slow},
        )
