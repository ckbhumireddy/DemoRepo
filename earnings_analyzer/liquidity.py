"""Options liquidity screen (pure functions).

Wide spreads eat any theoretical edge, and thin open interest means bad
fills both in and out. Names that fail here get a "no trade" badge and no
strategy suggestions, regardless of how attractive the setup looks.
"""

from __future__ import annotations

from typing import List, Optional

from .models import LiquidityReport, OptionChain, OptionContract


def _avg(values: List[Optional[float]]) -> Optional[float]:
    known = [v for v in values if v is not None]
    return sum(known) / len(known) if known else None


def screen_liquidity(
    chain: OptionChain,
    min_open_interest: float = 200,
    min_option_volume: float = 50,
    max_spread_pct: float = 8.0,
) -> LiquidityReport:
    """Judge tradeability from the ATM call + put of the event-expiry chain."""
    atm: List[OptionContract] = [
        c for c in (chain.atm_call(), chain.atm_put()) if c is not None
    ]
    if not atm:
        return LiquidityReport(tradeable=False, reasons=["no ATM quotes"])

    oi = _avg([c.open_interest for c in atm])
    volume = _avg([c.volume for c in atm])
    spread = _avg([c.spread_pct for c in atm])

    reasons: List[str] = []
    if oi is None or oi < min_open_interest:
        reasons.append(
            f"open interest {oi:.0f} < {min_open_interest:.0f}"
            if oi is not None
            else "open interest unavailable"
        )
    if volume is None or volume < min_option_volume:
        reasons.append(
            f"volume {volume:.0f} < {min_option_volume:.0f}"
            if volume is not None
            else "volume unavailable"
        )
    if spread is None or spread > max_spread_pct:
        reasons.append(
            f"spread {spread:.1f}% > {max_spread_pct:.0f}%"
            if spread is not None
            else "spread unavailable"
        )

    return LiquidityReport(
        atm_open_interest=oi,
        atm_volume=volume,
        atm_spread_pct=round(spread, 2) if spread is not None else None,
        tradeable=not reasons,
        reasons=reasons,
    )
