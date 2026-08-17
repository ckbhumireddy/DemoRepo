"""Portfolio views, watch-item rules, and midday alert triggers (all pure).

Every suggestion is soft and tied to a stated fact. Two thresholds are
module constants rather than config knobs: UNDERWATER_PCT (a position this
far below cost triggers a review suggestion) and WINNER_GAIN_PCT (a gain
this large plus elevated weight triggers a rebalance suggestion).
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from earnings_analyzer.models import PriceBar
from earnings_notifier.earnings import EarningsEvent

from .portfolio import Position, PortfolioSnapshot
from .quotes import BENCHMARK, Quote

UNDERWATER_PCT = 20.0
WINNER_GAIN_PCT = 50.0
# Noise floor: per-position insights and alerts only for positions worth at
# least this much — a lottery-ticket holding down 95% is not a watch item.
MIN_INSIGHT_VALUE = 500.0
# A "volatility spike" additionally requires a move of at least this many
# percent — 0.1% vs a 0.05% norm is technically 2x and practically nothing.
MIN_SPIKE_PCT = 1.0


@dataclass
class PositionView:
    position: Position
    quote: Optional[Quote]
    market_value: Optional[float] = None
    weight_pct: Optional[float] = None
    day_change_pct: Optional[float] = None
    day_pnl: Optional[float] = None
    total_pnl: Optional[float] = None
    total_pnl_pct: Optional[float] = None
    vs_spy_pct: Optional[float] = None

    @property
    def ticker(self) -> str:
        return self.position.ticker


@dataclass
class PortfolioView:
    total_value: Optional[float] = None    # positions + cash (where known)
    cash: Optional[float] = None
    day_pnl: Optional[float] = None
    day_change_pct: Optional[float] = None
    total_pnl: Optional[float] = None
    spy_day_pct: Optional[float] = None
    unquoted: int = 0                      # positions without a quote


@dataclass(frozen=True)
class Insight:
    category: str      # "concentration" | "vol-spike" | "earnings" | ...
    ticker: str
    fact: str
    suggestion: str


@dataclass(frozen=True)
class AlertTrigger:
    kind: str          # "position" | "portfolio"
    ticker: str        # "PORTFOLIO" for kind == "portfolio"
    detail: str


def _significant(v: "PositionView") -> bool:
    """Above the noise floor: worth mentioning in insights and alerts."""
    return v.market_value is not None and abs(v.market_value) >= MIN_INSIGHT_VALUE


def build_views(
    snapshot: PortfolioSnapshot, quotes: Dict[str, Quote]
) -> Tuple[List[PositionView], PortfolioView]:
    spy = quotes.get(BENCHMARK)
    spy_day = spy.day_change_pct if spy else None

    views: List[PositionView] = []
    for p in snapshot.positions:
        v = PositionView(position=p, quote=quotes.get(p.ticker))
        q = v.quote
        if q is not None:
            v.market_value = round(p.quantity * q.last, 2)
            v.day_change_pct = q.day_change_pct
            if q.prev_close is not None:
                v.day_pnl = round((q.last - q.prev_close) * p.quantity, 2)
            if p.cost_basis is not None:
                v.total_pnl = round((q.last - p.cost_basis) * p.quantity, 2)
                if p.cost_basis > 0:
                    v.total_pnl_pct = round(
                        (q.last - p.cost_basis) / p.cost_basis * 100.0, 2
                    )
            if v.day_change_pct is not None and spy_day is not None:
                v.vs_spy_pct = round(v.day_change_pct - spy_day, 2)
        views.append(v)

    invested = sum(abs(v.market_value) for v in views if v.market_value)
    for v in views:
        if v.market_value is not None and invested > 0:
            v.weight_pct = round(abs(v.market_value) / invested * 100.0, 2)
    # Largest holdings first — the email reads top-down by importance.
    views.sort(key=lambda v: -(abs(v.market_value) if v.market_value else 0.0))

    valued = [v for v in views if v.market_value is not None]
    portfolio = PortfolioView(
        cash=snapshot.cash,
        spy_day_pct=spy_day,
        unquoted=len(views) - len(valued),
    )
    if valued:
        total = sum(v.market_value for v in valued) + (snapshot.cash or 0.0)
        portfolio.total_value = round(total, 2)
        day_pnls = [v.day_pnl for v in valued if v.day_pnl is not None]
        if day_pnls:
            day_pnl = sum(day_pnls)
            portfolio.day_pnl = round(day_pnl, 2)
            prior = total - day_pnl
            if prior > 0:
                portfolio.day_change_pct = round(day_pnl / prior * 100.0, 2)
        total_pnls = [v.total_pnl for v in valued if v.total_pnl is not None]
        if total_pnls:
            portfolio.total_pnl = round(sum(total_pnls), 2)
    return views, portfolio


def volatility_spikes(
    views: List[PositionView],
    histories: Dict[str, List[PriceBar]],
    mult: float,
) -> List[Insight]:
    """Positions whose day move dwarfs their own 30-day typical move."""
    out: List[Insight] = []
    for v in views:
        if v.day_change_pct is None or not _significant(v):
            continue
        if abs(v.day_change_pct) < MIN_SPIKE_PCT:
            continue
        bars = sorted(histories.get(v.ticker, []), key=lambda b: b.day)[-31:]
        closes = [b.close for b in bars if b.close > 0]
        if len(closes) < 10:
            continue
        moves = [
            abs(b / a - 1.0) * 100.0
            for a, b in zip(closes, closes[1:])
            if a > 0
        ]
        avg = sum(moves) / len(moves) if moves else 0.0
        if avg > 0 and abs(v.day_change_pct) >= mult * avg:
            out.append(Insight(
                category="vol-spike",
                ticker=v.ticker,
                fact=(
                    f"{v.ticker} moved {v.day_change_pct:+.1f}% today vs its "
                    f"30-day typical ±{avg:.1f}%."
                ),
                suggestion=(
                    "Unusual move — worth checking the news before the next "
                    "session."
                ),
            ))
    return out


def earnings_ahead(
    events: List[EarningsEvent], today: dt.date, event_days: int
) -> List[Insight]:
    out: List[Insight] = []
    for e in events:
        days = (e.date - today).days
        if not 0 <= days <= event_days:
            continue
        timing = e.timing or "timing unknown"
        out.append(Insight(
            category="earnings",
            ticker=e.ticker,
            fact=(
                f"{e.ticker} reports earnings {e.date.strftime('%a %b %d')} "
                f"({timing})."
            ),
            suggestion=(
                "Review exposure before the print — earnings gaps can jump "
                "past stops."
            ),
        ))
    return out


def generate_insights(
    views: List[PositionView],
    vol_spikes: List[Insight],
    events: List[Insight],
    *,
    max_weight_pct: float,
    move_alert_pct: float,
) -> List[Insight]:
    """The full watch-item list, ordered by severity category."""
    insights: List[Insight] = []
    concentrated = set()

    for v in views:  # rule 1: concentration
        if v.weight_pct is not None and v.weight_pct >= max_weight_pct:
            concentrated.add(v.ticker)
            insights.append(Insight(
                category="concentration",
                ticker=v.ticker,
                fact=(
                    f"{v.ticker} is {v.weight_pct:.1f}% of your portfolio "
                    f"(threshold {max_weight_pct:.0f}%)."
                ),
                suggestion=(
                    "Consider trimming or hedging to reduce single-name risk."
                ),
            ))

    insights.extend(vol_spikes)   # rule 2

    # Rule 3: earnings ahead — only for positions above the noise floor.
    significant = {v.ticker for v in views if _significant(v)}
    insights.extend(i for i in events if i.ticker in significant)

    for v in views:  # rule 4: big loss day
        if not _significant(v):
            continue
        if v.day_change_pct is not None and v.day_change_pct <= -move_alert_pct:
            pnl = f" ({v.day_pnl:+,.0f})" if v.day_pnl is not None else ""
            insights.append(Insight(
                category="big-loss",
                ticker=v.ticker,
                fact=f"{v.ticker} fell {v.day_change_pct:.1f}% today{pnl}.",
                suggestion=(
                    "Consider whether today's news changes your thesis; no "
                    "action required."
                ),
            ))

    # Rule 5: badly under water — one consolidated line, not one per ticker
    # (a long portfolio can hold a dozen of these; a list reads better).
    underwater = [
        v for v in views
        if _significant(v)
        and v.total_pnl_pct is not None
        and v.total_pnl_pct <= -UNDERWATER_PCT
    ]
    if underwater:
        underwater.sort(key=lambda v: v.total_pnl_pct)
        listing = " · ".join(
            f"{v.ticker} {v.total_pnl_pct:.0f}%" for v in underwater
        )
        insights.append(Insight(
            category="underwater",
            ticker="",
            fact=(
                f"{len(underwater)} position(s) are 20%+ below your average "
                f"cost: {listing}."
            ),
            suggestion=(
                "Consider whether each still earns its place — or whether "
                "tax-loss harvesting applies."
            ),
        ))

    for v in views:  # rule 6: winner concentration creep (1 wins over 6)
        if v.ticker in concentrated or not _significant(v):
            continue
        if (
            v.total_pnl_pct is not None
            and v.total_pnl_pct >= WINNER_GAIN_PCT
            and v.weight_pct is not None
            and v.weight_pct >= 0.75 * max_weight_pct
        ):
            insights.append(Insight(
                category="winner-creep",
                ticker=v.ticker,
                fact=(
                    f"{v.ticker} has grown to {v.weight_pct:.1f}% of the "
                    f"portfolio, up {v.total_pnl_pct:.0f}% on your cost."
                ),
                suggestion=(
                    "Gains have concentrated risk here — consider rebalancing "
                    "a slice."
                ),
            ))
    return insights


def check_alert_triggers(
    views: List[PositionView],
    portfolio: PortfolioView,
    *,
    move_alert_pct: float,
    portfolio_alert_pct: float,
    move_alert_dollars: float = 5000.0,
    portfolio_alert_dollars: float = 15000.0,
) -> List[AlertTrigger]:
    """Percent OR dollar thresholds trigger — a large position loses real
    money on a percent move far below the percent gate."""
    triggers: List[AlertTrigger] = []
    pct = portfolio.day_change_pct
    pnl = portfolio.day_pnl
    if (pct is not None and abs(pct) >= portfolio_alert_pct) or (
        pnl is not None and abs(pnl) >= portfolio_alert_dollars
    ):
        detail = "portfolio"
        if pct is not None:
            detail += f" {pct:+.1f}%"
        if pnl is not None:
            detail += f" ({pnl:+,.0f})"
        triggers.append(
            AlertTrigger(kind="portfolio", ticker="PORTFOLIO", detail=detail)
        )
    for v in views:
        if not _significant(v):
            continue
        pct_hit = (
            v.day_change_pct is not None
            and abs(v.day_change_pct) >= move_alert_pct
        )
        dollar_hit = (
            v.day_pnl is not None and abs(v.day_pnl) >= move_alert_dollars
        )
        if pct_hit or dollar_hit:
            detail = v.ticker
            if v.day_change_pct is not None:
                detail += f" {v.day_change_pct:+.1f}%"
            if v.day_pnl is not None:
                detail += f" ({v.day_pnl:+,.0f})"
            triggers.append(
                AlertTrigger(kind="position", ticker=v.ticker, detail=detail)
            )
    return triggers


def biggest_movers(
    views: List[PositionView], n: int = 3
) -> Tuple[List[PositionView], List[PositionView]]:
    """Top gainers/losers by day percentage (significant positions only)."""
    moved = [
        v for v in views if v.day_change_pct is not None and _significant(v)
    ]
    moved.sort(key=lambda v: v.day_change_pct, reverse=True)
    gainers = [v for v in moved[:n] if v.day_change_pct > 0]
    losers = [v for v in reversed(moved[-n:]) if v.day_change_pct < 0]
    return gainers, losers


def biggest_dollar_movers(
    views: List[PositionView], n: int = 3
) -> Tuple[List[PositionView], List[PositionView]]:
    """Top gainers/losers by day P&L dollars — a big position on a small
    percent move often matters more than a small one on a big move."""
    moved = [v for v in views if v.day_pnl is not None and _significant(v)]
    moved.sort(key=lambda v: v.day_pnl, reverse=True)
    gainers = [v for v in moved[:n] if v.day_pnl > 0]
    losers = [v for v in reversed(moved[-n:]) if v.day_pnl < 0]
    return gainers, losers
