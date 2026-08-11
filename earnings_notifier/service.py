"""Orchestration: wire the ticker source, earnings provider, selection,
formatting, and notifier together into one run."""

from __future__ import annotations

import dataclasses
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
    sort_by_market_cap,
)
from .formatting import render_email
from .notifier import EmailNotifier
from .sp500 import get_sp500_tickers, normalize_ticker
from .state import event_key, load_state, save_state
from .window import write_window_file

logger = logging.getLogger(__name__)


@dataclass
class RunResult:
    total_tickers: int
    resolved: int
    in_window: List[EarningsEvent]   # everything within the look-ahead window
    new_events: List[EarningsEvent]  # events emailed for the first time (NEW badge)
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

    # Hand the full window (pre-dedupe) to downstream services such as the
    # earnings analyzer. A data artifact, not notification state, so it is
    # written even in dry-run.
    if config.window_file:
        write_window_file(config.window_file, in_window, today, config.lead_days)

    # The digest covers the WHOLE window on every run — a company repeats
    # daily until its earnings day passes. State no longer suppresses events;
    # it only marks which ones appear for the first time (the NEW badge).
    # Dry runs skip the state read, so a preview marks everything as new.
    known: set = set()
    if config.use_state and not config.dry_run:
        known = load_state(config.state_file)
    new_events = [e for e in in_window if event_key(e) not in known]
    if new_events:
        logger.info("%d event(s) appear for the first time", len(new_events))

    # Add company name, price, 52-week range, market cap, and last-earnings
    # results, mark the first-time events, then order the digest by company
    # size, biggest names first.
    digest_events = in_window
    if digest_events:
        digest_events = enrich_events(
            digest_events, provider, max_workers=config.max_workers
        )
        new_keys = {event_key(e) for e in new_events}
        digest_events = [
            dataclasses.replace(e, first_notice=event_key(e) in new_keys)
            for e in digest_events
        ]
        digest_events = sort_by_market_cap(digest_events)

    subject, text_body, html_body = render_email(
        digest_events, today, config.lead_days
    )

    # Skip the email only when the window is empty (unless send_empty).
    sent = False
    if digest_events or config.send_empty:
        EmailNotifier(config).send(subject, text_body, html_body)
        sent = not config.dry_run
    else:
        logger.info("Window is empty; no email sent")

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
