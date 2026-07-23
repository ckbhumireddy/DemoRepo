"""Detecting post-earnings crashes from price history.

The idea: take the last earnings date and measure how the stock reacted. We
capture three things -- the immediate reaction, the worst point (trough) in a
short window afterward, and where the stock sits now relative to before earnings.
A qualifying "crash" is a drop past a threshold that happened recently enough to
still be actionable.
"""

from __future__ import annotations

from datetime import date
from typing import List, Optional

from ..config import ScreenConfig
from ..data.models import EarningsCrash, PriceBar


def detect_earnings_crash(
    ticker: str,
    prices: List[PriceBar],
    earnings_date: Optional[date],
    cfg: ScreenConfig,
    *,
    today: Optional[date] = None,
) -> Optional[EarningsCrash]:
    """Return an :class:`EarningsCrash` if the stock qualifies, else ``None``.

    ``prices`` must be sorted ascending by day. ``today`` defaults to the last
    bar's date, keeping the function deterministic and testable offline.
    """
    if earnings_date is None or len(prices) < 2:
        return None

    prices = sorted(prices, key=lambda b: b.day)
    if today is None:
        today = prices[-1].day

    days_since = (today - earnings_date).days
    if days_since < 0 or days_since > cfg.crash_lookback_days:
        return None

    pre_bars = [b for b in prices if b.day < earnings_date]
    post_bars = [b for b in prices if b.day >= earnings_date]
    if not pre_bars or not post_bars:
        return None

    pre_close = pre_bars[-1].close
    reaction_close = post_bars[0].close
    if pre_close <= 0:
        return None

    # Trough within the post-earnings window (in trading days).
    window = post_bars[: max(1, cfg.post_earnings_window_days)]
    trough_bar = min(window, key=lambda b: b.close)
    trough_close = trough_bar.close
    current_close = prices[-1].close

    reaction_drop = (reaction_close - pre_close) / pre_close
    max_drop = (trough_close - pre_close) / pre_close
    still_down = (current_close - pre_close) / pre_close

    # Must have dropped past the threshold at some point in the window.
    if max_drop > -cfg.min_crash_pct:
        return None

    if cfg.require_still_depressed and still_down >= 0:
        return None

    return EarningsCrash(
        ticker=ticker,
        earnings_date=earnings_date,
        pre_close=round(pre_close, 4),
        reaction_close=round(reaction_close, 4),
        trough_close=round(trough_close, 4),
        current_close=round(current_close, 4),
        reaction_drop_pct=round(reaction_drop, 4),
        max_drop_pct=round(max_drop, 4),
        still_down_pct=round(still_down, 4),
        days_since_earnings=days_since,
    )
