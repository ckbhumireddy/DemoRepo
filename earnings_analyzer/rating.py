"""Composite 0-100 rating for an earnings setup (pure functions).

Five weighted components; a component whose inputs are missing contributes
nothing and its weight is redistributed pro-rata across the rest, with the
fraction of weight backed by real data reported as ``coverage``. Untradeable
options and thin data cap the score — a setup can't rate highly on missing
information.
"""

from __future__ import annotations

import math
from typing import Dict, Optional

from .models import (
    EarningsHistoryStats,
    ImpliedMove,
    IVRank,
    LiquidityReport,
    Rating,
    ReactionStats,
    TrendContext,
)

WEIGHTS = {
    "earnings_quality": 25.0,
    "vol_edge": 25.0,
    "liquidity": 20.0,
    "reaction_consistency": 15.0,
    "trend_alignment": 15.0,
}

# Score caps (see module docstring).
ILLIQUID_CAP = 64.0    # max grade C when options are untradeable
LOW_COVERAGE_CAP = 59.0


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def _earnings_quality(history: Optional[EarningsHistoryStats]) -> Optional[float]:
    if history is None or history.beat_rate is None:
        return None
    beat = history.beat_rate * 100.0
    surprise = _clamp01((max(-10.0, min(10.0, history.avg_surprise_pct or 0.0)) + 10) / 20) * 100
    streak = _clamp01((max(-4, min(4, history.streak)) + 4) / 8) * 100
    return 0.60 * beat + 0.25 * surprise + 0.15 * streak


def _vol_edge(implied: Optional[ImpliedMove], iv_rank: Optional[IVRank]) -> Optional[float]:
    if implied is None or implied.ratio is None:
        return None
    # Symmetric mispricing: very rich or very cheap both mean edge; 1.0 = none.
    mispricing = _clamp01(abs(math.log(implied.ratio)) / math.log(1.6)) * 100
    confirmation = 50.0
    if iv_rank is not None:
        agrees = (implied.verdict == "rich" and iv_rank.iv_rank >= 0.7) or (
            implied.verdict == "cheap" and iv_rank.iv_rank <= 0.3
        )
        confirmation = 100.0 if agrees else 50.0
    score = 0.70 * mispricing + 0.30 * confirmation
    if implied.diluted:
        score = min(score, 50.0)
    return score


def _liquidity(report: Optional[LiquidityReport]) -> Optional[float]:
    if report is None:
        return None
    if not report.tradeable:
        return 0.0  # absence of liquidity is information, not missing data
    spread = report.atm_spread_pct
    spread_score = _clamp01((10.0 - (spread if spread is not None else 10.0)) / 8.0) * 100
    oi = report.atm_open_interest or 0.0
    oi_score = _clamp01(math.log10(oi + 1) / 3.0) * 100
    vol = report.atm_volume or 0.0
    vol_score = _clamp01(vol / 500.0) * 100
    return 0.50 * spread_score + 0.30 * oi_score + 0.20 * vol_score


def _reaction_consistency(reactions: Optional[ReactionStats]) -> Optional[float]:
    if reactions is None or reactions.up_rate is None:
        return None
    directionality = abs(reactions.up_rate - 0.5) * 2 * 100
    worst = abs(reactions.worst_pct or 0.0)
    tail_safety = (1 - _clamp01(worst / 25.0)) * 100
    return 0.60 * directionality + 0.40 * tail_safety


def _trend_alignment(trend: Optional[TrendContext]) -> Optional[float]:
    if trend is None:
        return None
    score = 0.0
    for ma in (trend.ma20, trend.ma50, trend.ma200):
        if ma is not None and trend.price > ma:
            score += 25.0
    if trend.pct_of_52wk_range is not None:
        score += 25.0 * trend.pct_of_52wk_range
    return score


def compute_rating(
    history: Optional[EarningsHistoryStats],
    reactions: Optional[ReactionStats],
    implied: Optional[ImpliedMove],
    iv_rank: Optional[IVRank],
    liquidity: Optional[LiquidityReport],
    trend: Optional[TrendContext],
) -> Rating:
    subscores: Dict[str, Optional[float]] = {
        "earnings_quality": _earnings_quality(history),
        "vol_edge": _vol_edge(implied, iv_rank),
        "liquidity": _liquidity(liquidity),
        "reaction_consistency": _reaction_consistency(reactions),
        "trend_alignment": _trend_alignment(trend),
    }
    available = {k: v for k, v in subscores.items() if v is not None}
    total_weight = sum(WEIGHTS[k] for k in available)
    coverage = total_weight / sum(WEIGHTS.values())

    if not available:
        return Rating(score=0.0, grade="F", components={}, coverage=0.0)

    score = sum(WEIGHTS[k] * v for k, v in available.items()) / total_weight

    if liquidity is not None and not liquidity.tradeable:
        score = min(score, ILLIQUID_CAP)
    if coverage < 0.5:
        score = min(score, LOW_COVERAGE_CAP)

    return Rating(
        score=round(score, 1),
        grade=_grade(score),
        components={k: round(v, 1) for k, v in available.items()},
        coverage=round(coverage, 2),
    )


def _grade(score: float) -> str:
    if score >= 90:
        return "A+"
    if score >= 80:
        return "A"
    if score >= 65:
        return "B"
    if score >= 50:
        return "C"
    if score >= 35:
        return "D"
    return "F"
