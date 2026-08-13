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
    """A single upcoming earnings announcement.

    Everything beyond ``ticker``/``date``/``is_estimate`` is optional detail
    used to enrich the email digest; ``None`` means "not available".
    """

    ticker: str
    date: dt.date
    is_estimate: bool = True   # future dates are estimates until confirmed
    company: Optional[str] = None
    price: Optional[float] = None
    fifty_two_week_low: Optional[float] = None
    fifty_two_week_high: Optional[float] = None
    market_cap: Optional[float] = None
    last_earnings_date: Optional[dt.date] = None
    last_eps_estimate: Optional[float] = None
    last_eps_actual: Optional[float] = None
    last_surprise_pct: Optional[float] = None
    last_reaction_pct: Optional[float] = None  # price move across the report
    first_notice: bool = False  # True the first time this event is emailed
    timing: Optional[str] = None  # "pre-market" | "after-market" | None (unknown)

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
    min_days: int = 0,
) -> List[EarningsEvent]:
    """Return the events whose earnings date falls within the look-ahead window.

    An event qualifies when the number of days from ``today`` to the earnings
    date is in ``[min_days, lead_days]`` (inclusive). With the defaults this is
    "any earnings from today through one week out". A given earnings stays in
    this window for several days; de-duplication against persisted state (see
    ``state.py``) is what keeps each one to a single email.

    Results are sorted by date, then ticker.
    """
    selected = [e for e in events if min_days <= e.days_until(today) <= lead_days]
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


def sort_by_market_cap(events: Iterable[EarningsEvent]) -> List[EarningsEvent]:
    """Largest companies first; unknown market caps sink to the end, where
    events fall back to date-then-ticker order."""
    return sorted(
        events,
        key=lambda e: (e.market_cap is None, -(e.market_cap or 0.0), e.date, e.ticker),
    )


def enrich_events(
    events: Iterable[EarningsEvent],
    provider,
    max_workers: int = 8,
) -> List[EarningsEvent]:
    """Fill in company/price/valuation details on the events being emailed.

    Only called for the handful of events that survive selection, so the
    per-ticker quote lookups stay cheap overall. A provider without an
    ``enrich`` method, or a failed lookup, leaves the event unchanged.
    """
    events = list(events)
    enrich = getattr(provider, "enrich", None)
    if not events or not callable(enrich):
        return events

    def _one(event: EarningsEvent) -> EarningsEvent:
        try:
            return enrich(event) or event
        except Exception as exc:  # noqa: BLE001 - detail is optional, never fatal
            logger.debug("enrichment failed for %s: %s", event.ticker, exc)
            return event

    workers = max(1, min(max_workers, len(events)))
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(_one, events))


# --------------------------------------------------------------------------- #
# yfinance-backed provider
# --------------------------------------------------------------------------- #
class YFinanceProvider:
    """Fetches upcoming earnings dates from Yahoo Finance via ``yfinance``.

    Yahoo intermittently returns 404s for perfectly valid symbols when it is
    rate limiting, so each lookup is retried a couple of times with a short
    backoff before giving up.
    """

    def __init__(
        self,
        today: Optional[dt.date] = None,
        attempts: int = 2,
        backoff: float = 0.75,
    ) -> None:
        self._today = today  # overridable for testing; None -> use dt.date.today()
        self.attempts = max(1, attempts)
        self.backoff = backoff

    def _now(self) -> dt.date:
        return self._today or dt.date.today()

    def next_earnings_date(self, ticker: str) -> Optional[EarningsEvent]:
        import time

        for attempt in range(self.attempts):
            try:
                result = self._lookup_once(ticker)
            except Exception as exc:  # noqa: BLE001
                logger.debug("lookup error for %s (attempt %d): %s",
                             ticker, attempt + 1, exc)
                result = None
            if result is not None:
                return result
            if attempt < self.attempts - 1 and self.backoff:
                time.sleep(self.backoff * (attempt + 1))
        return None

    def _lookup_once(self, ticker: str) -> Optional[EarningsEvent]:
        import pandas as pd  # noqa: F401  (yfinance returns pandas objects)
        import yfinance as yf

        yticker = yf.Ticker(ticker)
        today = self._now()

        # Preferred path: the earnings-dates table (past + future rows).
        try:
            df = yticker.get_earnings_dates(limit=16)
        except Exception:  # noqa: BLE001
            df = None

        last = {}
        if df is not None and not df.empty:
            # The same table carries past rows with reported results — capture
            # the most recent one so the digest can say how last quarter went.
            last = _last_reported_earnings(df, today)
            future = []
            for idx in df.index:
                d = _to_date(idx)
                if d is not None and d >= today:
                    future.append((d, idx))
            if future:
                best_date, best_idx = min(future, key=lambda pair: pair[0])
                return EarningsEvent(
                    ticker=ticker,
                    date=best_date,
                    is_estimate=True,
                    timing=_classify_timing(best_idx),
                    **last,
                )

        # Fallback: the calendar dict exposes an "Earnings Date" field.
        try:
            cal = yticker.calendar
        except Exception:  # noqa: BLE001
            cal = None
        d = _calendar_earnings_date(cal, today)
        if d is not None:
            return EarningsEvent(ticker=ticker, date=d, is_estimate=True, **last)

        return None

    def enrich(self, event: EarningsEvent) -> EarningsEvent:
        """Return a copy of *event* with company name and quote details filled in.

        Uses the heavier ``Ticker.info`` scrape (one request), falling back to
        ``fast_info`` for any numbers it lacks. Failures leave fields ``None``.
        """
        import dataclasses

        import yfinance as yf

        yticker = yf.Ticker(event.ticker)
        try:
            info = yticker.info or {}
        except Exception:  # noqa: BLE001
            info = {}

        updates = {}
        name = info.get("longName") or info.get("shortName")
        if name:
            updates["company"] = str(name)
        for field, keys in (
            ("price", ("currentPrice", "regularMarketPrice")),
            ("fifty_two_week_low", ("fiftyTwoWeekLow",)),
            ("fifty_two_week_high", ("fiftyTwoWeekHigh",)),
            ("market_cap", ("marketCap",)),
        ):
            for key in keys:
                value = _as_float(info.get(key))
                if value is not None:
                    updates[field] = value
                    break

        missing = [
            (field, attr)
            for field, attr in (
                ("price", "last_price"),
                ("fifty_two_week_low", "year_low"),
                ("fifty_two_week_high", "year_high"),
                ("market_cap", "market_cap"),
            )
            if field not in updates
        ]
        if missing:
            try:
                fast = yticker.fast_info
            except Exception:  # noqa: BLE001
                fast = None
            for field, attr in missing:
                try:
                    value = _as_float(getattr(fast, attr, None))
                except Exception:  # noqa: BLE001
                    value = None
                if value is not None:
                    updates[field] = value

        # How did the market react to the last report? Compare the last close
        # before the earnings date with the first close after it.
        if event.last_earnings_date is not None and event.last_reaction_pct is None:
            try:
                history = yticker.history(
                    start=event.last_earnings_date - dt.timedelta(days=10),
                    end=event.last_earnings_date + dt.timedelta(days=10),
                    interval="1d",
                )
            except Exception:  # noqa: BLE001
                history = None
            reaction = _post_earnings_move(history, event.last_earnings_date)
            if reaction is not None:
                updates["last_reaction_pct"] = reaction

        return dataclasses.replace(event, **updates) if updates else event


