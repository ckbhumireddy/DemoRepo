import datetime as dt

import pytest

from earnings_analyzer.models import PriceBar
from swing_trader.levels import (
    bracket,
    cluster_levels,
    find_levels,
    find_pivots,
    nearest_resistance,
    nearest_support,
    Pivot,
)

BASE = dt.date(2026, 1, 5)


def _bars(rows):
    """rows: list of (open, high, low, close)."""
    return [
        PriceBar(day=BASE + dt.timedelta(days=i), open=o, high=h, low=l,
                 close=c, volume=1e6)
        for i, (o, h, l, c) in enumerate(rows)
    ]


def _flat(price, n):
    return [(price, price + 0.5, price - 0.5, price)] * n


# --------------------------------------------------------------------------- #
# Pivots
# --------------------------------------------------------------------------- #
def test_a_lone_spike_is_a_pivot_high_and_a_dip_a_pivot_low():
    rows = _flat(100, 5) + [(100, 110, 99.5, 101)] + _flat(100, 5) \
        + [(100, 100.5, 90, 99)] + _flat(100, 5)
    pivots = find_pivots(_bars(rows), span=3)
    kinds = {(p.kind, round(p.price)) for p in pivots}
    assert ("high", 110) in kinds
    assert ("low", 90) in kinds


def test_the_newest_bars_can_never_hold_a_confirmed_pivot():
    # A pivot needs `span` future bars to confirm — the structural guarantee
    # that replaying prefixes of history can't leak the future.
    rows = _flat(100, 8) + [(100, 120, 99, 101)]      # spike is the LAST bar
    pivots = find_pivots(_bars(rows), span=3)
    assert all(p.price != 120 for p in pivots)


def test_an_equal_double_top_is_not_double_counted_per_bar():
    # Two bars share the exact same high: neither dominates the window
    # strictly, so the tie is dropped rather than counted twice.
    rows = _flat(100, 4) + [(100, 110, 99, 101)] + [(100, 110, 99, 101)] \
        + _flat(100, 4)
    pivots = find_pivots(_bars(rows), span=3)
    assert not [p for p in pivots if p.kind == "high" and p.price == 110]


# --------------------------------------------------------------------------- #
# Clustering
# --------------------------------------------------------------------------- #
def _pivot(price, i=0, kind="low"):
    return Pivot(index=i, day=BASE + dt.timedelta(days=i), price=price, kind=kind)


def test_nearby_pivots_merge_and_distant_ones_do_not():
    levels = cluster_levels(
        [_pivot(100.0, 0), _pivot(101.0, 5), _pivot(120.0, 9)],
        tolerance=0.015,
    )
    prices = sorted(round(l.price, 1) for l in levels)
    assert prices == [100.5, 120.0]
    merged = next(l for l in levels if l.touches == 2)
    assert merged.last_touch == BASE + dt.timedelta(days=5)


def test_touch_count_is_the_cluster_size():
    levels = cluster_levels([_pivot(100.0, i) for i in range(4)], 0.015)
    assert len(levels) == 1 and levels[0].touches == 4
    assert cluster_levels([], 0.015) == []


# --------------------------------------------------------------------------- #
# Bracketing
# --------------------------------------------------------------------------- #
def test_bracket_finds_the_corridor_around_price():
    levels = cluster_levels(
        [_pivot(80.0), _pivot(80.5, 1), _pivot(95.0, 2), _pivot(95.3, 3),
         _pivot(60.0, 4)],
        tolerance=0.015,
    )
    support, resistance = bracket(levels, price=90.0, min_touches=2)
    assert support.price == pytest.approx(80.25)     # nearest below, not 60
    assert resistance.price == pytest.approx(95.15)


def test_min_touches_filters_untested_levels_out_of_the_corridor():
    levels = cluster_levels([_pivot(85.0), _pivot(80.0, 1), _pivot(80.4, 2)],
                            tolerance=0.015)
    # The single-touch 85 is ignored; the double-tested 80 zone is the floor.
    assert nearest_support(levels, 90.0, min_touches=2).price == pytest.approx(80.2)
    assert nearest_resistance(levels, 90.0, min_touches=2) is None


def test_find_levels_recovers_a_real_channel():
    # Oscillate between ~80 and ~95: both walls should come back as
    # multi-touch levels.
    rows = []
    for cycle in range(4):
        rows += _flat(88, 3) + [(82, 83, 79.8, 82)] + _flat(88, 3) \
            + [(93, 95.2, 92, 93)]
    levels = find_levels(_bars(rows), span=2, tolerance=0.02)
    strong = [l for l in levels if l.touches >= 3]
    assert any(abs(l.price - 80) < 2 for l in strong), "floor not found"
    assert any(abs(l.price - 95) < 2 for l in strong), "ceiling not found"
