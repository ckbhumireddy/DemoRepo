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
class OptionContract:
    """A single quoted option contract from a live chain."""

    contract_symbol: str
    option_type: str                # "call" or "put"
    strike: float
    expiry: date
    bid: Optional[float] = None
    ask: Optional[float] = None
    last: Optional[float] = None
    implied_volatility: Optional[float] = None   # annualized decimal, 0.35 = 35%
    volume: Optional[float] = None
    open_interest: Optional[float] = None
    in_the_money: Optional[bool] = None

    @property
    def mid(self) -> Optional[float]:
        """Mid price from bid/ask, falling back to last."""
        if self.bid is not None and self.ask is not None and self.ask >= self.bid >= 0:
            m = (self.bid + self.ask) / 2.0
            if m > 0:
                return round(m, 4)
        return self.last

    @property
    def days_to_expiry_from(self):  # helper used with an explicit 'today'
        return lambda today: (self.expiry - today).days


@dataclass
class OptionChain:
    """Calls and puts for one ticker at one expiry."""

    ticker: str
    expiry: date
    underlying_price: float
    calls: List["OptionContract"] = field(default_factory=list)
    puts: List["OptionContract"] = field(default_factory=list)

    def _nearest(self, contracts: List["OptionContract"]) -> Optional["OptionContract"]:
        if not contracts:
            return None
        return min(contracts, key=lambda c: abs(c.strike - self.underlying_price))

    def atm_call(self) -> Optional["OptionContract"]:
        return self._nearest(self.calls)

    def atm_put(self) -> Optional["OptionContract"]:
        return self._nearest(self.puts)

    def atm_iv(self) -> Optional[float]:
        """Average of the ATM call and put implied vols (whichever exist)."""
        ivs = [
            c.implied_volatility
            for c in (self.atm_call(), self.atm_put())
            if c is not None and c.implied_volatility is not None
        ]
        return round(sum(ivs) / len(ivs), 4) if ivs else None


@dataclass
class IVRank:
    """Where current implied volatility sits in its trailing range.

    True IV rank uses a year of historical *implied* vol, which free feeds do
    not expose. We approximate using the trailing realized-volatility range as
    the reference, and label the method so the limitation is explicit.
    """

    ticker: str
    current_iv: float               # annualized decimal
    hv_low: float                   # trailing realized-vol low
    hv_high: float                  # trailing realized-vol high
    iv_rank: float                  # 0-1 within [hv_low, hv_high]
    iv_percentile: float            # 0-1 fraction of days below current
    window_days: int
    sample_size: int
    method: str = "IV vs trailing realized-vol range (proxy)"

    @property
    def rank_pct(self) -> float:
        return self.iv_rank * 100.0

    @property
    def is_elevated(self) -> bool:
        """Rich premium -> favors selling. Threshold at the 50th rank."""
        return self.iv_rank >= 0.5


@dataclass
class OptionLeg:
    """One leg of a priced strategy."""

    action: str                     # "buy" or "sell"
    option_type: str                # "call" or "put"
    strike: float
    expiry: date
    price: float                    # per-share mid used in the calculation
    implied_volatility: Optional[float] = None


@dataclass
class PricedStrategy:
    """A concrete, priced version of a strategy idea built from live quotes.

    All monetary figures are per share; multiply by 100 for one contract.
    Credits are positive when you receive premium; debits are negative net.
    """

    strategy: str
    outlook: str
    legs: List[OptionLeg] = field(default_factory=list)
    net_premium: float = 0.0        # >0 = credit received, <0 = debit paid
    max_profit: Optional[float] = None
    max_loss: Optional[float] = None
    breakeven: Optional[float] = None
    notes: str = ""

    @property
    def is_credit(self) -> bool:
        return self.net_premium > 0


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
    iv_rank: Optional[IVRank] = None
    priced_strategies: List[PricedStrategy] = field(default_factory=list)

    @property
    def name(self) -> str:
        return self.fundamentals.name or self.ticker
