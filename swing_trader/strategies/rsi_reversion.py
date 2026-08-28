"""Strategy 3 — oversold snapback ("rsi-reversion").

Short-horizon mean reversion inside a long-term uptrend: a few violent
down days in a stock still above its 200-day average tend to snap back.
The RSI here is a short one (3 bars) — it measures the last few sessions'
panic, not the trend. This family of strategies typically wins often and
small, so the reward/risk floor is deliberately lower than the swing
strategies'; the win rate is supposed to carry it, and the backtest gate
decides whether it actually does.

The plan: RSI(3) under the threshold while above the 200-day, enter at
the close; stop under the selloff's own low; target the highest close of
the pre-selloff window — "back to normal", not "to the moon".
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Sequence

from earnings_analyzer.models import PriceBar

from .base import Signal

NAME = "rsi-reversion"


def rsi(closes: Sequence[float], period: int) -> Optional[float]:
    """Plain (simple-average) RSI over the last ``period`` changes."""
    if len(closes) < period + 1:
        return None
    changes = [b - a for a, b in zip(closes[-period - 1:-1], closes[-period:])]
    gains = sum(c for c in changes if c > 0)
    losses = -sum(c for c in changes if c < 0)
    if losses == 0:
        return 100.0
    if gains == 0:
        return 0.0
    rs = (gains / period) / (losses / period)
    return 100.0 - 100.0 / (1.0 + rs)


@dataclass
class RsiReversion:
    name: str = NAME
    description: str = (
        "Short-RSI oversold snapbacks in stocks above their 200-day; "
        "high win rate, modest targets."
    )

    rsi_period: int = 3
    rsi_threshold: float = 20.0
    ma_slow: int = 200
    stop_lookback: int = 5          # the selloff's low is the line
    stop_buffer: float = 0.005
    target_lookback: int = 10       # "back to normal" reference
    min_rr: float = 1.0
    min_price: float = 10.0

    def min_history(self) -> int:
        return self.ma_slow + 10

    def research_grid(self) -> dict:
        return {
            "rsi_threshold": [10.0, 20.0, 30.0],
            "stop_lookback": [3, 5, 8],
            "target_lookback": [5, 10, 15],
            "min_rr": [0.8, 1.0, 1.5],
        }

    def evaluate(self, ticker: str, bars: Sequence[PriceBar]) -> Optional[Signal]:
        ordered = sorted(bars, key=lambda b: b.day)
        if len(ordered) < self.min_history():
            return None
        last = ordered[-1]
        price = last.close
        if price < self.min_price:
            return None
        closes: List[float] = [b.close for b in ordered]
        ma_slow = sum(closes[-self.ma_slow:]) / self.ma_slow
        if price <= ma_slow:
            return None                  # reversion longs need the uptrend
        value = rsi(closes, self.rsi_period)
        if value is None or value > self.rsi_threshold:
            return None

        selloff_low = min(b.low for b in ordered[-self.stop_lookback:])
        stop = selloff_low * (1 - self.stop_buffer)
        target = max(closes[-self.target_lookback:])
        risk = price - stop
        if risk <= 0 or target <= price:
            return None
        rr = (target - price) / risk
        if rr < self.min_rr:
            return None
        return Signal(
            strategy=self.name,
            ticker=ticker,
            day=last.day,
            price=price,
            entry=price,
            stop=stop,
            target=target,
            support=selloff_low,
            resistance=target,
            note=(
                f"RSI({self.rsi_period}) at {value:.0f} above the 200-day; "
                f"targeting the {self.target_lookback}-bar high"
            ),
            context={"rsi": value, "ma_slow": ma_slow},
        )
