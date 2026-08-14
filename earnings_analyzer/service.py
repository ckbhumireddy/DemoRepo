"""Orchestration: window -> per-ticker analysis -> rating sort -> email."""

from __future__ import annotations

import concurrent.futures
import datetime as dt
import logging
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from earnings_notifier.notifier import EmailNotifier
from earnings_notifier.window import read_window_file

from .analyzer import analyze_ticker
from .config import AnalyzerConfig
from .formatting import render_email
from .models import TickerAnalysis
from .provider import MarketDataProvider

logger = logging.getLogger(__name__)

# (ticker, earnings date) — an optional third element carries the report
# timing ("pre-market" / "after-market" / None); plain 2-tuples still work.
Entry = Tuple[str, dt.date]


@dataclass
class AnalyzerRunResult:
    requested: int
    analyzed: int
    notified: bool
    subject: str
    analyses: List[TickerAnalysis] = field(default_factory=list)


def resolve_entries(
    config: AnalyzerConfig,
    today: dt.date,
    tickers: Optional[List[str]] = None,
) -> List[Entry]:
    """Figure out which (ticker, event date) pairs to analyze.

    Preference order: explicit tickers -> the notifier's window file -> a
    full recompute of the window (slow path, used when run standalone).
    """
    if tickers:
        from earnings_notifier.earnings import YFinanceProvider

        provider = YFinanceProvider(today=today)
        entries: List[Entry] = []
        for ticker in tickers:
            event = provider.next_earnings_date(ticker)
            if event is not None:
                entries.append((event.ticker, event.date, event.timing))
            else:
                logger.warning("No upcoming earnings date found for %s", ticker)
        return entries

    window = read_window_file(config.window_file)
    if window is not None:
        entries = [
            (e["ticker"], e["date"], e.get("timing")) for e in window["events"]
        ]
        # Keep only events still in the future window (a stale file is safe).
        entries = [entry for entry in entries if entry[1] >= today]
        logger.info(
            "Loaded %d entr(ies) from window file %s",
            len(entries),
            config.window_file,
        )
        return entries

    logger.info("No window file; recomputing the earnings window (slow path)")
    from earnings_notifier.earnings import (
        YFinanceProvider,
        collect_upcoming,
        select_for_notification,
    )
    from earnings_notifier.sp500 import get_sp500_tickers, normalize_ticker

    roster = get_sp500_tickers()
    if config.extra_tickers:
        seen = set(roster)
        roster = list(roster) + [
            t for t in (normalize_ticker(r) for r in config.extra_tickers)
            if t not in seen
        ]
    events = collect_upcoming(
        roster, YFinanceProvider(today=today), max_workers=config.max_workers
    )
    selected = select_for_notification(
        events, today, lead_days=config.lead_days, min_days=config.min_days
    )
    return [(e.ticker, e.date, e.timing) for e in selected]


def run_analyzer(
    config: AnalyzerConfig,
    *,
    today: Optional[dt.date] = None,
    provider: Optional[MarketDataProvider] = None,
    entries: Optional[List[Entry]] = None,
    tickers: Optional[List[str]] = None,
) -> AnalyzerRunResult:
    """Execute one trade-sheet cycle.

    ``today``, ``provider`` and ``entries`` are injectable for testing; in
    production they default to the real date, live Yahoo data, and the
    window file the notifier wrote.
    """
    config.validate()
    today = today or dt.date.today()

    if entries is None:
        entries = resolve_entries(config, today, tickers)
    if config.analyzer_ticker_limit and config.analyzer_ticker_limit > 0:
        entries = entries[: config.analyzer_ticker_limit]
        logger.info("Limiting analysis to first %d entr(ies)", len(entries))

    if provider is None:
        from .schwab import build_provider

        provider = build_provider(config, today=today)

    logger.info("Analyzing %d earnings setup(s)...", len(entries))

    def _one(entry: Entry) -> Optional[TickerAnalysis]:
        ticker, event_date = entry[0], entry[1]
        timing = entry[2] if len(entry) > 2 else None
        try:
            analysis = analyze_ticker(ticker, event_date, provider, config, today)
        except Exception as exc:  # noqa: BLE001 - one ticker never kills the run
            logger.warning("Analysis failed for %s: %s", ticker, exc)
            return None
        if analysis is not None:
            analysis.timing = timing
        return analysis

    analyses: List[TickerAnalysis] = []
    if entries:
        workers = max(1, min(config.analyzer_max_workers, len(entries)))
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
            analyses = [a for a in pool.map(_one, entries) if a is not None]

    # Best setups first.
    analyses.sort(key=lambda a: (a.rating.score if a.rating else 0.0), reverse=True)

    # Scorecard: grade past suggestions against what actually happened, then
    # record today's. Dry runs read the summary but write nothing.
    scorecard_summary = None
    if config.scorecard_file:
        from .scorecard import (
            grade_pending,
            load_scorecard,
            record_suggestions,
            save_scorecard,
            summarize,
        )

        data = load_scorecard(config.scorecard_file)
        if not config.dry_run:
            grade_pending(data, provider, today)
            record_suggestions(data, analyses, today)
            save_scorecard(config.scorecard_file, data, today)
        scorecard_summary = summarize(data)

    # Paper trader: close positions whose report has passed, then open new
    # ones from today's sheet. Dry runs read the summary but trade nothing.
    paper_summary = None
    if config.paper_file:
        from .paper import (
            close_due_positions,
            load_paper,
            open_positions,
            save_paper,
            summarize_paper,
        )

        pdata = load_paper(config.paper_file, config.paper_start_balance)
        closed = []
        if not config.dry_run:
            closed = close_due_positions(pdata, provider, today)
            open_positions(
                pdata,
                analyses,
                today,
                max_risk=config.paper_max_risk,
                min_grade=config.paper_min_grade,
            )
            save_paper(config.paper_file, pdata, today)
        paper_summary = summarize_paper(pdata)
        paper_summary.closed_this_run = closed

    subject, text_body, html_body = render_email(
        analyses, today, scorecard=scorecard_summary, paper=paper_summary
    )

    sent = False
    if analyses or config.analyzer_send_empty:
        EmailNotifier(config).send(subject, text_body, html_body)
        sent = not config.dry_run
    else:
        logger.info("Nothing analyzed; no trade sheet sent")

    return AnalyzerRunResult(
        requested=len(entries),
        analyzed=len(analyses),
        notified=sent,
        subject=subject,
        analyses=analyses,
    )
