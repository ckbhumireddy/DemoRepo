"""Market-data access for the analyzer.

``MarketDataProvider`` is the seam between analysis (pure) and the outside
world. ``YFinanceMarketData`` talks to Yahoo Finance with the same
retry/backoff posture as the notifier's provider; ``InMemoryProvider`` backs
tests and the offline demo. Parsing helpers are module-level pure functions
so they can be unit-tested with canned frames.
"""

from __future__ import annotations

import datetime as dt
import logging
import time
from typing import Callable, Dict, List, Optional, Protocol, Tuple, TypeVar

from earnings_notifier.earnings import _as_float, _to_date

from .models import OptionChain, OptionContract, PriceBar, QuarterResult, Snapshot

logger = logging.getLogger(__name__)

T = TypeVar("T")


class MarketDataProvider(Protocol):
    """Everything the analyzer needs to know about a ticker."""

    def earnings_history(self, ticker: str, limit: int = 20) -> List[QuarterResult]:
        ...

    def price_history(self, ticker: str, days: int = 700) -> List[PriceBar]:
        ...

    def snapshot(self, ticker: str) -> Optional[Snapshot]:
        ...

    def option_expiries(self, ticker: str) -> List[dt.date]:
        ...

    def option_chain(self, ticker: str, expiry: dt.date) -> Optional[OptionChain]:
        ...


# --------------------------------------------------------------------------- #
# Pure parsing helpers (testable without network)
# --------------------------------------------------------------------------- #
def quarters_from_frame(frame, today: dt.date) -> List[QuarterResult]:
    """Past rows of a yfinance ``get_earnings_dates`` frame, newest first."""
    out: List[QuarterResult] = []
    if frame is None or getattr(frame, "empty", True):
        return out
    for idx, row in frame.iterrows():
        d = _to_date(idx)
        if d is None or d >= today:
            continue
        actual = _as_float(row.get("Reported EPS"))
        if actual is None:
            continue
        out.append(
            QuarterResult(
                date=d,
                eps_estimate=_as_float(row.get("EPS Estimate")),
                eps_actual=actual,
                surprise_pct=_as_float(row.get("Surprise(%)")),
            )
        )
    return sorted(out, key=lambda q: q.date, reverse=True)


def bars_from_frame(frame) -> List[PriceBar]:
    """Convert a yfinance ``history`` frame into :class:`PriceBar` rows."""
    bars: List[PriceBar] = []
    if frame is None or getattr(frame, "empty", True):
        return bars
    for idx, row in frame.iterrows():
        day = _to_date(idx)
        close = _as_float(row.get("Close"))
        if day is None or close is None or close <= 0:
            continue
        bars.append(
            PriceBar(
                day=day,
                open=_as_float(row.get("Open")) or close,
                high=_as_float(row.get("High")) or close,
                low=_as_float(row.get("Low")) or close,
                close=close,
                volume=_as_float(row.get("Volume")) or 0.0,
            )
        )
    return bars


def contracts_from_frame(frame, option_type: str, expiry: dt.date) -> List[OptionContract]:
    contracts: List[OptionContract] = []
    if frame is None or getattr(frame, "empty", True):
        return contracts
    for _, row in frame.iterrows():
        strike = _as_float(row.get("strike"))
        if strike is None:
            continue
        itm = row.get("inTheMoney")
        contracts.append(
            OptionContract(
                contract_symbol=str(row.get("contractSymbol", "")),
                option_type=option_type,
                strike=strike,
                expiry=expiry,
                bid=_as_float(row.get("bid")),
                ask=_as_float(row.get("ask")),
                last=_as_float(row.get("lastPrice")),
                implied_volatility=_as_float(row.get("impliedVolatility")),
                volume=_as_float(row.get("volume")),
                open_interest=_as_float(row.get("openInterest")),
                in_the_money=bool(itm) if itm is not None else None,
            )
        )
    return contracts


def snapshot_from_info(info: dict) -> Optional[Snapshot]:
    if not info:
        return None
    return Snapshot(
        company=info.get("longName") or info.get("shortName"),
        sector=info.get("sector"),
        market_cap=_as_float(info.get("marketCap")),
        forward_pe=_as_float(info.get("forwardPE")),
        revenue_growth=_as_float(info.get("revenueGrowth")),
        profit_margin=_as_float(info.get("profitMargins")),
        fifty_two_week_low=_as_float(info.get("fiftyTwoWeekLow")),
        fifty_two_week_high=_as_float(info.get("fiftyTwoWeekHigh")),
        short_pct_float=_as_float(info.get("shortPercentOfFloat")),
        short_ratio=_as_float(info.get("shortRatio")),
    )


