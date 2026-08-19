"""Orchestration: settle expired condors -> ladder -> open the next one.

One run per weekday, after the close. Each run:

1. Settles any open condor whose expiry has passed, at that expiry
   day's closing level (closes come from the martingale trader's
   SchwabHistory, which already handles Schwab's lagging history via
   the after-close quote).
2. Advances the martingale ladder on the outcome.
3. If nothing is open and the account is not busted, prices a new
   condor from the Schwab option chain (nearest weekly expiry within
   the DTE window) and opens it at the ladder's quantity.
4. Emails a report when something settled or opened; a duplicate run
   finds the position already open and does nothing.

The ledger is saved BEFORE any email is sent, so an SMTP failure can
never cause a double settlement or double open.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass
from typing import Optional

from earnings_notifier.config import ConfigError
from earnings_notifier.notifier import EmailNotifier
from martingale_trader.service import SchwabHistory

from .config import CondorConfig
from .emails import render_report_email
from .engine import (
    load_ledger,
    open_condor,
    pick_condor,
    qty_for_step,
    save_ledger,
    settle_position,
    summarize,
)

logger = logging.getLogger(__name__)

SYMBOL_LABEL = "SPX"


class SchwabChains:
    """Option chains for one symbol from the Schwab ``/chains`` endpoint."""

    def __init__(self, session, symbol: str) -> None:
        self._session = session
        self._symbol = symbol
        self._cache = None

    def _chains(self, today: dt.date, horizon_days: int = 21):
        if self._cache is None:
            from earnings_analyzer.schwab import chains_from_payload, schwab_symbol

            # strikeCount keeps the response small: an unfiltered $SPX
            # chain over 3 weeks is thousands of contracts and Schwab
            # 502s on it. 80 strikes around the money comfortably covers
            # ~1.25% OTM shorts plus wings at 5-point spacing.
            payload = self._session.get(
                "/chains",
                {
                    "symbol": schwab_symbol(self._symbol),
                    "contractType": "ALL",
                    "strategy": "SINGLE",
                    "strikeCount": 80,
                    "fromDate": today.isoformat(),
                    "toDate": (
                        today + dt.timedelta(days=horizon_days)
                    ).isoformat(),
                },
            )
            self._cache = chains_from_payload(payload, self._symbol)
        return self._cache

    def expiries(self, today: dt.date):
        return sorted(self._chains(today))

    def chain(self, expiry: dt.date, today: dt.date):
        return self._chains(today).get(expiry)


def _schwab_session(config: CondorConfig):
    if not (config.schwab_app_key and config.schwab_app_secret):
        raise ConfigError(
            "SCHWAB_APP_KEY and SCHWAB_APP_SECRET are required — the "
            "condor trader prices from Schwab only (no Yahoo fallback)."
        )
    from earnings_analyzer.schwab import SchwabSession

    return SchwabSession(
        app_key=config.schwab_app_key,
        app_secret=config.schwab_app_secret,
        token_json=config.schwab_token,
        token_file=config.schwab_token_file,
    )


@dataclass
class CondorRunResult:
    settled: int
    opened: int
    emails_sent: int
    skipped: bool = False
    subject: Optional[str] = None


def run_condor(
    config: CondorConfig,
    *,
    today: Optional[dt.date] = None,
    history=None,
    chains=None,
) -> CondorRunResult:
    """Execute one daily run. ``today``/``history``/``chains`` are
    injectable for testing."""
    config.validate()
    today = today or dt.date.today()
    data = load_ledger(config.condor_state_file, config.condor_start_balance)

    if data["busted"]:
        logger.info("Condor: account is busted; nothing to do")
        return CondorRunResult(0, 0, 0, skipped=True)

    session = None
    if history is None or chains is None:
        session = _schwab_session(config)
    history = history or SchwabHistory(session)
    chains = chains or SchwabChains(session, config.condor_symbol)

    bars = [
        b for b in history.price_history(config.condor_symbol, days=30)
        if b.day <= today
    ]
    closes = {b.day: b.close for b in bars}
    latest_day = bars[-1].day if bars else None

    # 1. Settle expired positions at their expiry day's close.
    settled = []
    for pos in data["positions"]:
        if pos["status"] != "open":
            continue
        expiry = dt.date.fromisoformat(pos["expiry"])
        if latest_day is None or expiry > latest_day:
            continue
        settle = closes.get(expiry)
        if settle is None:
            logger.warning(
                "Condor: no close for expiry %s yet; will retry", expiry
            )
            continue
        settled.append(settle_position(data, pos, settle, expiry))

    # 2. Open the next condor when flat.
    opened = None
    has_open = any(p["status"] == "open" for p in data["positions"])
    if not data["busted"] and not has_open:
        expiry = next(
            (e for e in chains.expiries(today)
             if config.condor_min_dte <= (e - today).days
             <= config.condor_max_dte),
            None,
        )
        chain = chains.chain(expiry, today) if expiry else None
        picked = pick_condor(
            chain, config.condor_otm_pct, config.condor_wing
        ) if chain else None
        if picked and picked["credit"] >= config.condor_min_credit:
            wing = picked["short_put"] - picked["long_put"]
            risk_per = (wing - picked["credit"]) * 100.0
            qty = qty_for_step(
                data["step"], config.condor_qty_ladder,
                risk_per, config.condor_max_risk, data["balance"],
            )
            if qty >= 1:
                opened = open_condor(data, expiry, picked, qty, today)
            else:
                logger.info("Condor: ladder quantity is 0 (risk cap); no open")
        else:
            logger.info(
                "Condor: no suitable setup (expiry %s, picked %s)",
                expiry, bool(picked),
            )

    if not settled and opened is None:
        # Duplicate run, holiday, or no setup — nothing to say.
        if not config.dry_run:
            save_ledger(config.condor_state_file, data)
        return CondorRunResult(0, 0, 0, skipped=True)

    if not config.dry_run:
        save_ledger(config.condor_state_file, data)

    summary = summarize(data)
    subject, text, html_body = render_report_email(
        summary, settled, opened, today, symbol_label=SYMBOL_LABEL
    )
    EmailNotifier(config).send(subject, text, html_body)

    return CondorRunResult(
        settled=len(settled),
        opened=1 if opened else 0,
        emails_sent=0 if config.dry_run else 1,
        subject=subject,
    )
