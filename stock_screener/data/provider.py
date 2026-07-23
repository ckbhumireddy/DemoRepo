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

from .models import Fundamentals, PriceBar


class MarketDataProvider(ABC):
    """Abstract source of fundamentals, prices, and earnings dates."""

    @abstractmethod
    def get_fundamentals(self, ticker: str) -> Optional[Fundamentals]:
        ...

    @abstractmethod
    def get_price_history(self, ticker: str, days: int = 120) -> List[PriceBar]:
        ...

    @abstractmethod
    def get_last_earnings_date(self, ticker: str) -> Optional[date]:
        ...


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

    def get_fundamentals(self, ticker: str) -> Optional[Fundamentals]:
        return self._fundamentals.get(ticker)

    def get_price_history(self, ticker: str, days: int = 120) -> List[PriceBar]:
        return list(self._prices.get(ticker, []))[-days:]

    def get_last_earnings_date(self, ticker: str) -> Optional[date]:
        return self._earnings.get(ticker)
