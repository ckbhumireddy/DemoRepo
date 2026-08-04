"""Earnings-event model, data providers, and the notification-selection logic.

The selection logic (:func:`select_for_notification`) is deliberately pure and
free of network/IO so it can be unit-tested without hitting Yahoo Finance.
"""

from __future__ import annotations

import concurrent.futures
import datetime as dt
import logging
from dataclasses import dataclass
from typing import Iterable, List, Optional, Protocol

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class EarningsEvent:
    """A single upcoming earnings announcement."""

    ticker: str
    date: dt.date
    is_estimate: bool = True   # future dates are estimates until confirmed
    company: Optional[str] = None

    def days_until(self, today: dt.date) -> int:
        return (self.date - today).days


class EarningsProvider(Protocol):
    """Something that can report the next earnings date for a ticker."""

    def next_earnings_date(self, ticker: str) -> Optional[EarningsEvent]:
        ...


# --------------------------------------------------------------------------- #
# Selection logic (pure)
# --------------------------------------------------------------------------- #
def select_for_notification(
    events: Iterable[EarningsEvent],
    today: dt.date,
    lead_days: int = 7,
    window_days: int = 0,
) -> List[EarningsEvent]:
    """Return the events whose earnings date is ``lead_days`` away.

    An event qualifies when the number of days from ``today`` to the earnings
    date falls in ``[lead_days - window_days, lead_days + window_days]``. With
    the defaults (lead 7, window 0) this fires exactly one week ahead, so each
    upcoming earnings is notified once when a daily job runs.

    Results are sorted by date, then ticker.
    """
    lo = lead_days - window_days
    hi = lead_days + window_days
    selected = [e for e in events if lo <= e.days_until(today) <= hi]
    return sorted(selected, key=lambda e: (e.date, e.ticker))


# --------------------------------------------------------------------------- #
# Collection (drives a provider across many tickers, tolerating failures)
# --------------------------------------------------------------------------- #
def collect_upcoming(
    tickers: Iterable[str],
    provider: EarningsProvider,
    max_workers: int = 8,
) -> List[EarningsEvent]:
    """Look up the next earnings date for every ticker.

    Failures for individual tickers are logged and skipped rather than aborting
    the whole run. Uses a small thread pool since each lookup is IO-bound.
    """
    tickers = list(tickers)
    events: List[EarningsEvent] = []
    failures = 0

    def _lookup(ticker: str) -> Optional[EarningsEvent]:
        try:
            return provider.next_earnings_date(ticker)
        except Exception as exc:  # noqa: BLE001 - never let one ticker kill the run
            logger.debug("earnings lookup failed for %s: %s", ticker, exc)
            return None

    workers = max(1, min(max_workers, len(tickers) or 1))
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        for ticker, result in zip(tickers, pool.map(_lookup, tickers)):
            if result is None:
                failures += 1
            else:
                events.append(result)

    logger.info(
        "Resolved earnings for %d/%d tickers (%d without upcoming data)",
        len(events),
        len(tickers),
        failures,
    )
    return events


# --------------------------------------------------------------------------- #
# yfinance-backed provider
# --------------------------------------------------------------------------- #
class YFinanceProvider:
    """Fetches upcoming earnings dates from Yahoo Finance via ``yfinance``."""

    def __init__(self, today: Optional[dt.date] = None) -> None:
        self._today = today  # overridable for testing; None -> use dt.date.today()

    def _now(self) -> dt.date:
        return self._today or dt.date.today()

    def next_earnings_date(self, ticker: str) -> Optional[EarningsEvent]:
        import pandas as pd  # noqa: F401  (yfinance returns pandas objects)
        import yfinance as yf

        yticker = yf.Ticker(ticker)
        today = self._now()

        # Preferred path: the earnings-dates table (past + future rows).
        try:
            df = yticker.get_earnings_dates(limit=16)
        except Exception:  # noqa: BLE001
            df = None

        if df is not None and not df.empty:
            future_dates = []
            for idx in df.index:
                d = _to_date(idx)
                if d is not None and d >= today:
                    future_dates.append(d)
            if future_dates:
                return EarningsEvent(
                    ticker=ticker,
                    date=min(future_dates),
                    is_estimate=True,
                )

        # Fallback: the calendar dict exposes an "Earnings Date" field.
        try:
            cal = yticker.calendar
        except Exception:  # noqa: BLE001
            cal = None
        d = _calendar_earnings_date(cal, today)
        if d is not None:
            return EarningsEvent(ticker=ticker, date=d, is_estimate=True)

        return None


def _to_date(value) -> Optional[dt.date]:
    """Best-effort conversion of a pandas/py timestamp to a ``date``."""
    if value is None:
        return None
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    to_pydatetime = getattr(value, "to_pydatetime", None)
    if callable(to_pydatetime):
        try:
            return to_pydatetime().date()
        except Exception:  # noqa: BLE001
            return None
    date_attr = getattr(value, "date", None)
    if callable(date_attr):
        try:
            return date_attr()
        except Exception:  # noqa: BLE001
            return None
    return None


def _calendar_earnings_date(cal, today: dt.date) -> Optional[dt.date]:
    """Extract the earliest future earnings date from a yfinance calendar."""
    if cal is None:
        return None

    raw = None
    # Newer yfinance: calendar is a dict.
    if isinstance(cal, dict):
        raw = cal.get("Earnings Date")
    else:
        # Older yfinance: calendar is a DataFrame with an "Earnings Date" row.
        try:
            raw = cal.loc["Earnings Date"].tolist()
        except Exception:  # noqa: BLE001
            raw = None

    if raw is None:
        return None
    if not isinstance(raw, (list, tuple)):
        raw = [raw]

    candidates = []
    for item in raw:
        d = _to_date(item)
        if d is not None and d >= today:
            candidates.append(d)
    return min(candidates) if candidates else None
