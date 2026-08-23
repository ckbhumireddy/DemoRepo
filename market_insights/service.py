"""Orchestration: universe -> quotes -> candidates -> analysis -> email."""

from __future__ import annotations

import datetime as dt
import json
import logging
import os
from dataclasses import dataclass
from typing import Optional

from earnings_notifier.notifier import EmailNotifier
from iv_scanner.scanner import RateGate

from .config import MarketInsightsConfig
from .emails import render_insights_email
from .scanner import (
    RATE_INTERVAL,
    annotate,
    fetch_quotes,
    make_tradestation_fetch,
    make_yahoo_fetch,
    prefilter,
    select_unusual,
    sweep,
)
from .tradestation import build_session
from .volume import session_fraction

logger = logging.getLogger(__name__)

DEGRADED_NOTE = (
    "TradeStation market data unavailable — scanned only your portfolio and "
    "watchlist via the delayed fallback feed instead of the full S&P 500."
)

# A quote pass that returns nothing must not be reported as a quiet market:
# "nothing unusual" and "nothing was screened" look identical in the inbox.
QUOTES_FAILED_NOTE = (
    "TradeStation returned no quotes for the universe — the feed looks down, "
    "so nothing could be screened. This is not a quiet market."
)


@dataclass
class InsightsRunResult:
    universe: int
    candidates: int
    analyzed: int
    unusual: int
    emails_sent: int
    subject: Optional[str] = None


def _held_tickers(config: MarketInsightsConfig) -> set:
    from portfolio_insights.portfolio import load_portfolio

    snapshot = load_portfolio(config.portfolio_json, config.portfolio_file)
    if snapshot.fetch_error:
        logger.info("No portfolio for highlights (unavailable)")
        return set()
    return {p.ticker for p in snapshot.positions}


def _already_sent_today(state_file: str, today: dt.date) -> bool:
    if not state_file or not os.path.exists(state_file):
        return False
    try:
        with open(state_file, "r", encoding="utf-8") as fh:
            return json.load(fh).get("last_sent") == today.isoformat()
    except (json.JSONDecodeError, OSError):
        return False


def _mark_sent(state_file: str, today: dt.date) -> None:
    directory = os.path.dirname(state_file)
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(state_file, "w", encoding="utf-8") as fh:
        json.dump({"last_sent": today.isoformat()}, fh)


def run_insights(
    config: MarketInsightsConfig,
    *,
    today: Optional[dt.date] = None,
    now: Optional[dt.datetime] = None,
    tickers=None,
    fetch=None,
    held: Optional[set] = None,
    fraction: Optional[float] = None,
) -> InsightsRunResult:
    """Execute one scan. Data sources are injectable for testing."""
    config.validate()
    today = today or dt.date.today()

    # Once-per-day guard (an external trigger and the cron fallback can both
    # fire for the same session).
    if _already_sent_today(config.insights_state_file, today):
        logger.info("Insights email already sent today; skipping")
        return InsightsRunResult(0, 0, 0, 0, 0)

    if held is None:
        held = _held_tickers(config)
    if fraction is None:
        fraction = session_fraction(now)

    watchlist = {t.upper() for t in config.extra_tickers}
    always = set(held) | watchlist
    scan_note = None
    universe_size = 0

    if tickers is None or fetch is None:
        session = build_session(config)
        if config.insights_universe == "portfolio":
            universe = sorted(always)
        else:
            from earnings_notifier.sp500 import get_sp500_tickers

            universe = list(dict.fromkeys(get_sp500_tickers() + sorted(always)))
        universe_size = len(universe)

        if session is not None:
            gate = RateGate(RATE_INTERVAL)
            quotes = fetch_quotes(session, universe, gate)
            candidates = prefilter(
                quotes,
                fraction,
                config.insights_prefilter_multiple,
                always=always,
                min_price=config.insights_min_price,
                limit=config.insights_max_candidates,
            )
            logger.info(
                "Quoted %d name(s); %d candidate(s) for history",
                len(quotes), len(candidates),
            )
            if universe and not quotes:
                logger.error("quote pass returned nothing for %d ticker(s)",
                             len(universe))
                scan_note = QUOTES_FAILED_NOTE
            tickers = tickers or candidates
            fetch = fetch or make_tradestation_fetch(session, gate, fraction, quotes)
        else:
            # Degraded: a 500-ticker Yahoo sweep would be rate-limited into
            # uselessness, so scan only what the user named.
            from earnings_analyzer.provider import YFinanceMarketData

            tickers = tickers or sorted(always)
            universe_size = len(tickers)
            fetch = fetch or make_yahoo_fetch(
                YFinanceMarketData(today=today), today, fraction
            )
            scan_note = DEGRADED_NOTE
    else:
        universe_size = len(tickers)

    rows, _failures = sweep(tickers, fetch, max_workers=config.insights_max_workers)
    annotate(rows, set(held))
    unusual = select_unusual(
        rows,
        min_rvol=config.insights_min_rvol,
        min_dollar_volume=config.insights_min_dollar_volume,
        min_price=config.insights_min_price,
        top_n=config.insights_top_n,
    )

    universe_note = (
        f"{len(unusual)} unusual of {len(rows)} analyzed"
        + (f", {universe_size} scanned" if universe_size else "")
    )
    subject, text, html_body = render_insights_email(
        unusual, today, universe_note=universe_note, scan_note=scan_note
    )

    # Nothing unusual is a legitimate result, but a daily "nothing happened"
    # email trains the reader to ignore the whole feed — send only when the
    # user has opted into empty reports.
    send = bool(unusual) or config.send_empty
    if send:
        EmailNotifier(config).send(subject, text, html_body)
    else:
        logger.info("Nothing unusual today; no email sent (set SEND_EMPTY=true "
                    "to receive it anyway)")

    if send and not config.dry_run and config.insights_state_file:
        _mark_sent(config.insights_state_file, today)

    return InsightsRunResult(
        universe=universe_size,
        candidates=len(tickers),
        analyzed=len(rows),
        unusual=len(unusual),
        emails_sent=1 if (send and not config.dry_run) else 0,
        subject=subject,
    )
