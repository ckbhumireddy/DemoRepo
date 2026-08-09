"""Rules-based option strategy suggestions, priced from live chains.

The rule table keys off two inputs: how the implied move compares to the
stock's historical earnings moves (rich / fair / cheap), and the directional
bias from trend + earnings momentum. Everything is defined-risk or
defined-cost; all figures are per share (x100 for one contract).

Suggestions are educational analysis, not orders or advice.
"""

from __future__ import annotations

import datetime as dt
from typing import List, Optional

from .models import (
    ImpliedMove,
    LiquidityReport,
    OptionChain,
    OptionContract,
    OptionLeg,
    PricedStrategy,
    TrendContext,
)

WING_MULT = 1.5   # long wings sit at 1.5x the implied move


# --------------------------------------------------------------------------- #
# Chain helpers (salvaged from the earlier screener prototype)
# --------------------------------------------------------------------------- #
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
    """Nearest strike to ``target`` strictly below/above ``boundary``.

    Keeps spread legs from collapsing onto the same strike when the strike
    grid is coarser than the implied move (e.g. $5 strikes on a 3% move).
    """
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


def _exp(expiry: dt.date) -> str:
    return f"exp {expiry.isoformat()}"


# --------------------------------------------------------------------------- #
# Strategy builders
# --------------------------------------------------------------------------- #
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
        notes=(
            f"Sell {sp.strike:g}P/{sc.strike:g}C, buy {lp.strike:g}P/{lc.strike:g}C "
            f"({_exp(chain.expiry)}). Short strikes sit at the implied move; profits "
            f"if the stock stays inside them through the IV crush."
        ),
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
    name = "Put credit spread" if kind == "put" else "Call credit spread"
    outlook = "bullish" if kind == "put" else "bearish"
    return PricedStrategy(
        strategy=name,
        outlook=outlook,
        legs=[_leg("sell", short), _leg("buy", long)],
        net_premium=round(credit, 2),
        max_profit=round(credit, 2),
        max_loss=round(width - credit, 2),
        breakevens=[round(breakeven, 2)],
        notes=(
            f"Sell {short.strike:g} / buy {long.strike:g} {kind} "
            f"({_exp(chain.expiry)}). Short strike at the implied move; net credit "
            f"${credit:.2f}, defined max loss ${width - credit:.2f}/share."
        ),
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
    name = "Call debit spread" if kind == "call" else "Put debit spread"
    outlook = "bullish" if kind == "call" else "bearish"
    return PricedStrategy(
        strategy=name,
        outlook=outlook,
        legs=[_leg("buy", long), _leg("sell", short)],
        net_premium=round(-debit, 2),
        max_profit=round(width - debit, 2),
        max_loss=round(debit, 2),
        breakevens=[round(breakeven, 2)],
        notes=(
            f"Buy {long.strike:g} / sell {short.strike:g} {kind} "
            f"({_exp(chain.expiry)}). Cheap vol makes the debit attractive; max "
            f"gain ${width - debit:.2f}/share at the short strike."
        ),
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
    name = "Long straddle" if same_strike else "Long strangle"
    return PricedStrategy(
        strategy=name,
        outlook="volatile",
        legs=[_leg("buy", call), _leg("buy", put)],
        net_premium=round(-debit, 2),
        max_profit=None,  # open-ended
        max_loss=round(debit, 2),
        breakevens=[round(put.strike - debit, 2), round(call.strike + debit, 2)],
        notes=(
            f"Buy the {call.strike:g} call + {put.strike:g} put ({_exp(chain.expiry)}). "
            f"Profits if the move beats the ±{debit / spot * 100:.1f}% priced in. "
            f"Debit ${debit:.2f}/share is the max loss."
        ),
    )


def _call_calendar(
    front: OptionChain, back: OptionChain, spot: float
) -> Optional[PricedStrategy]:
    front_call = _nearest_strike(front.calls, spot)
    if front_call is None:
        return None
    back_call = _nearest_strike(back.calls, front_call.strike)
    if back_call is None or back_call.strike != front_call.strike:
        return None
    debit = back_call.mid - front_call.mid
    if debit <= 0:
        return None
    return PricedStrategy(
        strategy="Call calendar",
        outlook="neutral",
        legs=[_leg("sell", front_call), _leg("buy", back_call)],
        net_premium=round(-debit, 2),
        max_profit=None,  # not closed-form; peaks near the strike at front expiry
        max_loss=round(debit, 2),
        breakevens=[],
        notes=(
            f"Sell the {front_call.strike:g} call ({_exp(front.expiry)}), buy the "
            f"same strike ({_exp(back.expiry)}). Harvests the event IV crush; "
            f"max profit lands near {front_call.strike:g} at the front expiry. "
            f"Max loss is the ${debit:.2f} debit."
        ),
    )


# --------------------------------------------------------------------------- #
# The rule table
# --------------------------------------------------------------------------- #
def suggest_strategies(
    implied: Optional[ImpliedMove],
    trend: Optional[TrendContext],
    liquidity: Optional[LiquidityReport],
    event_chain: Optional[OptionChain],
    back_chain: Optional[OptionChain],
    spot: float,
) -> List[PricedStrategy]:
    """Suggest at most two priced strategies for one earnings setup.

    Gated hard on liquidity and an honest implied-move comparison — no
    suggestion is better than a bad one.
    """
    if liquidity is None or not liquidity.tradeable:
        return []
    if implied is None or implied.diluted or implied.verdict == "unknown":
        return []
    if event_chain is None or spot <= 0:
        return []

    m = implied.implied_move_pct / 100.0
    if m <= 0:
        return []
    bias = trend.bias if trend is not None else "neutral"
    verdict = implied.verdict

    picks: List[Optional[PricedStrategy]] = []
    if verdict == "rich":
        if bias == "neutral":
            picks.append(_iron_condor(event_chain, spot, m))
        elif bias == "bullish":
            picks.append(_credit_spread(event_chain, spot, m, "put"))
        else:
            picks.append(_credit_spread(event_chain, spot, m, "call"))
    elif verdict == "cheap":
        if bias == "neutral":
            picks.append(_long_straddle(event_chain, spot))
            if back_chain is not None:
                picks.append(_call_calendar(event_chain, back_chain, spot))
        elif bias == "bullish":
            picks.append(_debit_spread(event_chain, spot, m, "call"))
        else:
            picks.append(_debit_spread(event_chain, spot, m, "put"))
    else:  # fair
        if bias == "bullish":
            picks.append(_credit_spread(event_chain, spot, m, "put"))
        elif bias == "bearish":
            picks.append(_credit_spread(event_chain, spot, m, "call"))
        # fair + neutral: no edge — suggest nothing.

    return [p for p in picks if p is not None][:2]
