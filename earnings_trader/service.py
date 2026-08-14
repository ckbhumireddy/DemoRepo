"""Orchestration: window -> candidates -> decisions -> ledger -> emails.

Two phases per weekday:

- ``morning`` (after the open): close due positions (one email per close),
  evaluate today's candidates as a tentative preview, send the daily plan +
  performance email.
- ``entry`` (near the close): safety-net close of leftovers, re-evaluate at
  live quotes, open positions (one email per open). Only this phase opens,
  so a missed entry run means missed trades, never late ones.

The ledger is saved BEFORE any email is sent, so an SMTP failure can never
cause a double-open or double-close on the next run.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass
from typing import List, Optional

from earnings_notifier.notifier import EmailNotifier
from earnings_notifier.window import read_window_file

from .candidates import find_candidates
from .config import TraderConfig
from .emails import render_close_email, render_open_email, render_plan_email
from .ledger import (
    close_due_positions,
    load_ledger,
    open_from_decisions,
    save_ledger,
    summarize,
)
from .pipeline import evaluate_all

logger = logging.getLogger(__name__)

PHASES = ("morning", "entry")


@dataclass
class TraderRunResult:
    phase: str
    candidates: int
    opened: int
    closed: int
    emails_sent: int
    plan_subject: Optional[str] = None


def run_trader(
    config: TraderConfig,
    phase: str,
    *,
    today: Optional[dt.date] = None,
    now: Optional[dt.datetime] = None,
    provider=None,
    window: Optional[dict] = None,
) -> TraderRunResult:
    """Execute one trader phase. ``today``/``now``/``provider``/``window``
    are injectable for testing."""
    if phase not in PHASES:
        raise ValueError(f"phase must be one of {PHASES}, got {phase!r}")
    config.validate()
    if not config.trader_ledger_file:
        logger.info("Trader disabled (TRADER_LEDGER_FILE empty); nothing to do")
        return TraderRunResult(phase=phase, candidates=0, opened=0, closed=0,
                               emails_sent=0)
    now = now or dt.datetime.now(dt.timezone.utc)
    today = today or now.date()

    window_note = None
    if window is None:
        window = read_window_file(config.window_file)
    if window is None:
        window_note = (
            "No window data — the earnings digest may have failed; "
            "no candidates were evaluated."
        )
    elif window.get("today") != today:
        window_note = f"Window data is from {window['today']} (stale)."

    candidates, skipped = find_candidates(window, today)
    data = load_ledger(config.trader_ledger_file, config.trader_start_balance)

    needs_provider = bool(candidates) or any(
        p["status"] == "open" for p in data["positions"]
    )
    if provider is None and needs_provider:
        from earnings_analyzer.schwab import build_provider

        provider = build_provider(config, today=today)

    notifier = EmailNotifier(config)
    emails_sent = 0

    # Both phases close first: the entry run is the same-day safety net for
    # a failed morning run.
    closed: List[dict] = []
    if provider is not None and not config.dry_run:
        closed = close_due_positions(data, provider, today, now)
        if closed:
            save_ledger(config.trader_ledger_file, data, today)
        for pos in closed:
            subject, text, html_body = render_close_email(
                pos, summarize(data, today)
            )
            notifier.send(subject, text, html_body)
            emails_sent += 1

    decisions = evaluate_all(candidates, provider, config, today) if (
        provider is not None and candidates
    ) else []

    opened: List[dict] = []
    if phase == "entry":
        if not config.dry_run:
            opened = open_from_decisions(
                data, decisions, today, now, max_risk=config.trader_max_risk
            )
            if opened:
                save_ledger(config.trader_ledger_file, data, today)
            for pos in opened:
                subject, text, html_body = render_open_email(pos)
                notifier.send(subject, text, html_body)
                emails_sent += 1
        else:
            opened = []
            logger.info(
                "Dry run: %d position(s) would open",
                sum(1 for d in decisions if d.action == "open"),
            )

    plan_subject = None
    if phase == "morning":
        summary = summarize(data, today)
        summary.closed_this_run = closed
        subject, text, html_body = render_plan_email(
            summary, decisions, skipped, today, window_note=window_note
        )
        plan_subject = subject
        # In dry runs the notifier logs the full text body instead of sending.
        notifier.send(subject, text, html_body)
        emails_sent += 1

    if not config.dry_run:
        save_ledger(config.trader_ledger_file, data, today)

    return TraderRunResult(
        phase=phase,
        candidates=len(candidates),
        opened=len(opened),
        closed=len(closed),
        emails_sent=0 if config.dry_run else emails_sent,
        plan_subject=plan_subject,
    )
