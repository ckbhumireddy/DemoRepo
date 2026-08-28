"""Orchestration: candidates -> bars -> strategy -> (backtest | signals).

Two verbs, one data path:

``backtest``
    Fetch history for the candidate universe, replay it through the
    strategy, score it, and record the verdict in the registry. This is the
    only way a strategy becomes eligible to emit live signals.
``scan``
    Evaluate the strategy on today's bars and email the signals — but only
    if the registry says the strategy's backtest passed and hasn't expired.
    An unapproved strategy scan still runs and prints (so you can watch a
    strategy paper-perform), it just refuses to email.

Candidate discovery is cheap on purpose: the distress screen's inputs
(price, 52-week high) are all in TradeStation's quote payload, so one
quotes sweep over the S&P 500 (~5 requests) finds the distressed names and
only those get a bar-history request, capped by ``swing_max_candidates``.
"""

from __future__ import annotations

import concurrent.futures
import datetime as dt
import logging
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence

from earnings_analyzer.models import PriceBar
from earnings_notifier.notifier import EmailNotifier
from iv_scanner.scanner import RateGate
from market_insights.scanner import RATE_INTERVAL, batched

from .backtest import BacktestResult, run_backtest
from .config import SwingConfig
from .emails import render_signals_email
from .registry import Registry
from .strategies.base import Signal, Strategy

logger = logging.getLogger(__name__)


@dataclass
class ScanRunResult:
    strategy: str
    approved: bool
    candidates: int
    signals: int
    emails_sent: int
    subject: Optional[str] = None
    approval_note: str = ""


def _quote_candidates(session, tickers: Sequence[str], gate: RateGate,
                      min_drawdown: float, min_price: float) -> List[str]:
    """Distressed names straight from the quote sweep, hottest fall first."""
    scored = []
    for batch in batched(list(tickers)):
        gate.wait()
        try:
            quotes = session.quotes(batch)
        except Exception as exc:  # noqa: BLE001
            logger.warning("quote batch of %d failed (%s)", len(batch), exc)
            continue
        for ticker, quote in quotes.items():
            last, high = quote.last, quote.high_52week
            if not last or not high or high <= 0 or last < min_price:
                continue
            drawdown = 1.0 - last / high
            if drawdown >= min_drawdown:
                scored.append((drawdown, ticker))
    scored.sort(key=lambda pair: (-pair[0], pair[1]))
    return [ticker for _, ticker in scored]


def _fetch_history(config: SwingConfig, session, tickers: Sequence[str],
                   gate: RateGate) -> Dict[str, List[PriceBar]]:
    """Daily bars per candidate, rate-paced; one bad ticker never kills a run."""
    def _one(ticker: str):
        gate.wait()
        try:
            bars, _partial = session.bars(ticker, config.swing_bars_back)
            return ticker, bars
        except Exception as exc:  # noqa: BLE001
            logger.debug("bars failed for %s (%s)", ticker, type(exc).__name__)
            return ticker, []

    history: Dict[str, List[PriceBar]] = {}
    if not tickers:
        return history
    workers = max(1, min(config.swing_max_workers, len(tickers)))
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        for ticker, bars in pool.map(_one, tickers):
            if bars:
                history[ticker] = bars
    logger.info("Fetched history for %d of %d candidate(s)",
                len(history), len(tickers))
    return history


def _yahoo_history(config: SwingConfig, tickers: Sequence[str],
                   today: Optional[dt.date]) -> Dict[str, List[PriceBar]]:
    """Degraded path: only the named watchlist, via the fallback feed."""
    from earnings_analyzer.provider import YFinanceMarketData

    provider = YFinanceMarketData(today=today)
    history: Dict[str, List[PriceBar]] = {}
    for ticker in tickers:
        bars = provider.price_history(ticker, days=config.swing_bars_back + 120)
        if bars:
            history[ticker] = bars
    return history


