"""Dataclasses for the analyzer.

Plain dataclasses with no external dependencies so they can be constructed
in tests without network access or third-party libraries. Every numeric
field is optional where providers may return partial data; ``None`` always
means "not available" and the analysis degrades gracefully.

The option/IV models are salvaged from the earlier stock-screener prototype.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Dict, List, Optional


# --------------------------------------------------------------------------- #
# Market data primitives
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class PriceBar:
    """A single daily OHLCV bar."""

    day: dt.date
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0


@dataclass
class OptionContract:
    """A single quoted option contract from a live chain."""

    contract_symbol: str
    option_type: str                # "call" or "put"
    strike: float
    expiry: dt.date
    bid: Optional[float] = None
    ask: Optional[float] = None
    last: Optional[float] = None
    close_price: Optional[float] = None   # previous session's close
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
    def spread_pct(self) -> Optional[float]:
        """Bid/ask spread as a percentage of the mid."""
        if self.bid is None or self.ask is None:
            return None
        m = self.mid
        if m is None or m <= 0:
            return None
        return (self.ask - self.bid) / m * 100.0


@dataclass
class OptionChain:
    """Calls and puts for one ticker at one expiry."""

    ticker: str
    expiry: dt.date
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


@dataclass
class OptionLeg:
    """One leg of a priced strategy."""

    action: str                     # "buy" or "sell"
    option_type: str                # "call" or "put"
    strike: float
    expiry: dt.date
    price: float                    # per-share mid used in the calculation
    implied_volatility: Optional[float] = None


@dataclass
class PricedStrategy:
    """A concrete, priced strategy built from live quotes.

    All monetary figures are per share; multiply by 100 for one contract.
    ``net_premium`` is positive for credits received, negative for debits.
    """

    strategy: str
    outlook: str
    legs: List[OptionLeg] = field(default_factory=list)
    net_premium: float = 0.0
    max_profit: Optional[float] = None
    max_loss: Optional[float] = None
    breakevens: List[float] = field(default_factory=list)
    notes: str = ""
    # Filled by backtest.attach_backtests: over the past reports, how often
    # this exact structure would have finished profitable.
    backtest_wins: Optional[int] = None
    backtest_total: Optional[int] = None

    @property
    def is_credit(self) -> bool:
        return self.net_premium > 0


# --------------------------------------------------------------------------- #
# Analysis results
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class QuarterResult:
    """One past quarterly report: estimate, actual, and the price reaction."""

    date: dt.date
    eps_estimate: Optional[float] = None
    eps_actual: Optional[float] = None
    surprise_pct: Optional[float] = None
    reaction_pct: Optional[float] = None   # close-before -> close-after, %


@dataclass(frozen=True)
class EarningsHistoryStats:
    quarters: int                          # sample size actually used
    beat_rate: Optional[float] = None      # 0-1
    avg_surprise_pct: Optional[float] = None
    streak: int = 0                        # +n consecutive beats, -n misses


@dataclass(frozen=True)
class ReactionStats:
    samples: int
    avg_abs_move_pct: Optional[float] = None
    up_rate: Optional[float] = None        # 0-1
    best_pct: Optional[float] = None
    worst_pct: Optional[float] = None


@dataclass(frozen=True)
class ImpliedMove:
    expiry: dt.date
    straddle_debit: float                  # per share
    implied_move_pct: float                # straddle / spot * 100
    historical_avg_abs_move_pct: Optional[float] = None
    ratio: Optional[float] = None          # implied / historical
    verdict: str = "unknown"               # "rich" | "fair" | "cheap" | "unknown"
    diluted: bool = False                  # expiry lands well past the event


@dataclass(frozen=True)
class LiquidityReport:
    atm_open_interest: Optional[float] = None
    atm_volume: Optional[float] = None
    atm_spread_pct: Optional[float] = None
    tradeable: bool = False
    reasons: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class TrendContext:
    price: float
    ma20: Optional[float] = None
    ma50: Optional[float] = None
    ma200: Optional[float] = None
    pct_of_52wk_range: Optional[float] = None   # 0-1
    bias: str = "neutral"                       # "bullish" | "bearish" | "neutral"


@dataclass(frozen=True)
class Snapshot:
    """Company fundamentals shown on the card."""

    company: Optional[str] = None
    sector: Optional[str] = None
    market_cap: Optional[float] = None
    forward_pe: Optional[float] = None
    revenue_growth: Optional[float] = None      # yoy, 0.15 = 15%
    profit_margin: Optional[float] = None
    fifty_two_week_low: Optional[float] = None
    fifty_two_week_high: Optional[float] = None
    short_pct_float: Optional[float] = None  # 0.037 = 3.7% of float sold short
    short_ratio: Optional[float] = None      # days to cover at avg volume


@dataclass(frozen=True)
class Rating:
    score: float                                # 0-100
    grade: str                                  # A+/A/B/C/D/F
    components: Dict[str, float] = field(default_factory=dict)
    coverage: float = 0.0                       # fraction of weight with data


@dataclass
class TickerAnalysis:
    """Everything the trade sheet knows about one upcoming earnings event."""

    ticker: str
    event_date: dt.date
    timing: Optional[str] = None  # "pre-market" | "after-market" | None
    price: Optional[float] = None
    snapshot: Optional[Snapshot] = None
    history: Optional[EarningsHistoryStats] = None
    reactions: Optional[ReactionStats] = None
    implied: Optional[ImpliedMove] = None
    iv_rank: Optional[IVRank] = None
    liquidity: Optional[LiquidityReport] = None
    trend: Optional[TrendContext] = None
    rating: Optional[Rating] = None
    strategies: List[PricedStrategy] = field(default_factory=list)
