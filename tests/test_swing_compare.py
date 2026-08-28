import datetime as dt

import pytest

from earnings_analyzer.models import PriceBar
from swing_trader.compare import buy_and_hold, compare_strategies, format_comparison
from swing_trader.registry import Thresholds
from swing_trader.strategies import get_strategy
from swing_trader.strategies.rsi_reversion import rsi

from tests.test_swing_strategy import channel_bars

BASE = dt.date(2024, 1, 2)


def _bar(day, o, h, l, c):
    return PriceBar(day=day, open=o, high=h, low=l, close=c, volume=1e6)


def uptrend_bars(n=320, drift=0.0015, dip_at=None, dip_len=8, dip_size=0.06):
    """A clean uptrend, optionally with one pullback toward the 50-day."""
    bars, day, p = [], BASE, 100.0
    for i in range(n):
        if dip_at is not None and dip_at <= i < dip_at + dip_len:
            p *= (1 - dip_size) ** (1 / dip_len)
            bars.append(_bar(day, p * 1.002, p * 1.004, p * 0.996, p))
        else:
            p *= 1 + drift
            bars.append(_bar(day, p * 0.998, p * 1.004, p * 0.997, p))
        day += dt.timedelta(days=1)
    return bars


# --------------------------------------------------------------------------- #
# Strategy 2: uptrend pullback
# --------------------------------------------------------------------------- #
def test_pullback_signals_only_after_the_dip_reaches_the_50_day():
    strategy = get_strategy("pullback-ma")
    strategy.band = 0.02
    bars = uptrend_bars(n=320, dip_at=290, dip_len=10, dip_size=0.055)
    # Before the dip: price rides well above the 50-day — no signal.
    assert strategy.evaluate("X", bars[:289]) is None
    # Once the dip has pulled price into the band, a strong close signals.
    signal = None
    for t in range(290, len(bars)):
        signal = strategy.evaluate("X", bars[:t + 1]) or signal
    assert signal is not None
    assert signal.stop < signal.entry < signal.target
    assert signal.rr >= strategy.min_rr
    assert "pullback" in signal.note


def test_pullback_refuses_a_downtrend():
    strategy = get_strategy("pullback-ma")
    down = [
        _bar(BASE + dt.timedelta(days=i), 100 - i * 0.2, 100.5 - i * 0.2,
             99 - i * 0.2, 100 - i * 0.2)
        for i in range(300)
    ]
    assert strategy.evaluate("X", down) is None


# --------------------------------------------------------------------------- #
# Strategy 3: RSI reversion
# --------------------------------------------------------------------------- #
def test_rsi_reads_the_extremes():
    assert rsi([100, 101, 102, 103], 3) == 100.0     # straight up
    assert rsi([103, 102, 101, 100], 3) == 0.0       # straight down
    assert rsi([100, 101], 3) is None                # not enough data
    mixed = rsi([100, 102, 101, 103], 3)
    assert 0 < mixed < 100


def test_reversion_signals_on_a_selloff_above_the_200_day():
    strategy = get_strategy("rsi-reversion")
    bars = uptrend_bars(n=300)
    # Three hard down days: still far above the 200-day, RSI(3) pinned low.
    p = bars[-1].close
    day = bars[-1].day
    for _ in range(3):
        day += dt.timedelta(days=1)
        p *= 0.975
        bars.append(_bar(day, p * 1.01, p * 1.012, p * 0.995, p))
    signal = strategy.evaluate("X", bars)
    assert signal is not None
    assert signal.target <= max(b.close for b in bars[-10:]) + 1e-9
    assert signal.stop < signal.entry
    assert "RSI(3)" in signal.note


def test_reversion_refuses_below_the_200_day():
    strategy = get_strategy("rsi-reversion")
    # The same selloff shape, but in a stock that has been falling all year.
    bars = []
    day, p = BASE, 300.0
    for i in range(300):
        p *= 0.998
        bars.append(_bar(day, p * 1.002, p * 1.004, p * 0.996, p))
        day += dt.timedelta(days=1)
    for _ in range(3):
        day += dt.timedelta(days=1)
        p *= 0.97
        bars.append(_bar(day, p * 1.01, p * 1.012, p * 0.995, p))
    assert strategy.evaluate("X", bars) is None


def test_new_strategies_expose_research_grids():
    for name in ("distressed-sr", "pullback-ma", "rsi-reversion"):
        grid = get_strategy(name).research_grid()
        assert grid and all(isinstance(v, list) and v for v in grid.values())
        # Every grid key must be a real parameter on the strategy.
        strategy = get_strategy(name)
        assert all(hasattr(strategy, k) for k in grid)


# --------------------------------------------------------------------------- #
# The comparison harness
# --------------------------------------------------------------------------- #
def test_buy_and_hold_is_the_span_return():
    bars = uptrend_bars(n=50)
    expected = bars[-1].close / bars[0].close - 1.0
    assert buy_and_hold({"X": bars}) == pytest.approx(expected)
    assert buy_and_hold({}) is None


def test_compare_races_all_strategies_on_identical_history():
    history = {"AAA": channel_bars(n=380)}
    comparison = compare_strategies(history, Thresholds(min_trades=2))
    assert {r.name for r in comparison.rows} == {
        "distressed-sr", "pullback-ma", "rsi-reversion",
    }
    assert comparison.buy_hold is not None
    # Sorted best return first.
    returns = [r.compounded for r in comparison.rows]
    assert returns == sorted(returns, reverse=True)


def test_compare_report_names_the_gate_verdicts():
    history = {"AAA": channel_bars(n=380)}
    comparison = compare_strategies(
        history, Thresholds(min_trades=500)      # nothing can pass
    )
    text = format_comparison(comparison)
    assert "would FAIL" in text and "buy-and-hold" in text
    assert "500" in text                          # the reason is spelled out


def test_compare_honors_a_strategy_subset():
    history = {"AAA": channel_bars(n=380)}
    comparison = compare_strategies(
        history, Thresholds(), names=["rsi-reversion"]
    )
    assert [r.name for r in comparison.rows] == ["rsi-reversion"]
