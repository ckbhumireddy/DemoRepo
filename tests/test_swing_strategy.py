import datetime as dt
import math

import pytest

from earnings_analyzer.models import PriceBar
from swing_trader.backtest import Trade, backtest_ticker, score, _resolve
from swing_trader.strategies import get_strategy
from swing_trader.strategies.base import Signal

BASE = dt.date(2024, 3, 1)


def _bar(day, o, h, l, c):
    return PriceBar(day=day, open=o, high=h, low=l, close=c, volume=1e6)


def channel_bars(n=400, crash=0.45, amp_frac=0.09, period=7.0, phase=0.0):
    """Run-up, crash, then an oscillating base — strategy 1's home turf."""
    bars, day, p = [], BASE, 100.0
    for _ in range(70):
        p *= 1.006
        bars.append(_bar(day, p * 0.995, p * 1.01, p * 0.99, p))
        day += dt.timedelta(days=1)
    for _ in range(55):
        p *= (1 - crash) ** (1 / 55)
        bars.append(_bar(day, p * 1.005, p * 1.015, p * 0.985, p))
        day += dt.timedelta(days=1)
    mid, amp = p * 1.05, p * amp_frac
    for i in range(n - 125):
        c = mid + amp * math.sin((i + phase) / period)
        bars.append(_bar(day, c * 0.998, c * 1.012, c * 0.988, c))
        day += dt.timedelta(days=1)
    return bars


def flat_bars(n=300, price=100.0):
    return [
        _bar(BASE + dt.timedelta(days=i), price, price + 1, price - 1, price)
        for i in range(n)
    ]


# --------------------------------------------------------------------------- #
# The distress screen
# --------------------------------------------------------------------------- #
def test_a_crashed_stock_is_distressed_and_a_flat_one_is_not():
    strategy = get_strategy("distressed-sr")
    # Probe while the crash peak is still inside the 52-week window.
    assert strategy.is_distressed(channel_bars()[:260])
    assert not strategy.is_distressed(flat_bars())


def test_distress_expires_when_the_peak_leaves_the_52_week_window():
    # A year after the crash, the drawdown is measured against the base the
    # stock actually trades in — it has healed, and the screen must let go.
    bars = channel_bars(n=400)
    strategy = get_strategy("distressed-sr")
    assert (strategy.drawdown(bars) or 1) < 0.30


def test_a_pullback_above_the_200_day_is_not_distress():
    # 35% off a brief spike but still above a rising long-term average:
    # the drawdown alone must not qualify it.
    bars = []
    day, p = BASE, 100.0
    for i in range(300):
        p *= 1.003
        bars.append(_bar(day, p, p * 1.001, p * 0.999, p))
        day += dt.timedelta(days=1)
    spike = p * 1.6
    bars.append(_bar(day, p, spike, p, p * 1.01))
    strategy = get_strategy("distressed-sr")
    assert (strategy.drawdown(bars) or 0) > 0.30
    assert not strategy.is_distressed(bars)


def test_cheap_stocks_are_screened_out():
    bars = [
        _bar(b.day, b.open / 20, b.high / 20, b.low / 20, b.close / 20)
        for b in channel_bars()
    ]
    strategy = get_strategy("distressed-sr")     # min_price=10 default
    assert not strategy.is_distressed(bars)


def test_too_little_history_never_signals():
    strategy = get_strategy("distressed-sr")
    assert strategy.evaluate("X", channel_bars()[:100]) is None


# --------------------------------------------------------------------------- #
# The signal
# --------------------------------------------------------------------------- #
def _first_signal(bars, strategy=None):
    strategy = strategy or get_strategy("distressed-sr")
    for t in range(strategy.min_history(), len(bars)):
        signal = strategy.evaluate("TEST", bars[:t + 1])
        if signal:
            return signal
    return None


def test_the_signal_is_a_complete_plan_between_the_levels():
    signal = _first_signal(channel_bars())
    assert signal is not None
    assert signal.stop < signal.support < signal.entry
    assert signal.entry < signal.resistance
    assert signal.target == pytest.approx(signal.resistance)
    assert signal.rr >= 2.0
    assert "off its 52-week high" in signal.note


def test_far_from_support_means_no_signal():
    # Mid-channel bars are distressed with valid levels but not near the
    # floor — the entry band is what makes this a swing entry, not a buy.
    strategy = get_strategy("distressed-sr")
    bars = channel_bars()
    signals = []
    for t in range(strategy.min_history(), len(bars)):
        s = strategy.evaluate("TEST", bars[:t + 1])
        if s:
            signals.append((s, bars[t]))
    assert signals, "channel should produce entries"
    for s, bar in signals:
        assert bar.close <= s.support * (1 + strategy.entry_band)


