"""Per-ticker orchestration: fetch everything, run the pure analysis."""

from __future__ import annotations

import datetime as dt
import logging

from .backtest import attach_backtests
from .config import AnalyzerConfig
from .history import (
    attach_reactions,
    compute_history_stats,
    compute_reaction_stats,
    reactions_from_bars,
)
from .implied_move import compute_implied_move, select_event_expiry
from .liquidity import screen_liquidity
from .models import TickerAnalysis
from .provider import MarketDataProvider
from .rating import compute_rating
from .strategies import select_expiry, suggest_strategies
from .trend import compute_trend
from .volatility import compute_iv_rank

logger = logging.getLogger(__name__)

# The calendar spread's long leg targets ~30-45 days past the event.
BACK_MIN_DTE = 30
BACK_MAX_DTE = 45


def analyze_ticker(
    ticker: str,
    event_date: dt.date,
    provider: MarketDataProvider,
    config: AnalyzerConfig,
    today: dt.date,
) -> TickerAnalysis:
    """Build the full :class:`TickerAnalysis` for one upcoming report.

    Every data source is optional: whatever is missing leaves its analysis
    block ``None`` and the rating reweights around it.
    """
    quarters = provider.earnings_history(ticker, limit=20)
    bars = provider.price_history(ticker, days=700)

    # Reactions for past quarters, from the single price-history fetch.
    pending = [q.date for q in quarters if q.reaction_pct is None]
    if pending and bars:
        quarters = attach_reactions(quarters, reactions_from_bars(bars, pending))
    recent = sorted(quarters, key=lambda q: q.date, reverse=True)
    recent = recent[: config.history_quarters]

    history = compute_history_stats(recent, config.history_quarters)
    reactions = compute_reaction_stats(recent)
    trend = compute_trend(bars, streak=history.streak if history else 0)
    snapshot = provider.snapshot(ticker)

    # Options: the chain at the first expiry spanning the event.
    expiries = provider.option_expiries(ticker)
    event_expiry = select_event_expiry(expiries, event_date)
    event_chain = (
        provider.option_chain(ticker, event_expiry) if event_expiry else None
    )

    spot = None
    if event_chain is not None and event_chain.underlying_price > 0:
        spot = event_chain.underlying_price
    elif trend is not None:
        spot = trend.price

    implied = None
    iv_rank = None
    liquidity = None
    if event_chain is not None and spot:
        implied = compute_implied_move(
            event_chain,
            spot,
            event_date,
            hist_avg_abs_move=reactions.avg_abs_move_pct if reactions else None,
            rich_threshold=config.rich_threshold,
            cheap_threshold=config.cheap_threshold,
        )
        iv_rank = compute_iv_rank(ticker, event_chain.atm_iv(), bars)
        liquidity = screen_liquidity(
            event_chain,
            min_open_interest=config.min_open_interest,
            min_option_volume=config.min_option_volume,
            max_spread_pct=config.max_spread_pct,
        )

    # The calendar's back-month chain, fetched only when it can be used.
    back_chain = None
    if (
        implied is not None
        and implied.verdict == "cheap"
        and (trend is None or trend.bias == "neutral")
    ):
        back_expiry = select_expiry(
            expiries, event_date, min_dte=BACK_MIN_DTE, max_dte=BACK_MAX_DTE
        )
        if back_expiry and back_expiry != event_expiry:
            back_chain = provider.option_chain(ticker, back_expiry)

    rating = compute_rating(history, reactions, implied, iv_rank, liquidity, trend)
    strategies = (
        suggest_strategies(implied, trend, liquidity, event_chain, back_chain, spot)
        if spot
        else []
    )
    # Replay each suggestion against the past reports it would have faced.
    if strategies and spot:
        past_moves = [q.reaction_pct for q in recent if q.reaction_pct is not None]
        attach_backtests(strategies, spot, past_moves)

    return TickerAnalysis(
        ticker=ticker,
        event_date=event_date,
        price=spot,
        snapshot=snapshot,
        history=history,
        reactions=reactions,
        implied=implied,
        iv_rank=iv_rank,
        liquidity=liquidity,
        trend=trend,
        rating=rating,
        strategies=strategies,
    )