def gather_history(
    config: SwingConfig,
    strategy: Strategy,
    *,
    today: Optional[dt.date] = None,
) -> tuple:
    """(history, note): candidate discovery + bars, or the degraded fallback."""
    from market_insights.tradestation import build_session

    session = build_session(config)
    watchlist = [t.upper() for t in config.extra_tickers]
    if session is not None:
        from earnings_notifier.sp500 import get_sp500_tickers

        universe = list(dict.fromkeys(get_sp500_tickers() + watchlist))
        gate = RateGate(RATE_INTERVAL)
        candidates = _quote_candidates(
            session, universe, gate,
            getattr(strategy, "min_drawdown", 0.30),
            getattr(strategy, "min_price", 10.0),
        )
        kept = candidates[:config.swing_max_candidates]
        note = (
            f"{len(kept)} distressed candidate(s) from {len(universe)} quoted"
            + (f" (capped from {len(candidates)})"
               if len(candidates) > len(kept) else "")
        )
        return _fetch_history(config, session, kept, gate), note

    if not watchlist:
        return {}, (
            "TradeStation unavailable and no EXTRA_TICKERS watchlist — "
            "nothing to scan."
        )
    return _yahoo_history(config, watchlist, today), (
        f"TradeStation unavailable — scanned only the {len(watchlist)}-name "
        "watchlist via the delayed fallback feed."
    )


# --------------------------------------------------------------------------- #
# The two verbs.
# --------------------------------------------------------------------------- #
def run_strategy_backtest(
    config: SwingConfig,
    *,
    today: Optional[dt.date] = None,
    history: Optional[Dict[str, List[PriceBar]]] = None,
) -> tuple:
    """Backtest the configured strategy and record the verdict.

    Returns (BacktestResult, Record).
    """
    config.validate()
    strategy = config.build_strategy()
    note = ""
    if history is None:
        history, note = gather_history(config, strategy, today=today)
    if note:
        logger.info("%s", note)
    result: BacktestResult = run_backtest(
        strategy, history, max_hold=config.swing_max_hold
    )
    registry = Registry(config.swing_registry_file, config.swing_approval_ttl_days)
    record = registry.record(result, config.thresholds(), today=today)
    return result, record


def run_scan(
    config: SwingConfig,
    *,
    today: Optional[dt.date] = None,
    history: Optional[Dict[str, List[PriceBar]]] = None,
) -> ScanRunResult:
    """Evaluate today's bars; email only if the strategy is approved."""
    config.validate()
    today = today or dt.date.today()
    strategy = config.build_strategy()
    registry = Registry(config.swing_registry_file, config.swing_approval_ttl_days)
    approved = registry.is_approved(strategy.name, today)
    approval_note = registry.why_not(strategy.name, today)

    note = ""
    if history is None:
        history, note = gather_history(config, strategy, today=today)

    signals: List[Signal] = []
    for ticker, bars in sorted(history.items()):
        try:
            signal = strategy.evaluate(ticker, bars)
        except Exception as exc:  # noqa: BLE001
            logger.debug("evaluate failed for %s (%s)", ticker, exc)
            continue
        if signal is not None:
            signals.append(signal)
    signals.sort(key=lambda s: -s.rr)
    signals = signals[:config.swing_top_n]

    subject, text, html_body = render_signals_email(
        signals, today,
        strategy_name=strategy.name,
        approval_note=approval_note,
        universe_note=note,
    )

    emails_sent = 0
    if not approved:
        # The whole point of the gate: signals from an unapproved strategy
        # are shown to the operator, never mailed as if they were live.
        logger.warning(
            "Strategy %s is NOT approved for practice (%s); printing "
            "signals instead of emailing", strategy.name, approval_note,
        )
        logger.info("\n%s", text)
    elif signals or config.send_empty:
        EmailNotifier(config).send(subject, text, html_body)
        emails_sent = 0 if config.dry_run else 1
    else:
        logger.info("No setups today; no email sent (SEND_EMPTY=true to "
                    "receive empty reports)")

    return ScanRunResult(
        strategy=strategy.name,
        approved=approved,
        candidates=len(history),
        signals=len(signals),
        emails_sent=emails_sent,
        subject=subject,
        approval_note=approval_note,
    )
