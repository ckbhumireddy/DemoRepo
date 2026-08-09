"""Trend context and directional bias (pure functions)."""

from __future__ import annotations

from typing import List, Optional

from .models import PriceBar, TrendContext


def _ma(closes: List[float], window: int) -> Optional[float]:
    if len(closes) < window:
        return None
    return sum(closes[-window:]) / window


def compute_trend(bars: List[PriceBar], streak: int = 0) -> Optional[TrendContext]:
    """Moving averages, 52-week-range position, and a simple bias.

    Bias: bullish when price > MA50 > MA200 and the stock sits in the upper
    half of its 52-week range; bearish on the mirror image; else neutral.
    An earnings beat/miss streak of ±3 or more breaks a neutral tie.
    """
    ordered = sorted(bars, key=lambda b: b.day)
    closes = [b.close for b in ordered if b.close > 0]
    if not closes:
        return None
    price = closes[-1]

    ma20 = _ma(closes, 20)
    ma50 = _ma(closes, 50)
    ma200 = _ma(closes, 200)

    year = closes[-252:]
    lo, hi = min(year), max(year)
    pct_range = (price - lo) / (hi - lo) if hi > lo else None

    bias = "neutral"
    if ma50 is not None and ma200 is not None and pct_range is not None:
        if price > ma50 > ma200 and pct_range >= 0.5:
            bias = "bullish"
        elif price < ma50 < ma200 and pct_range <= 0.5:
            bias = "bearish"
    if bias == "neutral":
        if streak >= 3:
            bias = "bullish"
        elif streak <= -3:
            bias = "bearish"

    return TrendContext(
        price=price,
        ma20=ma20,
        ma50=ma50,
        ma200=ma200,
        pct_of_52wk_range=pct_range,
        bias=bias,
    )
