"""Short- and long-term trend, scored from daily bars (pure functions).

The two horizons answer different questions, and the interesting names are
the ones where they disagree:

short term (days to weeks)
    5- and 21-session returns plus position against the 20-day average —
    where the stock has been going *lately*.
long term (months)
    6-month return, position against the 200-day average, and whether the
    50-day sits above the 200-day — the regime the stock is in.

Each horizon is scored from three independent components rather than a
single measure, so one noisy input cannot flip the label on its own. Moving
averages and the 52-week range come from ``earnings_analyzer.trend`` so that
math lives in exactly one place.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Sequence

from earnings_analyzer.models import PriceBar
from earnings_analyzer.trend import compute_trend

UP, DOWN, FLAT = "up", "down", "flat"

# Trading sessions per lookback.
SHORT_WINDOW = 5
MEDIUM_WINDOW = 21      # ~1 month
QUARTER_WINDOW = 63     # ~3 months
LONG_WINDOW = 126       # ~6 months
YEAR_WINDOW = 252

# A move smaller than this is drift, not direction.
SHORT_MOVE = 0.02       # 5-session
MEDIUM_MOVE = 0.04      # 21-session
LONG_MOVE = 0.10        # 126-session

# Two of three components must agree before a horizon gets a direction.
LABEL_THRESHOLD = 2

# Price sitting within this much of a moving average (or two averages this
# close together) is not evidence either way, so it casts no vote. Without
# the band an exact tie would resolve bearish, and a stock hugging its
# 200-day would flip label on rounding noise.
NEUTRAL_BAND = 0.005


@dataclass
class TrendView:
    """Both horizons for one ticker, with the numbers behind the labels."""

    price: float
    short_term: str
    long_term: str
    short_score: int
    long_score: int
    ret_5d: Optional[float] = None
    ret_21d: Optional[float] = None
    ret_63d: Optional[float] = None
    ret_126d: Optional[float] = None
    ret_252d: Optional[float] = None
    ma20: Optional[float] = None
    ma50: Optional[float] = None
    ma200: Optional[float] = None
    pct_of_52wk_range: Optional[float] = None

    @property
    def aligned(self) -> bool:
        """Both horizons pointing the same way (and not sideways)."""
        return self.short_term == self.long_term != FLAT

    @property
    def diverging(self) -> bool:
        """Horizons in outright conflict — the pullbacks and the bounces."""
        return FLAT not in (self.short_term, self.long_term) and (
            self.short_term != self.long_term
        )


def pct_change(closes: Sequence[float], window: int) -> Optional[float]:
    """Return over ``window`` sessions as a decimal, or None if too short."""
    if len(closes) <= window:
        return None
    earlier = closes[-1 - window]
    if earlier <= 0:
        return None
    return (closes[-1] - earlier) / earlier


def _vote(value: Optional[float], threshold: float) -> int:
    if value is None:
        return 0
    if value >= threshold:
        return 1
    if value <= -threshold:
        return -1
    return 0


def _side(price: float, average: Optional[float]) -> int:
    """+1 clearly above, -1 clearly below, 0 within :data:`NEUTRAL_BAND`."""
    if average is None or average <= 0:
        return 0
    gap = (price - average) / average
    if gap > NEUTRAL_BAND:
        return 1
    if gap < -NEUTRAL_BAND:
        return -1
    return 0


def _label(score: int) -> str:
    if score >= LABEL_THRESHOLD:
        return UP
    if score <= -LABEL_THRESHOLD:
        return DOWN
    return FLAT


def compute_trend_view(bars: Sequence[PriceBar]) -> Optional[TrendView]:
    """Score both horizons from daily bars; None when there's no usable price.

    Missing history degrades rather than fails: a stock with four months of
    trading still gets a short-term label, and its long-term score simply
    has fewer votes to work with.
    """
    context = compute_trend(list(bars))
    if context is None:
        return None
    closes: List[float] = [b.close for b in sorted(bars, key=lambda b: b.day) if b.close > 0]
    if not closes:
        return None
    price = closes[-1]

    ret_5d = pct_change(closes, SHORT_WINDOW)
    ret_21d = pct_change(closes, MEDIUM_WINDOW)
    ret_63d = pct_change(closes, QUARTER_WINDOW)
    ret_126d = pct_change(closes, LONG_WINDOW)
    ret_252d = pct_change(closes, YEAR_WINDOW)

    short_score = (
        _vote(ret_5d, SHORT_MOVE)
        + _vote(ret_21d, MEDIUM_MOVE)
        + _side(price, context.ma20)
    )
    long_score = (
        _vote(ret_126d, LONG_MOVE)
        + _side(price, context.ma200)
        + (_side(context.ma50, context.ma200) if context.ma50 else 0)
    )

    return TrendView(
        price=price,
        short_term=_label(short_score),
        long_term=_label(long_score),
        short_score=short_score,
        long_score=long_score,
        ret_5d=ret_5d,
        ret_21d=ret_21d,
        ret_63d=ret_63d,
        ret_126d=ret_126d,
        ret_252d=ret_252d,
        ma20=context.ma20,
        ma50=context.ma50,
        ma200=context.ma200,
        pct_of_52wk_range=context.pct_of_52wk_range,
    )
