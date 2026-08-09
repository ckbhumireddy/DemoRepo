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
    enrich_events,
    select_for_notification,
)
from .formatting import render_email
from .notifier import EmailNotifier
from .sp500 import get_sp500_tickers, normalize_ticker
from .state import event_key, load_state, save_state

logger = logging.getLogger(__name__)


@dataclass
class RunResult:
    total_tickers: int
    resolved: int
    in_window: List[EarningsEvent]   # everything within the look-ahead window
    new_events: List[EarningsEvent]  # window minus already-notified (what we email)
    notified: bool                   # True if an email was actually sent
    subject: str

    # Backwards-compatible alias: the events the digest was built from.
    @property
    def selected(self) -> List[EarningsEvent]:
        return self.new_events


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

    in_window = select_for_notification(
        events, today, lead_days=config.lead_days, min_days=config.min_days
    )
    logger.info(
        "%d compan(ies) report earnings within the next %d day(s)",
        len(in_window),
        config.lead_days,
    )

    # De-duplicate against what we've already emailed so each earnings goes out
    # only once, even though it stays in the window for several days. Dry runs
    # skip the filter (and never write state below) so a preview always shows
    # the full window, already-notified events included.
    known: set = set()
    if config.use_state and not config.dry_run:
        known = load_state(config.state_file)
    new_events = [e for e in in_window if event_key(e) not in known]
    suppressed = len(in_window) - len(new_events)
    if suppressed:
        logger.info("Suppressed %d already-notified event(s)", suppressed)

    # Add company name, price, 52-week range, market cap, and last-earnings
    # results — only for the few events actually going into the email.
    if new_events:
        new_events = enrich_events(new_events, provider, max_workers=config.max_workers)

    subject, text_body, html_body = render_email(new_events, today, config.lead_days)

    # Skip the email entirely when nothing new is due (unless send_empty).
    sent = False
    if new_events or config.send_empty:
        EmailNotifier(config).send(subject, text_body, html_body)
        sent = not config.dry_run
    else:
        logger.info("Nothing new to report; no email sent")

    # Record what we just notified — but never in dry-run (it would suppress
    # the real email later) and only if state is enabled.
    if config.use_state and not config.dry_run and new_events:
        known.update(event_key(e) for e in new_events)
        save_state(
            config.state_file, known, today,
            retention_days=config.state_retention_days,
        )

    return RunResult(
        total_tickers=len(tickers),
        resolved=len(events),
        in_window=in_window,
        new_events=new_events,
        notified=sent,
        subject=subject,
    )
