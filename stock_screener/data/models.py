"""Data models shared across the screener.

These are plain dataclasses with no external dependencies so they can be
constructed in tests without any network access or third-party libraries.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import List, Optional


@dataclass
class Fundamentals:
    """A snapshot of a company's fundamentals.

    Every numeric field is optional because data providers frequently return
    partial data. The analysis code is written to degrade gracefully when a
    metric is missing rather than crash.
    """

    ticker: str
    name: Optional[str] = None
    sector: Optional[str] = None
    price: Optional[float] = None
    market_cap: Optional[float] = None

    # Valuation
    trailing_pe: Optional[float] = None
    forward_pe: Optional[float] = None
    peg_ratio: Optional[float] = None

    # Profitability / quality
    profit_margin: Optional[float] = None          # net margin, e.g. 0.21 = 21%
    operating_margin: Optional[float] = None
    return_on_equity: Optional[float] = None        # e.g. 0.30 = 30%
    free_cash_flow: Optional[float] = None          # absolute, in currency

    # Growth
    revenue_growth: Optional[float] = None          # yoy, e.g. 0.15 = 15%
    earnings_growth: Optional[float] = None          # yoy

    # Balance sheet health
    debt_to_equity: Optional[float] = None          # as a ratio, e.g. 0.5
    current_ratio: Optional[float] = None
    total_cash: Optional[float] = None
    total_debt: Optional[float] = None

    dividend_yield: Optional[float] = None


@dataclass
class PriceBar:
    """A single daily OHLCV bar."""

    day: date
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0


@dataclass
class EarningsCrash:
    """Describes a post-earnings drop for a ticker."""

    ticker: str
    earnings_date: date
    pre_close: float            # last close before earnings
    reaction_close: float       # first close on/after the earnings reaction
    trough_close: float         # lowest close in the post-earnings window
    current_close: float        # most recent close
    reaction_drop_pct: float    # (reaction - pre) / pre  (negative = drop)
    max_drop_pct: float         # (trough - pre) / pre    (negative = drop)
    still_down_pct: float       # (current - pre) / pre
    days_since_earnings: int

    @property
    def recovered_from_trough_pct(self) -> float:
        if self.trough_close == 0:
            return 0.0
        return (self.current_close - self.trough_close) / self.trough_close


@dataclass
class OptionSuggestion:
    """An educational options-strategy idea for a candidate.

    This is *not* trade advice or an order ticket -- it describes a strategy
    template and where strikes/expiries might sit relative to the current
    price so the user can research a concrete trade themselves.
    """

    strategy: str
    outlook: str                # e.g. "bullish", "neutral-to-bullish"
    description: str
    strike_guidance: str
    risk_note: str


@dataclass
class Candidate:
    """A screened stock that passed the quality + crash filters."""

    ticker: str
    fundamentals: Fundamentals
    crash: EarningsCrash
    fundamental_score: float                 # 0-100
    data_coverage: float                     # 0-1, fraction of metrics available
    passed_criteria: List[str] = field(default_factory=list)
    failed_criteria: List[str] = field(default_factory=list)
    option_suggestions: List[OptionSuggestion] = field(default_factory=list)
    composite_score: float = 0.0             # blends quality + crash depth

    @property
    def name(self) -> str:
        return self.fundamentals.name or self.ticker