def test_a_thin_reward_is_refused_whatever_the_level():
    # Squash the channel so support and resistance sit ~3% apart: perfect
    # levels, useless trade.
    strategy = get_strategy("distressed-sr")
    assert _first_signal(channel_bars(amp_frac=0.015), strategy) is None


# --------------------------------------------------------------------------- #
# The backtest engine
# --------------------------------------------------------------------------- #
def _sig(entry=100.0, stop=95.0, target=110.0):
    return Signal(strategy="s", ticker="X", day=BASE, price=entry,
                  entry=entry, stop=stop, target=target)


def _future(rows, start=1):
    return [
        _bar(BASE + dt.timedelta(days=start + i), o, h, l, c)
        for i, (o, h, l, c) in enumerate(rows)
    ]


def test_target_and_stop_fill_at_their_price():
    win = _resolve("X", _sig(), _future([(100, 111, 99, 110)]), 20)
    assert win.outcome == "target" and win.exit == 110.0
    loss = _resolve("X", _sig(), _future([(100, 101, 94, 95)]), 20)
    assert loss.outcome == "stop" and loss.exit == 95.0


def test_a_bar_touching_both_levels_resolves_as_the_stop():
    # The ambiguous bar reads pessimistically — backtests should understate.
    both = _resolve("X", _sig(), _future([(100, 112, 94, 100)]), 20)
    assert both.outcome == "stop"


def test_gaps_fill_at_the_open_not_the_stop():
    gap = _resolve("X", _sig(), _future([(90, 92, 88, 91)]), 20)
    assert gap.outcome == "stop" and gap.exit == 90.0     # cost the gap
    gap_up = _resolve("X", _sig(), _future([(115, 116, 114, 115)]), 20)
    assert gap_up.outcome == "target" and gap_up.exit == 115.0


def test_stale_trades_are_cut_at_the_time_stop():
    quiet = [(100, 101, 99, 100)] * 30
    trade = _resolve("X", _sig(), _future(quiet), max_hold=5)
    assert trade.outcome == "time" and trade.held == 5


def test_a_trade_still_open_at_history_end_is_scored_not_hidden():
    quiet = [(100, 101, 99, 100)] * 3
    trade = _resolve("X", _sig(), _future(quiet), max_hold=20)
    assert trade.outcome == "open" and trade.exit == 100.0


def test_backtest_holds_one_position_and_blocks_reentry():
    trades = backtest_ticker(get_strategy("distressed-sr"), "T", channel_bars())
    assert trades, "the channel should produce trades"
    for earlier, later in zip(trades, trades[1:]):
        assert later.entry_day > earlier.exit_day


def test_no_lookahead_signal_on_last_bar_produces_no_trade():
    strategy = get_strategy("distressed-sr")
    bars = channel_bars()
    signal_days = set()
    for t in range(strategy.min_history(), len(bars)):
        s = strategy.evaluate("T", bars[:t + 1])
        if s:
            signal_days.add(s.day)
    cut = max(signal_days)
    upto = [b for b in bars if b.day <= cut]
    trades = backtest_ticker(strategy, "T", upto)
    assert all(t.entry_day < cut for t in trades)


# --------------------------------------------------------------------------- #
# Scoring
# --------------------------------------------------------------------------- #
def _trade(pct, day_offset=0, held=5):
    entry = 100.0
    return Trade(ticker="X", entry_day=BASE + dt.timedelta(days=day_offset),
                 entry=entry, exit_day=BASE + dt.timedelta(days=day_offset + held),
                 exit=entry * (1 + pct), stop=95.0, target=110.0,
                 outcome="target" if pct > 0 else "stop", held=held)


def test_score_computes_the_gate_metrics():
    result = score("s", [_trade(0.10, 0), _trade(0.10, 10), _trade(-0.05, 20)])
    assert result.trades == 3 and result.wins == 2
    assert result.win_rate == pytest.approx(2 / 3)
    assert result.profit_factor == pytest.approx(0.20 / 0.05)
    assert result.expectancy == pytest.approx(0.05)
    assert result.avg_win == pytest.approx(0.10)
    assert result.avg_loss == pytest.approx(-0.05)


def test_max_drawdown_reads_the_equity_curve_in_trade_order():
    # +10%, then two -10%s: peak 1.1, trough 1.1*0.81 -> 19% drawdown.
    result = score("s", [_trade(0.10, 0), _trade(-0.10, 10), _trade(-0.10, 20)])
    assert result.max_drawdown == pytest.approx(0.19, abs=0.001)


def test_an_empty_book_scores_zero_not_crash():
    result = score("s", [])
    assert result.trades == 0 and result.profit_factor == 0.0