# --------------------------------------------------------------------------- #
# Live provider
# --------------------------------------------------------------------------- #
class YFinanceMarketData:
    """Yahoo-backed provider with the notifier's retry/backoff posture.

    Defaults retry harder than the notifier (4 attempts, growing 2s backoff):
    the analyzer often runs right after a 500-ticker scan and Yahoo throttles
    the earnings-dates endpoint aggressively; with only ~13 tickers the extra
    patience is cheap and losing history guts the whole sheet.
    """

    def __init__(
        self,
        today: Optional[dt.date] = None,
        attempts: int = 4,
        backoff: float = 2.0,
    ) -> None:
        self._today = today
        self.attempts = max(1, attempts)
        self.backoff = backoff

    def _now(self) -> dt.date:
        return self._today or dt.date.today()

    def _ticker(self, ticker: str):
        import yfinance as yf

        return yf.Ticker(ticker)

    def _retry(self, what: str, fn: Callable[[], T], empty: T) -> T:
        for attempt in range(self.attempts):
            try:
                result = fn()
                if result is not None:
                    return result
            except Exception as exc:  # noqa: BLE001 - degrade, never abort
                logger.debug("%s failed (attempt %d): %s", what, attempt + 1, exc)
            if attempt < self.attempts - 1 and self.backoff:
                time.sleep(self.backoff * (attempt + 1))
        return empty

    def earnings_history(self, ticker: str, limit: int = 20) -> List[QuarterResult]:
        def _fetch():
            frame = self._ticker(ticker).get_earnings_dates(limit=limit)
            # A throttled response is often an empty frame, not an exception —
            # map it to None so _retry treats it as a failure worth retrying.
            return quarters_from_frame(frame, self._now()) or None

        return self._retry(f"earnings history {ticker}", _fetch, [])

    def price_history(self, ticker: str, days: int = 700) -> List[PriceBar]:
        def _fetch():
            frame = self._ticker(ticker).history(
                period=f"{max(days, 30)}d", interval="1d", auto_adjust=True
            )
            return bars_from_frame(frame) or None

        return self._retry(f"price history {ticker}", _fetch, [])

    def snapshot(self, ticker: str) -> Optional[Snapshot]:
        def _fetch():
            return snapshot_from_info(self._ticker(ticker).info or {})

        return self._retry(f"snapshot {ticker}", _fetch, None)

    def option_expiries(self, ticker: str) -> List[dt.date]:
        def _fetch():
            raw = self._ticker(ticker).options or ()
            out = []
            for s in raw:
                try:
                    out.append(dt.datetime.strptime(s, "%Y-%m-%d").date())
                except (TypeError, ValueError):
                    continue
            return sorted(out) or None

        return self._retry(f"option expiries {ticker}", _fetch, [])

    def option_chain(self, ticker: str, expiry: dt.date) -> Optional[OptionChain]:
        def _fetch():
            tk = self._ticker(ticker)
            raw = tk.option_chain(expiry.isoformat())
            underlying = 0.0
            try:
                fast = getattr(tk, "fast_info", None)
                underlying = _as_float(getattr(fast, "last_price", None)) or 0.0
            except Exception:  # noqa: BLE001
                underlying = 0.0
            calls = contracts_from_frame(getattr(raw, "calls", None), "call", expiry)
            puts = contracts_from_frame(getattr(raw, "puts", None), "put", expiry)
            if not calls and not puts:
                return None  # throttled/empty chain -> retry
            return OptionChain(
                ticker=ticker.upper(),
                expiry=expiry,
                underlying_price=underlying,
                calls=calls,
                puts=puts,
            )

        return self._retry(f"option chain {ticker} {expiry}", _fetch, None)


# --------------------------------------------------------------------------- #
# Offline provider
# --------------------------------------------------------------------------- #
class InMemoryProvider:
    """Canned data for tests and the offline demo."""

    def __init__(self) -> None:
        self._quarters: Dict[str, List[QuarterResult]] = {}
        self._bars: Dict[str, List[PriceBar]] = {}
        self._snapshots: Dict[str, Snapshot] = {}
        self._chains: Dict[Tuple[str, dt.date], OptionChain] = {}

    def add(
        self,
        ticker: str,
        quarters: Optional[List[QuarterResult]] = None,
        bars: Optional[List[PriceBar]] = None,
        snapshot: Optional[Snapshot] = None,
    ) -> None:
        if quarters is not None:
            self._quarters[ticker] = quarters
        if bars is not None:
            self._bars[ticker] = bars
        if snapshot is not None:
            self._snapshots[ticker] = snapshot

    def add_option_chain(self, chain: OptionChain) -> None:
        self._chains[(chain.ticker, chain.expiry)] = chain

    def earnings_history(self, ticker: str, limit: int = 20) -> List[QuarterResult]:
        return self._quarters.get(ticker, [])[:limit]

    def price_history(self, ticker: str, days: int = 700) -> List[PriceBar]:
        return self._bars.get(ticker, [])

    def snapshot(self, ticker: str) -> Optional[Snapshot]:
        return self._snapshots.get(ticker)

    def option_expiries(self, ticker: str) -> List[dt.date]:
        return sorted(e for t, e in self._chains if t == ticker)

    def option_chain(self, ticker: str, expiry: dt.date) -> Optional[OptionChain]:
        return self._chains.get((ticker, expiry))
