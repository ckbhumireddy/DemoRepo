"""Local support and resistance from swing pivots (pure functions).

A "level" here is not a line someone drew on a chart — it is a price zone
the stock has actually reversed at, more than once, recently. The
construction is mechanical so the backtest and the live scan can never
disagree about where a level is:

1. **Pivots.** A bar is a swing high when its high tops the ``span`` bars on
   each side (a swing low mirrors it). This is the classic fractal
   definition; ``span`` controls how local "local" is.
2. **Clusters.** Reversals never land on the same tick, so pivots within
   ``tolerance`` of each other merge into one level at their mean price.
3. **Strength.** A level's touch count is how many distinct pivots formed
   it. One touch is an accident; the defaults require two before a level is
   tradeable.

Everything takes an explicit bar slice, so a caller replaying history can
hand in ``bars[:t]`` and get only what was knowable at ``t`` — the pivot
definition needs ``span`` future bars, meaning the newest ``span`` bars can
never contain a confirmed pivot, and lookahead is structurally impossible.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

from earnings_analyzer.models import PriceBar

DEFAULT_SPAN = 3            # bars each side that a pivot must dominate
DEFAULT_TOLERANCE = 0.015   # pivots within 1.5% merge into one level


@dataclass
class Pivot:
    """One confirmed swing extreme."""

    index: int
    day: dt.date
    price: float
    kind: str                # "high" | "low"


@dataclass
class Level:
    """A price zone built from clustered pivots."""

    price: float             # mean of the member pivots
    touches: int
    last_touch: dt.date
    kinds: List[str] = field(default_factory=list)

    def is_below(self, reference: float) -> bool:
        return self.price < reference


def find_pivots(bars: Sequence[PriceBar], span: int = DEFAULT_SPAN) -> List[Pivot]:
    """Confirmed swing highs and lows; the newest ``span`` bars never qualify."""
    ordered = sorted(bars, key=lambda b: b.day)
    pivots: List[Pivot] = []
    n = len(ordered)
    for i in range(span, n - span):
        window = ordered[i - span:i + span + 1]
        bar = ordered[i]
        if bar.high >= max(b.high for b in window) and sum(
            1 for b in window if b.high == bar.high
        ) == 1:
            pivots.append(Pivot(index=i, day=bar.day, price=bar.high, kind="high"))
        if bar.low <= min(b.low for b in window) and sum(
            1 for b in window if b.low == bar.low
        ) == 1:
            pivots.append(Pivot(index=i, day=bar.day, price=bar.low, kind="low"))
    return pivots


def cluster_levels(
    pivots: Sequence[Pivot], tolerance: float = DEFAULT_TOLERANCE
) -> List[Level]:
    """Merge nearby pivots into levels, cheapest first.

    Greedy by price: walk the sorted pivots and extend the current cluster
    while the next pivot sits within ``tolerance`` of the cluster mean.
    """
    if not pivots:
        return []
    ordered = sorted(pivots, key=lambda p: p.price)
    levels: List[Level] = []
    members: List[Pivot] = [ordered[0]]

    def _flush() -> None:
        mean = sum(p.price for p in members) / len(members)
        levels.append(
            Level(
                price=mean,
                touches=len(members),
                last_touch=max(p.day for p in members),
                kinds=[p.kind for p in members],
            )
        )

    for pivot in ordered[1:]:
        mean = sum(p.price for p in members) / len(members)
        if mean > 0 and abs(pivot.price - mean) / mean <= tolerance:
            members.append(pivot)
        else:
            _flush()
            members = [pivot]
    _flush()
    return levels


def find_levels(
    bars: Sequence[PriceBar],
    span: int = DEFAULT_SPAN,
    tolerance: float = DEFAULT_TOLERANCE,
) -> List[Level]:
    """Pivots -> clustered levels, sorted by price."""
    return cluster_levels(find_pivots(bars, span), tolerance)


def nearest_support(
    levels: Sequence[Level], price: float, min_touches: int = 1
) -> Optional[Level]:
    """The strongest claim to 'the floor': the highest level below price."""
    below = [l for l in levels if l.price < price and l.touches >= min_touches]
    return max(below, key=lambda l: l.price) if below else None


def nearest_resistance(
    levels: Sequence[Level], price: float, min_touches: int = 1
) -> Optional[Level]:
    """The first ceiling: the lowest level above price."""
    above = [l for l in levels if l.price > price and l.touches >= min_touches]
    return min(above, key=lambda l: l.price) if above else None


def bracket(
    levels: Sequence[Level], price: float, min_touches: int = 1
) -> Tuple[Optional[Level], Optional[Level]]:
    """(support, resistance) around ``price`` — the swing-trading corridor."""
    return (
        nearest_support(levels, price, min_touches),
        nearest_resistance(levels, price, min_touches),
    )
