"""Valuing the portfolio's option positions from live chains.

One chain fetch per distinct (underlying, expiry) pair; each position is
marked at the contract's bid/ask mid. Day P&L uses the contract's previous
close when the feed provides it. A contract the chain cannot price stays
in the list with value None and an explanatory note — never silently
dropped, never guessed.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from .portfolio import OptionPosition

logger = logging.getLogger(__name__)

MULTIPLIER = 100.0


@dataclass
class OptionView:
    position: OptionPosition
    mark: Optional[float] = None          # per share (mid)
    market_value: Optional[float] = None  # mark x 100 x qty (signed)
    day_pnl: Optional[float] = None
    total_pnl: Optional[float] = None
    note: Optional[str] = None            # why it could not be valued

    @property
    def label(self) -> str:
        return self.position.label


def _find_contract(chain, option_type: str, strike: float):
    if chain is None:
        return None
    contracts = chain.calls if option_type == "call" else chain.puts
    for c in contracts:
        if abs(c.strike - strike) < 1e-3:
            return c
    return None


def build_option_views(
    options: List[OptionPosition], provider, today: dt.date
) -> List[OptionView]:
    chains: Dict[Tuple[str, dt.date], object] = {}
    views: List[OptionView] = []
    for pos in options:
        v = OptionView(position=pos)
        views.append(v)
        if pos.expiry < today:
            v.note = "expired"
            continue
        key = (pos.underlying, pos.expiry)
        if key not in chains:
            # Prefer the exact-expiry fetch (LEAPS sit far beyond the bulk
            # chain horizon); plain option_chain otherwise.
            fetch = getattr(provider, "option_chain_at", provider.option_chain)
            try:
                chains[key] = fetch(pos.underlying, pos.expiry)
            except Exception as exc:  # noqa: BLE001
                logger.debug("chain fetch failed (%s)", type(exc).__name__)
                chains[key] = None
        contract = _find_contract(chains[key], pos.option_type, pos.strike)
        if contract is None:
            v.note = "no quote for this contract"
            continue
        mark = contract.mid
        if mark is None:
            v.note = "no usable mid"
            continue
        v.mark = round(mark, 4)
        v.market_value = round(mark * MULTIPLIER * pos.quantity, 2)
        # Day P&L: prefer the feed's own net-change figure (the same math
        # the broker shows). The mid-minus-close fallback mixes two price
        # bases and misfires on illiquid contracts with stale last trades.
        if contract.day_change is not None:
            v.day_pnl = round(
                contract.day_change * MULTIPLIER * pos.quantity, 2
            )
        elif contract.close_price is not None:
            v.day_pnl = round(
                (mark - contract.close_price) * MULTIPLIER * pos.quantity, 2
            )
        if pos.cost_basis is not None:
            v.total_pnl = round(
                (mark - pos.cost_basis) * MULTIPLIER * pos.quantity, 2
            )
    return views


@dataclass
class OptionsSummary:
    views: List[OptionView]

    @property
    def valued(self) -> List[OptionView]:
        return [v for v in self.views if v.market_value is not None]

    @property
    def total_value(self) -> float:
        return round(sum(v.market_value for v in self.valued), 2)

    @property
    def day_pnl(self) -> Optional[float]:
        """None unless EVERY valued contract has a day figure.

        A partial sum is worse than none: dropping one leg of a spread
        (e.g. the losing SNDK leg) flips the whole book's sign.
        """
        pnls = [v.day_pnl for v in self.valued]
        if not pnls or any(p is None for p in pnls):
            return None
        return round(sum(pnls), 2)

    @property
    def day_missing(self) -> int:
        return sum(1 for v in self.valued if v.day_pnl is None)

    @property
    def total_pnl(self) -> Optional[float]:
        pnls = [v.total_pnl for v in self.valued if v.total_pnl is not None]
        return round(sum(pnls), 2) if pnls else None

    @property
    def unvalued(self) -> List[OptionView]:
        return [v for v in self.views if v.market_value is None]
