"""Realized-volatility and IV-rank calculations.

Free data feeds don't expose a history of *implied* volatility, so a textbook
IV Rank (current IV vs its own trailing 52-week range) isn't directly
computable. We approximate it by comparing the current implied volatility to the
trailing range of *realized* (historical) volatility. It's a proxy -- clearly
labelled as such on the resulting :class:`IVRank` -- and still a useful gauge of
whether options premium is rich or cheap relative to how the stock has actually
moved.
"""

from __future__ import annotations

import math
from typing import List, Optional

from ..data.models import IVRank, PriceBar

TRADING_DAYS = 252


def log_returns(closes: List[float]) -> List[float]:
    out: List[float] = []
    for prev, cur in zip(closes, closes[1:]):
        if prev > 0 and cur > 0:
            out.append(math.log(cur / prev))
    return out


def _stdev(xs: List[float]) -> float:
    n = len(xs)
    if n < 2:
        return 0.0
    mean = sum(xs) / n
    var = sum((x - mean) ** 2 for x in xs) / (n - 1)
    return math.sqrt(var)


def realized_vol_series(
    closes: List[float], window: int = 21
) -> List[float]:
    """Rolling annualized realized volatility from a close series.

    Returns one value per window-sized slice of daily log returns.
    """
    rets = log_returns(closes)
    if len(rets) < window:
        # Not enough data for a rolling window: fall back to a single estimate.
        if len(rets) >= 2:
            return [_stdev(rets) * math.sqrt(TRADING_DAYS)]
        return []
    series: List[float] = []
    for i in range(window, len(rets) + 1):
        window_rets = rets[i - window : i]
        series.append(_stdev(window_rets) * math.sqrt(TRADING_DAYS))
    return series


def compute_iv_rank(
    ticker: str,
    current_iv: Optional[float],
    prices: List[PriceBar],
    *,
    window: int = 21,
) -> Optional[IVRank]:
    """Rank ``current_iv`` against the trailing realized-vol range.

    Returns ``None`` when there isn't enough data or no current IV is available.
    """
    if current_iv is None or current_iv <= 0 or len(prices) < window + 2:
        return None

    closes = [b.close for b in sorted(prices, key=lambda b: b.day)]
    hv = realized_vol_series(closes, window=window)
    hv = [v for v in hv if v > 0]
    if not hv:
        return None

    hv_low = min(hv)
    hv_high = max(hv)
    span = hv_high - hv_low
    if span <= 0:
        rank = 1.0 if current_iv >= hv_high else 0.0
    else:
        rank = (current_iv - hv_low) / span
    rank = max(0.0, min(1.0, rank))

    below = sum(1 for v in hv if v <= current_iv)
    percentile = below / len(hv)

    return IVRank(
        ticker=ticker.upper(),
        current_iv=round(current_iv, 4),
        hv_low=round(hv_low, 4),
        hv_high=round(hv_high, 4),
        iv_rank=round(rank, 3),
        iv_percentile=round(percentile, 3),
        window_days=window,
        sample_size=len(hv),
    )
