"""Replay a priced strategy against past earnings moves (pure functions).

For each historical post-earnings move, decide if this strategy — with its
actual strikes and breakevens — would have finished profitable. This is an
approximation at expiry: it ignores early exits and IV path, but it turns
"iron condor" into "an iron condor at these strikes survived 9 of 11 past
reports", which is an honest, testable statement.
"""

from __future__ import annotations

import logging
from typing import List, Optional, Tuple

from .models import PricedStrategy

logger = logging.getLogger(__name__)


def _breakeven_moves(strategy: PricedStrategy, spot: float) -> List[float]:
    """Breakevens as percent moves from spot."""
    return [(b - spot) / spot * 100.0 for b in strategy.breakevens]


def is_win(strategy: PricedStrategy, spot: float, move_pct: float) -> Optional[bool]:
    """Would this strategy have finished profitable on a ``move_pct`` report?

    Uses the strategy's own breakevens where they exist. Returns ``None``
    when the strategy shape cannot be judged from a single move.
    """
    if spot <= 0:
        return None
    name = strategy.strategy.lower()
    moves = sorted(_breakeven_moves(strategy, spot))

    if "condor" in name:
        if len(moves) != 2:
            return None
        return moves[0] <= move_pct <= moves[1]
    if "straddle" in name or "strangle" in name:
        if len(moves) != 2:
            return None
        return move_pct <= moves[0] or move_pct >= moves[1]
    if "credit" in name:
        if len(moves) != 1:
            return None
        # Put credit wins above its breakeven; call credit wins below it.
        return move_pct >= moves[0] if "put" in name else move_pct <= moves[0]
    if "debit" in name:
        if len(moves) != 1:
            return None
        # Call debit needs the move above breakeven; put debit below.
        return move_pct >= moves[0] if "call" in name else move_pct <= moves[0]
    if "calendar" in name:
        # No closed-form breakeven; approximate with "the stock stayed
        # inside the front-month implied move" (the pin the trade wants).
        legs = strategy.legs
        if not legs:
            return None
        strike_move = (legs[0].strike - spot) / spot * 100.0
        width = abs(-strategy.net_premium / spot * 100.0) * 4  # rough pin zone
        return abs(move_pct - strike_move) <= max(width, 2.0)
    return None


def backtest_strategy(
    strategy: PricedStrategy, spot: float, past_moves: List[float]
) -> Optional[Tuple[int, int]]:
    """Return ``(wins, total)`` across past moves, or ``None`` if unjudgeable."""
    results = [is_win(strategy, spot, m) for m in past_moves]
    known = [r for r in results if r is not None]
    if not known:
        return None
    return sum(1 for r in known if r), len(known)


def attach_backtests(
    strategies: List[PricedStrategy], spot: float, past_moves: List[float]
) -> List[PricedStrategy]:
    """Fill ``backtest_wins``/``backtest_total`` on each strategy in place."""
    if not past_moves or spot <= 0:
        return strategies
    for s in strategies:
        result = backtest_strategy(s, spot, past_moves)
        if result is not None:
            s.backtest_wins, s.backtest_total = result
    return strategies
