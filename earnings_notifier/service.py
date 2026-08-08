"""Orchestration: wire the ticker source, earnings provider, selection,
formatting, and notifier together into one run."""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass
from typing import List, Optional

from .config import Config
from .earnings import (
    EarningsEvent,
    EarningsProvider,
    YFinanceProvider,
    collect_upcoming,
    select_for_notification,
)
from .formatting import render_email
from .notifier import EmailNotifier
from .sp500 import get_sp500_tickers, normalize_ticker

logger = logging.getLogger(__name__)


@dataclass
class RunResult:
    total_tickers: int
    resolved: int
    selected: List[EarningsEvent]
    notified: bool
    subject: str


def run(
    config: Config,
    *,
    today: Optional[dt.date] = None,
    provider: Optional[EarningsProvider] = None,
    tickers: Optional[List[str]] = None,
) -> RunResult:
    """Execute one notification cycle.

    ``today``, ``provider`` and ``tickers`` are injectable for testing; in
    production they default to the real date, a :class:`YFinanceProvider`, and
    the live S&P 500 roster.
    """
    config.validate()
    today = today or dt.date.today()

    if tickers is None:
        tickers = get_sp500_tickers()
    if config.ticker_limit and config.ticker_limit > 0:
        tickers = tickers[: config.ticker_limit]
        logger.info("Limiting to first %d tickers", config.ticker_limit)

    # Append any watchlist tickers (e.g. names outside the S&P 500), deduped.
    if config.extra_tickers:
        seen = set(tickers)
        added = []
        for raw in config.extra_tickers:
            t = normalize_ticker(raw)
            if t not in seen:
                seen.add(t)
                added.append(t)
        tickers = list(tickers) + added
        logger.info("Added %d watchlist ticker(s): %s", len(added), ", ".join(added))

    if provider is None:
        provider = YFinanceProvider(today=today)

    logger.info("Fetching earnings dates for %d tickers...", len(tickers))
    events = collect_upcoming(tickers, provider, max_workers=config.max_workers)

    selected = select_for_notification(
        events, today, lead_days=config.lead_days, window_days=config.window_days
    )
    logger.info(
        "%d compan(ies) report earnings in the target window", len(selected)
    )

    subject, text_body, html_body = render_email(selected, today, config.lead_days)

    notifier = EmailNotifier(config)
    notifier.send(subject, text_body, html_body)

    return RunResult(
        total_tickers=len(tickers),
        resolved=len(events),
        selected=selected,
        notified=not config.dry_run,
        subject=subject,
    )
