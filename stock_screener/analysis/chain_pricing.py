"""Turn live option chains into concrete, priced strategies.

Given a near-dated chain (for premium-selling / defined-risk plays) and an
optional long-dated chain (for a LEAPS call), build :class:`PricedStrategy`
objects with real strikes, mid prices, net premium, max profit/loss, and
breakeven. All figures are per share; multiply by 100 for one contract.
"""

from __future__ import annotations

from datetime import date
from typing import List, Optional

from ..data.models import OptionChain, OptionContract, OptionLeg, PricedStrategy


def select_expiry(
    expiries: List[date],
    today: date,
    *,
    min_dte: int,
    max_dte: int,
) -> Optional[date]:
    """Pick the expiry whose DTE best fits ``[min_dte, max_dte]``.

    Prefers expiries inside the window (closest to its midpoint); otherwise the
    nearest expiry at least ``min_dte`` out; otherwise the farthest available.
    """
    future = sorted(e for e in expiries if (e - today).days >= 0)
    if not future:
        return None
    in_window = [e for e in future if min_dte <= (e - today).days <= max_dte]
    if in_window:
        target = (min_dte + max_dte) / 2
        return min(in_window, key=lambda e: abs((e - today).days - target))
    at_least = [e for e in future if (e - today).days >= min_dte]
    if at_least:
        return at_least[0]
    return future[-1]


def _usable(contracts: List[OptionContract]) -> List[OptionContract]:
    return [c for c in contracts if c.mid is not None and c.mid > 0]


def _nearest_strike(
    contracts: List[OptionContract], target: float
) -> Optional[OptionContract]:
    usable = _usable(contracts)
    if not usable:
        return None
    return min(usable, key=lambda c: abs(c.strike - target))


def _spot(chain: OptionChain, fallback: Optional[float]) -> Optional[float]:
    if chain.underlying_price and chain.underlying_price > 0:
        return chain.underlying_price
    return fallback if fallback and fallback > 0 else None


def build_priced_strategies(
    near_chain: Optional[OptionChain],
    leaps_chain: Optional[OptionChain],
    *,
    fallback_spot: Optional[float] = None,
    high_quality: bool = False,
) -> List[PricedStrategy]:
    strategies: List[PricedStrategy] = []
    if near_chain is not None:
        spot = _spot(near_chain, fallback_spot)
        if spot:
            strategies.extend(_near_dated_strategies(near_chain, spot, high_quality))
    if leaps_chain is not None:
        spot = _spot(leaps_chain, fallback_spot)
        if spot:
            leaps = _leaps_call(leaps_chain, spot)
            if leaps is not None:
                strategies.append(leaps)
    return strategies


def _near_dated_strategies(
    chain: OptionChain, spot: float, high_quality: bool
) -> List[PricedStrategy]:
    out: List[PricedStrategy] = []

    # 1) Cash-secured put ~7% below spot.
    short_put = _nearest_strike(chain.puts, spot * 0.93)
    if short_put is not None:
        premium = short_put.mid
        out.append(
            PricedStrategy(
                strategy="Cash-secured put",
                outlook="neutral-to-bullish",
                legs=[_leg("sell", short_put)],
                net_premium=round(premium, 2),
                max_profit=round(premium, 2),
                max_loss=round(short_put.strike - premium, 2),  # if it goes to 0
                breakeven=round(short_put.strike - premium, 2),
                notes=(
                    f"Sell the {short_put.strike:g} put "
                    f"({_dte_label(chain.expiry)}). Collect ${premium:.2f}/share; "
                    f"annualized ~{_annualized_yield(premium, short_put.strike, chain.expiry):.0f}% "
                    "if it expires worthless. Set aside "
                    f"${short_put.strike * 100:,.0f} per contract."
                ),
            )
        )

    # 2) Bull put credit spread: short ~5% below, long ~10% below.
    s_put = _nearest_strike(chain.puts, spot * 0.95)
    l_put = _nearest_strike(chain.puts, spot * 0.90)
    if s_put is not None and l_put is not None and s_put.strike > l_put.strike:
        credit = s_put.mid - l_put.mid
        width = s_put.strike - l_put.strike
        if credit > 0:
            out.append(
                PricedStrategy(
                    strategy="Bull put credit spread",
                    outlook="bullish",
                    legs=[_leg("sell", s_put), _leg("buy", l_put)],
                    net_premium=round(credit, 2),
                    max_profit=round(credit, 2),
                    max_loss=round(width - credit, 2),
                    breakeven=round(s_put.strike - credit, 2),
                    notes=(
                        f"Sell {s_put.strike:g} / buy {l_put.strike:g} put "
                        f"({_dte_label(chain.expiry)}). Net credit "
                        f"${credit:.2f}, defined max loss ${width - credit:.2f}/share."
                    ),
                )
            )

    # 3) Bull call debit spread (only surfaced for the strongest names).
    if high_quality:
        l_call = _nearest_strike(chain.calls, spot)
        s_call = _nearest_strike(chain.calls, spot * 1.10)
        if l_call is not None and s_call is not None and s_call.strike > l_call.strike:
            debit = l_call.mid - s_call.mid
            width = s_call.strike - l_call.strike
            if debit > 0:
                out.append(
                    PricedStrategy(
                        strategy="Bull call debit spread",
                        outlook="bullish",
                        legs=[_leg("buy", l_call), _leg("sell", s_call)],
                        net_premium=round(-debit, 2),
                        max_profit=round(width - debit, 2),
                        max_loss=round(debit, 2),
                        breakeven=round(l_call.strike + debit, 2),
                        notes=(
                            f"Buy {l_call.strike:g} / sell {s_call.strike:g} call "
                            f"({_dte_label(chain.expiry)}). Net debit ${debit:.2f}, "
                            f"max gain ${width - debit:.2f}/share."
                        ),
                    )
                )
    return out


def _leaps_call(chain: OptionChain, spot: float) -> Optional[PricedStrategy]:
    """Long-dated, slightly ITM call for a leveraged recovery bet."""
    call = _nearest_strike(chain.calls, spot * 0.95)
    if call is None:
        return None
    debit = call.mid
    return PricedStrategy(
        strategy="Long LEAPS call",
        outlook="bullish",
        legs=[_leg("buy", call)],
        net_premium=round(-debit, 2),
        max_profit=None,  # unlimited upside
        max_loss=round(debit, 2),
        breakeven=round(call.strike + debit, 2),
        notes=(
            f"Buy the {call.strike:g} call ({_dte_label(chain.expiry)}). "
            f"Debit ${debit:.2f}/share is the max loss; breakeven "
            f"${call.strike + debit:.2f}."
        ),
    )


def _leg(action: str, c: OptionContract) -> OptionLeg:
    return OptionLeg(
        action=action,
        option_type=c.option_type,
        strike=c.strike,
        expiry=c.expiry,
        price=round(c.mid, 4),
        implied_volatility=c.implied_volatility,
    )


def _dte_label(expiry: date) -> str:
    return f"exp {expiry.isoformat()}"


def _annualized_yield(premium: float, strike: float, expiry: date) -> float:
    """Rough annualized premium yield of a cash-secured put.

    Uses a nominal 30-day holding assumption when the expiry can't be dated
    relative to a caller-supplied 'today' (kept simple and deterministic).
    """
    if strike <= 0:
        return 0.0
    return premium / strike * (365 / 30) * 100
