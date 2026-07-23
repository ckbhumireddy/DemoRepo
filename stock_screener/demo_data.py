"""Synthetic, offline data for the ``demo`` command and the test suite.

Builds an :class:`InMemoryProvider` with a handful of fictional-but-realistic
scenarios so the whole pipeline can be exercised without touching the network.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import List, Tuple

from .data.models import Fundamentals, PriceBar
from .data.provider import InMemoryProvider


def _flat_then_crash(
    start_day: date,
    days: int,
    base_price: float,
    earnings_offset: int,
    drop_pct: float,
    recovery_pct: float = 0.0,
) -> Tuple[List[PriceBar], date]:
    """Build a price series that is flat, gaps down on earnings, then drifts.

    Returns the bars plus the earnings date.
    """
    bars: List[PriceBar] = []
    earnings_date = start_day + timedelta(days=earnings_offset)
    price = base_price
    trough = base_price * (1 + drop_pct)
    for i in range(days):
        day = start_day + timedelta(days=i)
        if day < earnings_date:
            price = base_price
        elif day == earnings_date:
            price = base_price * (1 + drop_pct)  # the gap-down reaction
        else:
            # Drift from trough back up toward (pre * (1+recovery)).
            steps_after = (day - earnings_date).days
            target = base_price * (1 + recovery_pct)
            price = trough + (target - trough) * min(1.0, steps_after / 8.0)
        bars.append(
            PriceBar(
                day=day,
                open=price,
                high=price * 1.01,
                low=price * 0.99,
                close=round(price, 2),
                volume=1_000_000,
            )
        )
    return bars, earnings_date


def build_demo_provider() -> Tuple[InMemoryProvider, List[str], date]:
    """Return ``(provider, tickers, today)`` for offline demos/tests."""
    today = date(2024, 6, 1)
    start = today - timedelta(days=60)
    provider = InMemoryProvider()

    # 1) GOODCO: strong fundamentals, real ~18% earnings crash, still down.
    good = Fundamentals(
        ticker="GOODCO", name="Good Company Inc.", sector="Technology",
        price=82.0, market_cap=50_000_000_000, trailing_pe=22.0, forward_pe=18.0,
        profit_margin=0.20, operating_margin=0.25, return_on_equity=0.28,
        free_cash_flow=4_000_000_000, revenue_growth=0.14, earnings_growth=0.10,
        debt_to_equity=0.4, current_ratio=1.8,
    )
    good_bars, good_earn = _flat_then_crash(
        start, 60, base_price=100.0, earnings_offset=40,
        drop_pct=-0.18, recovery_pct=-0.10,
    )
    provider.add(good, good_bars, good_earn)

    # 2) WEAKCO: crashed hard, but weak fundamentals -> should be filtered out.
    weak = Fundamentals(
        ticker="WEAKCO", name="Weak Company Co.", sector="Consumer",
        price=15.0, market_cap=3_000_000_000, trailing_pe=-5.0, forward_pe=90.0,
        profit_margin=-0.10, operating_margin=-0.05, return_on_equity=-0.20,
        free_cash_flow=-500_000_000, revenue_growth=-0.08, earnings_growth=-0.40,
        debt_to_equity=3.5, current_ratio=0.6,
    )
    weak_bars, weak_earn = _flat_then_crash(
        start, 60, base_price=25.0, earnings_offset=42,
        drop_pct=-0.30, recovery_pct=-0.20,
    )
    provider.add(weak, weak_bars, weak_earn)

    # 3) STEADYCO: great fundamentals but no crash -> filtered out.
    steady = Fundamentals(
        ticker="STEADYCO", name="Steady Eddie Corp.", sector="Healthcare",
        price=200.0, market_cap=120_000_000_000, trailing_pe=25.0, forward_pe=23.0,
        profit_margin=0.22, operating_margin=0.28, return_on_equity=0.30,
        free_cash_flow=8_000_000_000, revenue_growth=0.09, earnings_growth=0.08,
        debt_to_equity=0.5, current_ratio=2.1,
    )
    steady_bars, steady_earn = _flat_then_crash(
        start, 60, base_price=200.0, earnings_offset=41,
        drop_pct=-0.01, recovery_pct=0.02,
    )
    provider.add(steady, steady_bars, steady_earn)

    # 4) SOLIDCO: solid fundamentals, ~12% crash, deeper score than GOODCO on
    #    crash severity but slightly lower quality -> tests ranking.
    solid = Fundamentals(
        ticker="SOLIDCO", name="Solid Foundations Ltd.", sector="Industrials",
        price=140.0, market_cap=30_000_000_000, trailing_pe=19.0, forward_pe=16.0,
        profit_margin=0.12, operating_margin=0.15, return_on_equity=0.18,
        free_cash_flow=2_000_000_000, revenue_growth=0.06, earnings_growth=0.05,
        debt_to_equity=0.9, current_ratio=1.4,
    )
    solid_bars, solid_earn = _flat_then_crash(
        start, 60, base_price=160.0, earnings_offset=43,
        drop_pct=-0.13, recovery_pct=-0.09,
    )
    provider.add(solid, solid_bars, solid_earn)

    tickers = ["GOODCO", "WEAKCO", "STEADYCO", "SOLIDCO"]
    return provider, tickers, today
