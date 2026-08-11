import datetime as dt

from earnings_analyzer.backtest import attach_backtests, backtest_strategy, is_win
from earnings_analyzer.models import OptionLeg, PricedStrategy

EXPIRY = dt.date(2026, 8, 14)


def _strategy(name, breakevens, net_premium=1.0, strikes=()):
    return PricedStrategy(
        strategy=name,
        outlook="neutral",
        legs=[
            OptionLeg(action="", option_type="", strike=k, expiry=EXPIRY, price=0.0)
            for k in strikes
        ],
        net_premium=net_premium,
        breakevens=list(breakevens),
    )


def test_condor_wins_inside_breakevens():
    condor = _strategy("Iron condor", [93.8, 106.2])
    assert is_win(condor, 100.0, 3.0) is True
    assert is_win(condor, 100.0, -8.0) is False
    assert is_win(condor, 100.0, 8.0) is False


def test_straddle_wins_outside_breakevens():
    straddle = _strategy("Long straddle", [78.0, 82.0], net_premium=-2.0)
    assert is_win(straddle, 80.0, 5.0) is True
    assert is_win(straddle, 80.0, -4.0) is True
    assert is_win(straddle, 80.0, 1.0) is False


def test_credit_spreads_direction():
    put = _strategy("Put credit spread", [139.5])
    assert is_win(put, 150.0, -5.0) is True    # breakeven move is -7%
    assert is_win(put, 150.0, -10.0) is False
    call = _strategy("Call credit spread", [106.3])
    assert is_win(call, 100.0, 5.0) is True
    assert is_win(call, 100.0, 8.0) is False


def test_debit_spreads_direction():
    call = _strategy("Call debit spread", [102.0])
    assert is_win(call, 100.0, 3.0) is True
    assert is_win(call, 100.0, 1.0) is False
    put = _strategy("Put debit spread", [59.34])
    assert is_win(put, 60.0, -5.0) is True
    assert is_win(put, 60.0, 2.0) is False


def test_calendar_wins_near_strike():
    calendar = _strategy("Call calendar", [], net_premium=-1.61, strikes=(80.0, 80.0))
    assert is_win(calendar, 80.0, 3.0) is True
    assert is_win(calendar, 80.0, 12.0) is False


def test_unjudgeable_shapes_return_none():
    assert is_win(_strategy("Iron condor", [95.0]), 100.0, 1.0) is None
    assert is_win(_strategy("Mystery trade", [100.0]), 100.0, 1.0) is None
    assert is_win(_strategy("Iron condor", [93.8, 106.2]), 0.0, 1.0) is None


def test_backtest_counts_wins():
    condor = _strategy("Iron condor", [94.0, 106.0])
    moves = [3.0, -8.0, 5.0, -2.0, 9.0]
    assert backtest_strategy(condor, 100.0, moves) == (3, 5)
    assert backtest_strategy(_strategy("Mystery", []), 100.0, moves) is None


def test_attach_backtests_fills_fields():
    condor = _strategy("Iron condor", [94.0, 106.0])
    attach_backtests([condor], 100.0, [3.0, -8.0])
    assert condor.backtest_wins == 1
    assert condor.backtest_total == 2
    untouched = _strategy("Iron condor", [94.0, 106.0])
    attach_backtests([untouched], 100.0, [])
    assert untouched.backtest_wins is None
