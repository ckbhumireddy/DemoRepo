"""Orchestration: closes -> settle -> ladder -> open -> email.

One run per weekday, after the close. Each run:

1. Fetches recent daily closes for the index from the Schwab Trader API
   (Schwab only — no Yahoo fallback; a run without usable Schwab
   credentials fails and triggers the workflow's failure alert, because
   this service writes permanent state from the prices it fetches).
2. Settles the open round at the latest close (if the close is newer
   than the round's entry) and advances the stake ladder.
3. Opens the next round at that close, unless the account is busted.
4. Emails the daily report.

The run is idempotent: a duplicate run on the same day finds an open
round already entered at the latest close and does nothing, so the
external trigger and the cron fallback can both fire. The state is
saved BEFORE the email is sent, so an SMTP failure can never cause a
double settlement on the next run.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass
from typing import Optional

from earnings_notifier.config import ConfigError
from earnings_notifier.notifier import EmailNotifier

from .config import MartingaleConfig
from .emails import render_daily_email
from .engine import (
    load_state,
    notional_for_step,
    open_round,
    save_state,
    settle_open_round,
    summarize,
)

logger = logging.getLogger(__name__)

SYMBOL_LABEL = "SPX"


def _ladder_base(config: MartingaleConfig, balance: float) -> float:
    """Step-0 stake: a fraction of the balance, or fixed dollars."""
    if config.martingale_base_pct > 0:
        return round(config.martingale_base_pct * balance, 2)
    return config.martingale_base_notional


class SchwabHistory:
    """Daily bars from the Schwab ``/pricehistory`` endpoint, nothing else."""

    def __init__(self, session) -> None:
        self._session = session

    def price_history(self, ticker: str, days: int = 30):
        from earnings_analyzer.schwab import bars_from_candles, schwab_symbol

        payload = self._session.get(
            "/pricehistory",
            {
                "symbol": schwab_symbol(ticker),
                "periodType": "month",
                "period": 1,
                "frequencyType": "daily",
                "frequency": 1,
            },
        )
        return bars_from_candles(payload)


def _schwab_provider(config: MartingaleConfig) -> SchwabHistory:
    """Schwab or nothing: missing/lapsed credentials abort the run."""
    if not (config.schwab_app_key and config.schwab_app_secret):
        raise ConfigError(
            "SCHWAB_APP_KEY and SCHWAB_APP_SECRET are required — the "
            "martingale trader prices from Schwab only (no Yahoo fallback)."
        )
    from earnings_analyzer.schwab import SchwabSession

    return SchwabHistory(
        SchwabSession(
            app_key=config.schwab_app_key,
            app_secret=config.schwab_app_secret,
            token_json=config.schwab_token,
            token_file=config.schwab_token_file,
        )
    )


@dataclass
class MartingaleRunResult:
    settled: int
    opened: int
    emails_sent: int
    skipped: bool = False
    subject: Optional[str] = None


def run_martingale(
    config: MartingaleConfig,
    *,
    today: Optional[dt.date] = None,
    provider=None,
) -> MartingaleRunResult:
    """Execute one daily run. ``today``/``provider`` are injectable for
    testing."""
    config.validate()
    today = today or dt.date.today()
    state = load_state(
        config.martingale_state_file, config.martingale_start_balance
    )

    if state["busted"]:
        # The run that busted the account already sent the bust email.
        logger.info("Martingale: account is busted; nothing to do")
        return MartingaleRunResult(0, 0, 0, skipped=True)

    if provider is None:
        provider = _schwab_provider(config)
    bars = [
        b for b in provider.price_history(config.martingale_symbol, days=30)
        if b.day <= today
    ]
    if not bars:
        raise RuntimeError(
            f"No price history for {config.martingale_symbol}; cannot run"
        )
    latest = bars[-1]

    prior = state["open_round"]
    if prior and latest.day.isoformat() <= prior["entry_date"]:
        logger.info(
            "Martingale: no close newer than %s (duplicate run or holiday); "
            "skipping", prior["entry_date"],
        )
        return MartingaleRunResult(0, 0, 0, skipped=True)

    settled = None
    if prior:
        settled = settle_open_round(state, latest.day, latest.close)

    opened = None
    if not state["busted"]:
        notional = notional_for_step(
            state["step"],
            _ladder_base(config, state["balance"]),
            config.martingale_factor,
            config.martingale_max_notional,
        )
        opened = open_round(state, latest.day, latest.close, notional)

    if not config.dry_run:
        save_state(config.martingale_state_file, state)

    summary = summarize(
        state,
        _ladder_base(config, state["balance"]),
        config.martingale_factor,
        config.martingale_max_notional,
    )
    subject, text, html_body = render_daily_email(
        summary, settled, opened, latest.day, symbol_label=SYMBOL_LABEL
    )
    EmailNotifier(config).send(subject, text, html_body)

    return MartingaleRunResult(
        settled=1 if settled else 0,
        opened=1 if opened else 0,
        emails_sent=0 if config.dry_run else 1,
        subject=subject,
    )
