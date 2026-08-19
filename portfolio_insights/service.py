"""Orchestration: portfolio -> quotes -> views -> insights -> emails.

Two modes per weekday:

- ``eod`` (after the close): the full insights email, once per day (a
  marker in state makes external-trigger + cron-fallback double runs send
  a single email).
- ``midday``: an alert email only when a position or the portfolio crosses
  a movement threshold; each (date, ticker, trigger) alerts at most once.

Degradation: a broken portfolio source produces an actionable EOD email
and a silent midday; the run itself never fails on a data problem.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass
from typing import Optional

from earnings_notifier.notifier import EmailNotifier

from .config import InsightsConfig
from .emails import render_eod_email, render_midday_email
from .options import OptionsSummary, build_option_views
from .portfolio import load_portfolio
from .quotes import fetch_quotes
from .signals import (
    apply_options,
    build_views,
    check_alert_triggers,
    earnings_ahead,
    generate_insights,
    option_triggers,
    volatility_spikes,
)
from .state import (
    alert_key,
    already_alerted,
    load_state,
    record_alerts,
    save_state,
)

logger = logging.getLogger(__name__)

MODES = ("eod", "midday")


@dataclass
class InsightsRunResult:
    mode: str
    positions: int
    insights: int
    alerts: int
    emails_sent: int
    subject: Optional[str] = None


def _schwab_session(config: InsightsConfig):
    """A Schwab session for quotes, or None (quotes fall back to Yahoo)."""
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
        logger.warning("Schwab not usable (%s); quotes via Yahoo", exc)
        return None


def run_insights(
    config: InsightsConfig,
    mode: str,
    *,
    today: Optional[dt.date] = None,
    snapshot=None,
    quotes_fn=None,
    provider=None,
    earnings_provider=None,
) -> InsightsRunResult:
    """Execute one insights run. Data sources are injectable for testing."""
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}, got {mode!r}")
    config.validate()
    today = today or dt.date.today()
    notifier = EmailNotifier(config)
    state = load_state(config.insights_state_file)

    def _result(insights=0, alerts=0, sent=0, subject=None, positions=0):
        return InsightsRunResult(
            mode=mode, positions=positions, insights=insights, alerts=alerts,
            emails_sent=0 if config.dry_run else sent, subject=subject,
        )

    # EOD once-per-day guard first — before any data fetch is wasted.
    if mode == "eod" and state.get("last_eod_date") == today.isoformat():
        logger.info("EOD email already sent today; skipping duplicate")
        return _result()

    if snapshot is None:
        snapshot = load_portfolio(config.portfolio_json, config.portfolio_file)

    if snapshot.fetch_error:
        if mode == "midday":
            logger.warning("Portfolio unavailable; midday alert skipped")
            return _result()
        subject, text, html_body = render_eod_email(
            [], None, [], today, fetch_error=snapshot.fetch_error
        )
        notifier.send(subject, text, html_body)
        if not config.dry_run and config.insights_state_file:
            state["last_eod_date"] = today.isoformat()
            save_state(config.insights_state_file, state, today)
        return _result(sent=1, subject=subject)

    tickers = [p.ticker for p in snapshot.positions]
    session = _schwab_session(config) if quotes_fn is None and tickers else None
    quotes = (
        quotes_fn(tickers)
        if quotes_fn is not None
        else (fetch_quotes(session, tickers) if tickers else {})
    )
    views, portfolio = build_views(snapshot, quotes)

    # Options book: valued and folded into totals only when tracking is
    # enabled — marks for far-dated contracts proved unreliable, so the
    # default is to exclude them and say so.
    option_summary = None
    options_note = None
    if snapshot.options and config.insights_track_options:
        if provider is None:
            from earnings_analyzer.schwab import build_provider

            provider = build_provider(config, today=today)
        option_summary = OptionsSummary(
            build_option_views(snapshot.options, provider, today)
        )
        apply_options(portfolio, option_summary)
    elif snapshot.options:
        options_note = (
            f"{len(snapshot.options)} option position(s) are excluded from "
            "all figures (options tracking is off)."
        )

    if mode == "midday":
        triggers = check_alert_triggers(
            views,
            portfolio,
            move_alert_pct=config.insights_move_alert_pct,
            portfolio_alert_pct=config.insights_portfolio_alert_pct,
            move_alert_dollars=config.insights_move_alert_dollars,
            portfolio_alert_dollars=config.insights_portfolio_alert_dollars,
        )
        if option_summary is not None:
            triggers += option_triggers(
                option_summary.views,
                move_alert_dollars=config.insights_move_alert_dollars,
            )
        keys = [alert_key(today, t.ticker, t.kind) for t in triggers]
        fresh = [
            (t, k) for t, k in zip(triggers, keys)
            if not (config.insights_state_file and already_alerted(state, k))
        ]
        if not fresh:
            logger.info("No new midday triggers; nothing sent")
            return _result(positions=len(views))
        subject, text, html_body = render_midday_email(
            [t for t, _ in fresh], views, portfolio, today
        )
        notifier.send(subject, text, html_body)
        if not config.dry_run and config.insights_state_file:
            record_alerts(state, [k for _, k in fresh])
            save_state(config.insights_state_file, state, today)
        return _result(
            positions=len(views), alerts=len(fresh), sent=1, subject=subject
        )

    # ---- EOD ----
    histories = {}
    events = []
    iv_context = []
    if views:
        if provider is None:
            from earnings_analyzer.schwab import build_provider

            provider = build_provider(config, today=today)
        for ticker in tickers:
            try:
                histories[ticker] = provider.price_history(ticker, days=60)
            except Exception as exc:  # noqa: BLE001
                logger.debug("No history for one ticker (%s)", type(exc).__name__)

        # Options premium context for the largest holdings: IV rank (proxy)
        # says whether hedges are cheap or covered calls pay well right now.
        from earnings_analyzer.volatility import compute_iv_rank

        top = [v for v in views if v.market_value is not None][:10]
        for v in top:
            try:
                # ~30-day expiry: the IV30 convention. The nearest expiry
                # sits at its structural low days before expiration and
                # would rank everything at 0.
                future = [
                    e for e in provider.option_expiries(v.ticker)
                    if (e - today).days >= 7
                ]
                expiry = (
                    min(future, key=lambda e: abs((e - today).days - 30))
                    if future
                    else None
                )
                chain = (
                    provider.option_chain(v.ticker, expiry) if expiry else None
                )
                atm_iv = chain.atm_iv() if chain else None
                # Skip chains that barely trade — their IV is a fiction.
                if chain is not None:
                    oi = [
                        c.open_interest
                        for c in (chain.atm_call(), chain.atm_put())
                        if c is not None and c.open_interest
                    ]
                    if not oi or max(oi) < 50:
                        atm_iv = None
                # Rank against a full year of realized vol — two volatile
                # months alone would put every IV at the bottom of the range.
                try:
                    year_bars = provider.price_history(v.ticker, days=380)
                except Exception:  # noqa: BLE001
                    year_bars = histories.get(v.ticker, [])
                rank = compute_iv_rank(v.ticker, atm_iv, year_bars)
            except Exception:  # noqa: BLE001
                rank = None
            # Cash-like instruments (realized vol ~0) rank meaninglessly.
            if rank is not None and rank.hv_high >= 0.05:
                iv_context.append(rank)
        if earnings_provider is None:
            from earnings_notifier.earnings import YFinanceProvider

            earnings_provider = YFinanceProvider(today=today)
        for ticker in tickers:
            try:
                event = earnings_provider.next_earnings_date(ticker)
            except Exception:  # noqa: BLE001
                event = None
            if event is not None:
                events.append(event)

    vol = volatility_spikes(views, histories, config.insights_vol_spike_mult)
    ahead = earnings_ahead(events, today, config.insights_event_days)
    insights = generate_insights(
        views,
        vol,
        ahead,
        max_weight_pct=config.insights_max_weight_pct,
        move_alert_pct=config.insights_move_alert_pct,
    )
    subject, text, html_body = render_eod_email(
        views, portfolio, insights, today, iv_context=iv_context,
        options_summary=option_summary, other_note=options_note,
    )
    notifier.send(subject, text, html_body)
    if not config.dry_run and config.insights_state_file:
        state["last_eod_date"] = today.isoformat()
        if portfolio.total_value is not None:
            state["last_portfolio_value"] = {
                "date": today.isoformat(),
                "value": portfolio.total_value,
            }
        save_state(config.insights_state_file, state, today)
    return _result(
        positions=len(views), insights=len(insights), sent=1, subject=subject
    )
