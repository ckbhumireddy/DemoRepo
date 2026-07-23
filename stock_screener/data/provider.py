"""Market-data provider interface plus an in-memory provider for tests.

Keeping data access behind an abstract interface means the analysis and CLI
never import yfinance directly. That makes the whole pipeline testable offline
(via :class:`InMemoryProvider`) and leaves the door open for paid providers
later without touching the rest of the code.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date
from typing import Dict, List, Optional, Tuple

from .models import Fundamentals, OptionChain, OptionContract, PriceBar  # noqa: F401


class MarketDataProvider(ABC):
    """Abstract source of fundamentals, prices, earnings dates, and options.

    Option-chain access is optional: providers that can't supply it inherit the
    default no-op implementations below and the screener simply skips options
    enrichment for them.
    """

    @abstractmethod
    def get_fundamentals(self, ticker: str) -> Optional[Fundamentals]:
        ...

    @abstractmethod
    def get_price_history(self, ticker: str, days: int = 120) -> List[PriceBar]:
        ...

    @abstractmethod
    def get_last_earnings_date(self, ticker: str) -> Optional[date]:
        ...

    # ---- Optional options-chain support ------------------------------------
    def supports_options(self) -> bool:
        return False

    def get_option_expiries(self, ticker: str) -> List[date]:
        return []

    def get_option_chain(self, ticker: str, expiry: date) -> Optional[OptionChain]:
        return None


class InMemoryProvider(MarketDataProvider):
    """A provider backed by dictionaries -- used in tests and demos.

    Seed it with data keyed by ticker and it behaves like a real provider
    without any network access.
    """

    def __init__(
        self,
        fundamentals: Optional[Dict[str, Fundamentals]] = None,
        prices: Optional[Dict[str, List[PriceBar]]] = None,
        earnings: Optional[Dict[str, date]] = None,
    ) -> None:
        self._fundamentals = fundamentals or {}
        self._prices = prices or {}
        self._earnings = earnings or {}
        self._chains: Dict[Tuple[str, str], OptionChain] = {}
        self._expiries: Dict[str, List[date]] = {}

    def add(
        self,
        fundamentals: Fundamentals,
        prices: List[PriceBar],
        earnings_date: Optional[date],
    ) -> None:
        t = fundamentals.ticker
        self._fundamentals[t] = fundamentals
        self._prices[t] = prices
        if earnings_date is not None:
            self._earnings[t] = earnings_date

    def add_option_chain(self, chain: OptionChain) -> None:
        t = chain.ticker
        self._chains[(t, chain.expiry.isoformat())] = chain
        self._expiries.setdefault(t, [])
        if chain.expiry not in self._expiries[t]:
            self._expiries[t].append(chain.expiry)
            self._expiries[t].sort()

    def get_fundamentals(self, ticker: str) -> Optional[Fundamentals]:
        return self._fundamentals.get(ticker)

    def get_price_history(self, ticker: str, days: int = 120) -> List[PriceBar]:
        return list(self._prices.get(ticker, []))[-days:]

    def get_last_earnings_date(self, ticker: str) -> Optional[date]:
        return self._earnings.get(ticker)

    def supports_options(self) -> bool:
        return bool(self._chains)

    def get_option_expiries(self, ticker: str) -> List[date]:
        return list(self._expiries.get(ticker, []))

    def get_option_chain(self, ticker: str, expiry: date) -> Optional[OptionChain]:
        return self._chains.get((ticker, expiry.isoformat()))
