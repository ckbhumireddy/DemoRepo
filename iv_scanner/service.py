"""Orchestration: universe -> scan -> top N -> plays -> email."""

from __future__ import annotations

import datetime as dt
import json
import logging
import os
from dataclasses import dataclass
from typing import Optional

from earnings_notifier.notifier import EmailNotifier
from earnings_notifier.window import read_window_file

from .config import IVScanConfig
from .emails import render_scan_email
from .plays import suggest_plays
from .scanner import (
    RateGate,
    annotate,
    make_schwab_fetch,
    make_yahoo_fetch,
    scan,
    select_top,
)

logger = logging.getLogger(__name__)


@dataclass
class ScanRunResult:
    scanned: int
    ranked: int
    top: int
    ideas: int
    emails_sent: int
    subject: Optional[str] = None


def _held_quantities(config: IVScanConfig) -> dict:
    from portfolio_insights.portfolio import load_portfolio

    snapshot = load_portfolio(config.portfolio_json, config.portfolio_file)
    if snapshot.fetch_error:
        logger.info("No portfolio for highlights (unavailable)")
        return {}
    return {p.ticker: p.quantity for p in snapshot.positions}


def _schwab_session(config: IVScanConfig):
    if not (config.schwab_app_key and config.schwab_app_secret):
        return None
    from earnings_analyzer.schwab import SchwabAuthError, SchwabSession

    try:
        return SchwabSession(
            app_key=config.schwab_app_key,
            app_secret=config.schwab_app_secret,
            token_json=config.schwab_token,
            token_file=config.schwab_token_file,
        )
    except SchwabAuthError as exc:
        logger.warning("Schwab not usable (%s)", exc)
        return None


def run_scan(
    config: IVScanConfig,
    *,
    today: Optional[dt.date] = None,
    tickers=None,
    fetch=None,
    window: Optional[dict] = None,
    held: Optional[set] = None,
) -> ScanRunResult:
    """Execute one scan. Data sources are injectable for testing."""
    config.validate()
    today = today or dt.date.today()

    # Once-per-day guard (external trigger + cron fallback can both fire).
    state_file = config.ivscan_state_file
    if state_file and os.path.exists(state_file):
        try:
            with open(state_file, "r", encoding="utf-8") as fh:
                if json.load(fh).get("last_sent") == today.isoformat():
                    logger.info("Scan email already sent today; skipping")
                    return ScanRunResult(0, 0, 0, 0, 0)
        except (json.JSONDecodeError, OSError):
            pass

    if held is None:
        held = _held_quantities(config)
    held_set = set(held)
    if window is None:
        window = read_window_file(config.window_file)

    scan_note = None
    if tickers is None or fetch is None:
        session = _schwab_session(config)
        if config.ivscan_universe == "portfolio":
            universe = sorted(held_set)
        else:
            from earnings_notifier.sp500 import get_sp500_tickers

            universe = list(dict.fromkeys(get_sp500_tickers() + sorted(held_set)))
        if session is not None:
            fetch = fetch or make_schwab_fetch(session, RateGate(), today)
            tickers = tickers or universe
        else:
            # Degraded: a 500-ticker Yahoo sweep would be rate-limited to
            # death — scan only the portfolio + this week's reporters.
            from earnings_analyzer.provider import YFinanceMarketData

            small = set(held_set)
            if window:
                small |= {e["ticker"] for e in window.get("events", [])}
            tickers = tickers or sorted(small)
            fetch = fetch or make_yahoo_fetch(YFinanceMarketData(today=today), today)
            scan_note = (
                "Schwab market data unavailable — scanned only the portfolio "
                "and this week's reporters instead of the full S&P 500."
            )

    rows, _failures = scan(tickers, fetch, max_workers=config.ivscan_max_workers)
    annotate(rows, held_set, window)
    top = select_top(rows, config.ivscan_top_n)
    # Plays consider every scanned holding, not only the top table — a held
    # name with a LOW rank is a cheap-protection candidate.
    held_rows = [r for r in rows if r.held and r not in top]
    qty_map = held if isinstance(held, dict) else {}
    ideas = suggest_plays(top + held_rows, qty_map)

    universe_note = f"{len(rows)} of {len(tickers)} tickers ranked"
    subject, text, html_body = render_scan_email(
        top, ideas, today, universe_note=universe_note, scan_note=scan_note
    )
    EmailNotifier(config).send(subject, text, html_body)

    if not config.dry_run and state_file:
        directory = os.path.dirname(state_file)
        if directory:
            os.makedirs(directory, exist_ok=True)
        with open(state_file, "w", encoding="utf-8") as fh:
            json.dump({"last_sent": today.isoformat()}, fh)

    return ScanRunResult(
        scanned=len(tickers),
        ranked=len(rows),
        top=len(top),
        ideas=len(ideas),
        emails_sent=0 if config.dry_run else 1,
        subject=subject,
    )
