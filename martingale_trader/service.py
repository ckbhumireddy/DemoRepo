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


# A complete SPX session's 30-minute candles run 9:30 to 15:30 at the
# last candle's open — 6 hours first-to-last. Requiring (nearly) that
# span rejects partial days without any timezone arithmetic; a run
# during market hours simply falls back to the last posted daily bar.
INTRADAY_FULL_SESSION = dt.timedelta(hours=5, minutes=54)


def daily_bar_from_intraday(payload):
    """One synthesized daily bar from a day of intraday candles.

    Returns None unless the newest day's candles span a full session.
    (An ET session never crosses UTC midnight, so grouping candles by
    their UTC date groups them by trading day.)
    """
    from earnings_analyzer.models import PriceBar

    by_day = {}
    for candle in (payload or {}).get("candles", []):
        ms, close = candle.get("datetime"), candle.get("close")
        if ms is None or close is None or close <= 0:
            continue
        stamp = dt.datetime.fromtimestamp(ms / 1000, tz=dt.timezone.utc)
        by_day.setdefault(stamp.date(), []).append((stamp, candle))
    for day in sorted(by_day, reverse=True):
        candles = sorted(by_day[day])
        if candles[-1][0] - candles[0][0] < INTRADAY_FULL_SESSION:
            continue  # in progress (or a short holiday day) — try older
        rows = [c for _, c in candles]
        return PriceBar(
            day=day,
            open=rows[0].get("open") or rows[0]["close"],
            high=max(c.get("high") or c["close"] for c in rows),
            low=min(c.get("low") or c["close"] for c in rows),
            close=rows[-1]["close"],
            volume=sum(c.get("volume") or 0.0 for c in rows),
        )
    return None


class SchwabHistory:
    """Daily bars from the Schwab ``/pricehistory`` endpoint, nothing else.

    Schwab posts the official daily candle hours after the close, so a
    16:45 ET run would otherwise only see yesterday. When the intraday
    feed shows a completed session newer than the last daily candle,
    its close is appended as the day's bar.
    """

    def __init__(self, session) -> None:
        self._session = session

    def price_history(self, ticker: str, days: int = 30):
        from earnings_analyzer.schwab import bars_from_candles, schwab_symbol

        symbol = schwab_symbol(ticker)
        payload = self._session.get(
            "/pricehistory",
            {
                "symbol": symbol,
                "periodType": "month",
                "period": 1,
                "frequencyType": "daily",
                "frequency": 1,
            },
        )
        bars = bars_from_candles(payload)
        try:
            # Two days, not one: late in the evening Schwab's 1-day window
            # can roll to the next (still empty) session and return nothing.
            # daily_bar_from_intraday picks the newest COMPLETE session, so
            # the extra day never changes which close is used.
            intraday = self._session.get(
                "/pricehistory",
                {
                    "symbol": symbol,
                    "periodType": "day",
                    "period": 2,
                    "frequencyType": "minute",
                    "frequency": 30,
                    "needExtendedHoursData": "false",
                },
            )
            synth = daily_bar_from_intraday(intraday)
        except Exception as exc:  # noqa: BLE001 - enhancement, not a dependency
            logger.warning(
                "Intraday close fetch failed (%s); using daily candles only",
                exc,
            )
            synth = None
        if synth and (not bars or synth.day > bars[-1].day):
            logger.info(
                "Martingale: daily candle for %s not posted yet; using the "
                "close %.2f from the completed intraday session",
                synth.day, synth.close,
            )
            bars.append(synth)
        return bars


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
