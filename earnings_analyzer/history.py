"""Earnings-history and post-earnings-reaction statistics (pure functions)."""

from __future__ import annotations

import datetime as dt
from typing import Dict, List, Optional

from .models import EarningsHistoryStats, PriceBar, QuarterResult, ReactionStats


def reactions_from_bars(
    bars: List[PriceBar], earnings_dates: List[dt.date]
) -> Dict[dt.date, float]:
    """Percent price move across each earnings report.

    For every earnings date, compares the last close *before* it with the
    first close *after* it (same bracketing as the notifier's
    ``_post_earnings_move``), computed locally over one price-history fetch
    instead of one request per quarter. Dates the bars can't bracket are
    omitted.
    """
    closes = sorted((b.day, b.close) for b in bars if b.close > 0)
    out: Dict[dt.date, float] = {}
    for ed in earnings_dates:
        before = [c for d, c in closes if d < ed]
        after = [c for d, c in closes if d > ed]
        if before and after and before[-1] > 0:
            out[ed] = (after[0] - before[-1]) / before[-1] * 100.0
    return out


def attach_reactions(
    quarters: List[QuarterResult], reactions: Dict[dt.date, float]
) -> List[QuarterResult]:
    """Fill in ``reaction_pct`` on quarters whose date has a computed move."""
    import dataclasses

    out = []
    for q in quarters:
        if q.reaction_pct is None and q.date in reactions:
            q = dataclasses.replace(q, reaction_pct=reactions[q.date])
        out.append(q)
    return out


def _surprise(q: QuarterResult) -> Optional[float]:
    """Surprise %, computed from the EPS pair when Yahoo omits it."""
    if q.surprise_pct is not None:
        return q.surprise_pct
    if q.eps_actual is not None and q.eps_estimate:
        return (q.eps_actual - q.eps_estimate) / abs(q.eps_estimate) * 100.0
    return None


def compute_history_stats(
    quarters: List[QuarterResult], max_quarters: int = 12
) -> Optional[EarningsHistoryStats]:
    """Beat rate, average surprise, and beat/miss streak (newest first).

    Only quarters with a computable surprise count toward the statistics.
    """
    recent = sorted(quarters, key=lambda q: q.date, reverse=True)[:max_quarters]
    surprises = [(q, _surprise(q)) for q in recent]
    surprises = [(q, s) for q, s in surprises if s is not None]
    if not surprises:
        return None

    beats = sum(1 for _, s in surprises if s >= 0)
    streak = 0
    for _, s in surprises:  # newest first
        if s >= 0:
            if streak < 0:
                break
            streak += 1
        else:
            if streak > 0:
                break
            streak -= 1

    return EarningsHistoryStats(
        quarters=len(surprises),
        beat_rate=beats / len(surprises),
        avg_surprise_pct=sum(s for _, s in surprises) / len(surprises),
        streak=streak,
    )


def compute_reaction_stats(
    quarters: List[QuarterResult], min_samples: int = 4
) -> Optional[ReactionStats]:
    """Distribution of past post-earnings moves; ``None`` below min_samples."""
    moves = [q.reaction_pct for q in quarters if q.reaction_pct is not None]
    if len(moves) < min_samples:
        return None
    return ReactionStats(
        samples=len(moves),
        avg_abs_move_pct=sum(abs(m) for m in moves) / len(moves),
        up_rate=sum(1 for m in moves if m >= 0) / len(moves),
        best_pct=max(moves),
        worst_pct=min(moves),
    )