def _classify_timing(value) -> Optional[str]:
    """Classify an earnings timestamp as "pre-market" or "after-market".

    Yahoo's earnings-dates table carries a US/Eastern time of day (07:00 for
    a pre-market report, 16:05 for an after-market one). Reports before the
    09:30 ET open are "pre-market"; reports at or after the 16:00 ET close
    are "after-market". A midnight timestamp means Yahoo only knows the date,
    and an in-session time is ambiguous — both return ``None`` (unknown).
    """
    ts: Optional[dt.datetime] = None
    if isinstance(value, dt.datetime):
        ts = value
    else:
        to_pydatetime = getattr(value, "to_pydatetime", None)
        if callable(to_pydatetime):
            try:
                ts = to_pydatetime()
            except Exception:  # noqa: BLE001
                return None
    if ts is None:
        return None
    if ts.tzinfo is not None:
        try:
            from zoneinfo import ZoneInfo

            ts = ts.astimezone(ZoneInfo("America/New_York"))
        except Exception:  # noqa: BLE001 - unknown tz database entry
            return None
    t = ts.time()
    if t == dt.time(0, 0):
        return None  # date-only row; the time of day is unknown
    if t < dt.time(9, 30):
        return "pre-market"
    if t >= dt.time(16, 0):
        return "after-market"
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


def _as_float(value) -> Optional[float]:
    """Coerce to float, treating None/NaN/garbage as "not available"."""
    if value is None or isinstance(value, bool):
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return None if f != f else f  # NaN check


def _last_reported_earnings(df, today: dt.date) -> dict:
    """Pull the most recent past row with a reported EPS out of the
    ``get_earnings_dates`` table, as ``EarningsEvent`` field values.

    Returns an empty dict when no past row has results (e.g. a fresh IPO).
    """
    best_date = None
    best_row = None
    for idx, row in df.iterrows():
        d = _to_date(idx)
        if d is None or d >= today:
            continue
        if _as_float(row.get("Reported EPS")) is None:
            continue
        if best_date is None or d > best_date:
            best_date, best_row = d, row
    if best_row is None:
        return {}
    return {
        "last_earnings_date": best_date,
        "last_eps_estimate": _as_float(best_row.get("EPS Estimate")),
        "last_eps_actual": _as_float(best_row.get("Reported EPS")),
        "last_surprise_pct": _as_float(best_row.get("Surprise(%)")),
    }


def _post_earnings_move(history, earnings_date: dt.date) -> Optional[float]:
    """Percent price change across an earnings report.

    Compares the last close *before* the earnings date with the first close
    *after* it, so the reaction is captured whether the company reported
    before the open or after the close. ``history`` is a daily OHLC frame
    from ``yfinance``; returns ``None`` when it doesn't bracket the date.
    """
    if history is None or getattr(history, "empty", True):
        return None
    closes = []
    for idx, row in history.iterrows():
        d = _to_date(idx)
        c = _as_float(row.get("Close"))
        if d is not None and c is not None:
            closes.append((d, c))
    closes.sort()
    before = [c for d, c in closes if d < earnings_date]
    after = [c for d, c in closes if d > earnings_date]
    if not before or not after or before[-1] <= 0:
        return None
    return (after[0] - before[-1]) / before[-1] * 100


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
