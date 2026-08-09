"""The market-implied earnings move, from the ATM straddle (pure functions)."""

from __future__ import annotations

import datetime as dt
from typing import List, Optional

from .models import ImpliedMove, OptionChain

# An expiry landing more than this many days past the event includes so much
# non-event time value that the implied-move comparison stops being honest.
DILUTION_DAYS = 10


def select_event_expiry(
    expiries: List[dt.date], event_date: dt.date
) -> Optional[dt.date]:
    """The nearest expiry on/after the event — where the event premium lives."""
    candidates = sorted(e for e in expiries if e >= event_date)
    return candidates[0] if candidates else None


def compute_implied_move(
    chain: OptionChain,
    spot: float,
    event_date: dt.date,
    hist_avg_abs_move: Optional[float],
    rich_threshold: float = 1.25,
    cheap_threshold: float = 0.80,
) -> Optional[ImpliedMove]:
    """ATM straddle debit / spot, compared against the historical move.

    Returns ``None`` when the chain can't price a straddle. The verdict is
    "unknown" (and strategies are suppressed downstream) when the expiry is
    diluted or there is no historical baseline.
    """
    if spot <= 0:
        return None
    call, put = chain.atm_call(), chain.atm_put()
    if call is None or put is None:
        return None
    if call.mid is None or put.mid is None or call.mid <= 0 or put.mid <= 0:
        return None

    straddle = call.mid + put.mid
    implied_pct = straddle / spot * 100.0
    diluted = (chain.expiry - event_date).days > DILUTION_DAYS

    ratio = None
    verdict = "unknown"
    if hist_avg_abs_move and hist_avg_abs_move > 0 and not diluted:
        ratio = implied_pct / hist_avg_abs_move
        if ratio >= rich_threshold:
            verdict = "rich"
        elif ratio <= cheap_threshold:
            verdict = "cheap"
        else:
            verdict = "fair"

    return ImpliedMove(
        expiry=chain.expiry,
        straddle_debit=round(straddle, 2),
        implied_move_pct=round(implied_pct, 2),
        historical_avg_abs_move_pct=hist_avg_abs_move,
        ratio=round(ratio, 2) if ratio is not None else None,
        verdict=verdict,
        diluted=diluted,
    )
