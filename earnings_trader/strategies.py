"""The trader's strategy playbook: one defined-risk strategy per setup.

Adapted from the trade-sheet builders. The rule table keys off the vol
verdict (rich/cheap) and the trend bias; "fair" setups are never traded.
All figures per share (x100 for one contract). Calendars are out of scope:
every position must have a closed-form max loss to size against.
"""

from __future__ import annotations

import datetime as dt
from typing import List, Optional

from earnings_analyzer.models import (
    OptionChain,
    OptionContract,
    OptionLeg,
    PricedStrategy,
)

WING_MULT = 1.5   # long wings sit at 1.5x the implied move


def select_expiry(
    expiries: List[dt.date], today: dt.date, *, min_dte: int, max_dte: int
) -> Optional[dt.date]:
    """Pick the expiry whose DTE best fits ``[min_dte, max_dte]``."""
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


def _nearest_strike_beyond(
    contracts: List[OptionContract], target: float, boundary: float, side: str
) -> Optional[OptionContract]:
    """Nearest strike to ``target`` strictly below/above ``boundary``."""
    usable = [
        c
        for c in _usable(contracts)
        if (c.strike < boundary if side == "below" else c.strike > boundary)
    ]
    if not usable:
        return None
    return min(usable, key=lambda c: abs(c.strike - target))


def _leg(action: str, c: OptionContract) -> OptionLeg:
    return OptionLeg(
        action=action,
        option_type=c.option_type,
        strike=c.strike,
        expiry=c.expiry,
        price=round(c.mid, 4),
        implied_volatility=c.implied_volatility,
    )


def _iron_condor(chain: OptionChain, spot: float, m: float) -> Optional[PricedStrategy]:
    sp = _nearest_strike(chain.puts, spot * (1 - m))
    sc = _nearest_strike(chain.calls, spot * (1 + m))
    if sp is None or sc is None or sp.strike >= sc.strike:
        return None
    lp = _nearest_strike_beyond(
        chain.puts, spot * (1 - WING_MULT * m), sp.strike, "below"
    )
    lc = _nearest_strike_beyond(
        chain.calls, spot * (1 + WING_MULT * m), sc.strike, "above"
    )
    if lp is None or lc is None:
        return None
    credit = sp.mid + sc.mid - lp.mid - lc.mid
    if credit <= 0:
        return None
    width = max(sp.strike - lp.strike, lc.strike - sc.strike)
    return PricedStrategy(
        strategy="Iron condor",
        outlook="neutral",
        legs=[_leg("sell", sp), _leg("buy", lp), _leg("sell", sc), _leg("buy", lc)],
        net_premium=round(credit, 2),
        max_profit=round(credit, 2),
        max_loss=round(width - credit, 2),
        breakevens=[round(sp.strike - credit, 2), round(sc.strike + credit, 2)],
    )


def _credit_spread(
    chain: OptionChain, spot: float, m: float, kind: str
) -> Optional[PricedStrategy]:
    if kind == "put":
        short = _nearest_strike(chain.puts, spot * (1 - m))
        long = (
            _nearest_strike_beyond(
                chain.puts, spot * (1 - WING_MULT * m), short.strike, "below"
            )
            if short is not None
            else None
        )
    else:
        short = _nearest_strike(chain.calls, spot * (1 + m))
        long = (
            _nearest_strike_beyond(
                chain.calls, spot * (1 + WING_MULT * m), short.strike, "above"
            )
            if short is not None
            else None
        )
    if short is None or long is None:
        return None
    credit = short.mid - long.mid
    if credit <= 0:
        return None
    width = abs(short.strike - long.strike)
    breakeven = short.strike - credit if kind == "put" else short.strike + credit
    return PricedStrategy(
        strategy="Put credit spread" if kind == "put" else "Call credit spread",
        outlook="bullish" if kind == "put" else "bearish",
        legs=[_leg("sell", short), _leg("buy", long)],
        net_premium=round(credit, 2),
        max_profit=round(credit, 2),
        max_loss=round(width - credit, 2),
        breakevens=[round(breakeven, 2)],
    )


def _debit_spread(
    chain: OptionChain, spot: float, m: float, kind: str
) -> Optional[PricedStrategy]:
    if kind == "call":
        long = _nearest_strike(chain.calls, spot)
        short = (
            _nearest_strike_beyond(chain.calls, spot * (1 + m), long.strike, "above")
            if long is not None
            else None
        )
    else:
        long = _nearest_strike(chain.puts, spot)
        short = (
            _nearest_strike_beyond(chain.puts, spot * (1 - m), long.strike, "below")
            if long is not None
            else None
        )
    if long is None or short is None:
        return None
    debit = long.mid - short.mid
    if debit <= 0:
        return None
    width = abs(short.strike - long.strike)
    breakeven = long.strike + debit if kind == "call" else long.strike - debit
    return PricedStrategy(
        strategy="Call debit spread" if kind == "call" else "Put debit spread",
        outlook="bullish" if kind == "call" else "bearish",
        legs=[_leg("buy", long), _leg("sell", short)],
        net_premium=round(-debit, 2),
        max_profit=round(width - debit, 2),
        max_loss=round(debit, 2),
        breakevens=[round(breakeven, 2)],
    )


def _long_straddle(chain: OptionChain, spot: float) -> Optional[PricedStrategy]:
    call = _nearest_strike(chain.calls, spot)
    put = _nearest_strike(chain.puts, spot)
    if call is None or put is None:
        return None
    debit = call.mid + put.mid
    if debit <= 0:
        return None
    same_strike = call.strike == put.strike
    return PricedStrategy(
        strategy="Long straddle" if same_strike else "Long strangle",
        outlook="volatile",
        legs=[_leg("buy", call), _leg("buy", put)],
        net_premium=round(-debit, 2),
        max_profit=None,  # open-ended upside; risk is still the debit
        max_loss=round(debit, 2),
        breakevens=[round(put.strike - debit, 2), round(call.strike + debit, 2)],
    )


def build_strategy(
    verdict: str, bias: str, chain: OptionChain, spot: float, move_frac: float
) -> Optional[PricedStrategy]:
    """The rule table: exactly one strategy per (verdict, bias) cell.

    rich+neutral -> iron condor; rich+bullish/bearish -> put/call credit
    spread; cheap+bullish/bearish -> call/put debit spread; cheap+neutral ->
    long straddle. Anything else (fair/unknown) -> None.
    """
    if spot <= 0 or move_frac <= 0:
        return None
    if verdict == "rich":
        if bias == "neutral":
            return _iron_condor(chain, spot, move_frac)
        if bias == "bullish":
            return _credit_spread(chain, spot, move_frac, "put")
        return _credit_spread(chain, spot, move_frac, "call")
    if verdict == "cheap":
        if bias == "neutral":
            return _long_straddle(chain, spot)
        if bias == "bullish":
            return _debit_spread(chain, spot, move_frac, "call")
        return _debit_spread(chain, spot, move_frac, "put")
    return None
