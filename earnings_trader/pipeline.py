"""Candidate -> TradeDecision: the trader's own analysis pipeline."""

from __future__ import annotations

import concurrent.futures
import datetime as dt
import logging
from dataclasses import dataclass, field
from typing import List, Optional

from earnings_analyzer.models import (
    ImpliedMove,
    IVRank,
    LiquidityReport,
    PricedStrategy,
    TrendContext,
)
from earnings_analyzer.provider import MarketDataProvider

from .analysis import (
    attach_reactions,
    compute_implied_move,
    compute_iv_rank,
    compute_reaction_stats,
    compute_streak,
    compute_trend,
    reactions_from_bars,
    screen_liquidity,
    select_event_expiry,
)
from .candidates import Candidate
from .strategies import build_strategy

logger = logging.getLogger(__name__)


@dataclass
class TradeDecision:
    candidate: Candidate
    action: str                      # "open" | "skip"
    reasons: List[str] = field(default_factory=list)   # why skipped
    spot: Optional[float] = None
    implied: Optional[ImpliedMove] = None
    iv_rank: Optional[IVRank] = None
    liquidity: Optional[LiquidityReport] = None
    trend: Optional[TrendContext] = None
    strategy: Optional[PricedStrategy] = None


def _skip(decision: TradeDecision, reason: str) -> TradeDecision:
    decision.action = "skip"
    decision.reasons.append(reason)
    return decision


def evaluate_candidate(
    candidate: Candidate,
    provider: MarketDataProvider,
    config,
    today: dt.date,
) -> TradeDecision:
    """Analyze one candidate and decide open/skip with reasons."""
    d = TradeDecision(candidate=candidate, action="open")
    ticker = candidate.ticker

    # Reaction history: the baseline the implied move is judged against.
    quarters = provider.earnings_history(ticker, limit=20)
    bars = provider.price_history(ticker, days=700)
    if quarters:
        moves = reactions_from_bars(bars, [q.date for q in quarters])
        quarters = attach_reactions(quarters, moves)
    recent = sorted(quarters, key=lambda q: q.date, reverse=True)
    recent = recent[: config.history_quarters]
    reactions = compute_reaction_stats(recent)
    streak = compute_streak(quarters, config.history_quarters)
    d.trend = compute_trend(bars, streak=streak)

    # The chain at the first expiry spanning the event.
    expiries = provider.option_expiries(ticker)
    event_expiry = select_event_expiry(expiries, candidate.event_date)
    chain = provider.option_chain(ticker, event_expiry) if event_expiry else None
    if chain is None:
        return _skip(d, "no option chain spanning the event")
    d.spot = (
        chain.underlying_price
        if chain.underlying_price > 0
        else (d.trend.price if d.trend else None)
    )
    if not d.spot:
        return _skip(d, "no usable spot price")

    d.liquidity = screen_liquidity(
        chain,
        min_open_interest=config.min_open_interest,
        min_option_volume=config.min_option_volume,
        max_spread_pct=config.max_spread_pct,
    )
    if not d.liquidity.tradeable:
        d.action = "skip"
        d.reasons.extend(d.liquidity.reasons)
        return d

    d.iv_rank = compute_iv_rank(ticker, chain.atm_iv(), bars)
    d.implied = compute_implied_move(
        chain,
        d.spot,
        candidate.event_date,
        reactions.avg_abs_move_pct if reactions else None,
        rich_threshold=config.rich_threshold,
        cheap_threshold=config.cheap_threshold,
    )
    if d.implied is None or d.implied.diluted or d.implied.verdict == "unknown":
        return _skip(d, "no reliable implied-move comparison")
    if d.implied.verdict == "fair":
        return _skip(d, "fair vol — no edge")

    bias = d.trend.bias if d.trend else "neutral"
    d.strategy = build_strategy(
        d.implied.verdict, bias, chain, d.spot, d.implied.implied_move_pct / 100.0
    )
    if d.strategy is None or d.strategy.max_loss is None or d.strategy.max_loss <= 0:
        return _skip(d, "could not price a defined-risk strategy")
    return d


def evaluate_all(
    candidates: List[Candidate],
    provider: MarketDataProvider,
    config,
    today: dt.date,
) -> List[TradeDecision]:
    """Evaluate every candidate; one failing ticker never kills the run."""
    def _one(candidate: Candidate) -> TradeDecision:
        try:
            return evaluate_candidate(candidate, provider, config, today)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Evaluation failed for %s: %s", candidate.ticker, exc)
            return TradeDecision(
                candidate=candidate, action="skip",
                reasons=[f"evaluation error: {exc}"],
            )

    if not candidates:
        return []
    workers = max(1, min(config.trader_max_workers, len(candidates)))
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(_one, candidates))
