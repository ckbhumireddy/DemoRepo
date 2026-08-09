import datetime as dt

from earnings_analyzer.models import (
    ImpliedMove,
    LiquidityReport,
    OptionChain,
    OptionContract,
    TrendContext,
)
from earnings_analyzer.strategies import select_expiry, suggest_strategies

EXPIRY = dt.date(2026, 8, 14)
BACK_EXPIRY = dt.date(2026, 9, 18)
SPOT = 100.0


def _mid(kind, strike, tv_base):
    """Synthetic but sane mid: intrinsic + time value tapering with distance."""
    intrinsic = max(0.0, SPOT - strike) if kind == "call" else max(0.0, strike - SPOT)
    time_value = max(0.10, tv_base - 0.08 * abs(strike - SPOT))
    return intrinsic + time_value


def _chain(expiry=EXPIRY, tv_base=6.0):
    calls, puts = [], []
    for k in range(70, 135, 5):
        for kind, bucket in (("call", calls), ("put", puts)):
            m = _mid(kind, float(k), tv_base)
            bucket.append(
                OptionContract(
                    contract_symbol=f"X{expiry:%y%m%d}{kind[0]}{k}",
                    option_type=kind, strike=float(k), expiry=expiry,
                    bid=round(m - 0.05, 2), ask=round(m + 0.05, 2), last=round(m, 2),
                    implied_volatility=0.55, volume=800, open_interest=2000,
                )
            )
    return OptionChain(ticker="X", expiry=expiry, underlying_price=SPOT,
                       calls=calls, puts=puts)


def _implied(verdict, ratio=1.5, move=6.0, diluted=False):
    return ImpliedMove(
        expiry=EXPIRY, straddle_debit=6.0, implied_move_pct=move,
        historical_avg_abs_move_pct=4.0, ratio=ratio, verdict=verdict,
        diluted=diluted,
    )


def _trend(bias):
    return TrendContext(price=SPOT, bias=bias)


LIQUID = LiquidityReport(atm_open_interest=2000, atm_volume=800,
                         atm_spread_pct=2.0, tradeable=True)
ILLIQUID = LiquidityReport(tradeable=False, reasons=["spread 15.0% > 8%"])


def _suggest(verdict, bias, liquidity=LIQUID, implied=None, back=None):
    return suggest_strategies(
        implied if implied is not None else _implied(verdict),
        _trend(bias), liquidity, _chain(), back, SPOT,
    )


def test_select_expiry_prefers_window_midpoint():
    today = dt.date(2026, 8, 9)
    expiries = [dt.date(2026, 8, 14), dt.date(2026, 9, 18), dt.date(2026, 12, 18)]
    assert select_expiry(expiries, today, min_dte=30, max_dte=45) == dt.date(2026, 9, 18)


def test_rich_neutral_iron_condor():
    (s,) = _suggest("rich", "neutral")
    assert s.strategy == "Iron condor"
    assert len(s.legs) == 4
    assert s.net_premium > 0
    # Short strikes at ~1x the 6% move: 95P and 105C.
    shorts = sorted(l.strike for l in s.legs if l.action == "sell")
    assert shorts == [95.0, 105.0]
    assert s.max_loss == 5.0 - s.net_premium  # widest wing minus credit
    assert s.breakevens == [95.0 - s.net_premium, 105.0 + s.net_premium]


def test_rich_bullish_put_credit_spread():
    (s,) = _suggest("rich", "bullish")
    assert s.strategy == "Put credit spread"
    assert [l.action for l in s.legs] == ["sell", "buy"]
    assert s.is_credit


def test_rich_bearish_call_credit_spread():
    (s,) = _suggest("rich", "bearish")
    assert s.strategy == "Call credit spread"
    assert s.outlook == "bearish"


def test_cheap_neutral_straddle_and_calendar():
    strategies = _suggest("cheap", "neutral", back=_chain(BACK_EXPIRY, tv_base=8.0))
    names = [s.strategy for s in strategies]
    assert names == ["Long straddle", "Call calendar"]
    straddle, calendar = strategies
    assert straddle.max_loss == round(-straddle.net_premium, 2)
    assert len(straddle.breakevens) == 2
    assert calendar.legs[0].expiry == EXPIRY and calendar.legs[1].expiry == BACK_EXPIRY
    assert calendar.max_loss > 0


def test_cheap_without_back_chain_straddle_only():
    (s,) = _suggest("cheap", "neutral")
    assert s.strategy == "Long straddle"


def test_cheap_bullish_call_debit_spread():
    (s,) = _suggest("cheap", "bullish")
    assert s.strategy == "Call debit spread"
    assert not s.is_credit
    assert s.max_profit == 5.0 - s.max_loss  # width minus debit


def test_cheap_bearish_put_debit_spread():
    (s,) = _suggest("cheap", "bearish")
    assert s.strategy == "Put debit spread"


def test_fair_directional_credit_spread():
    (s,) = _suggest("fair", "bullish", implied=_implied("fair", ratio=1.0))
    assert s.strategy == "Put credit spread"


def test_fair_neutral_suggests_nothing():
    assert _suggest("fair", "neutral", implied=_implied("fair", ratio=1.0)) == []


def test_illiquid_suggests_nothing():
    assert _suggest("rich", "neutral", liquidity=ILLIQUID) == []


def test_diluted_or_unknown_suggests_nothing():
    assert _suggest("rich", "neutral", implied=_implied("rich", diluted=True)) == []
    assert _suggest("unknown", "neutral", implied=_implied("unknown", ratio=None)) == []


def test_missing_liquidity_suggests_nothing():
    assert suggest_strategies(_implied("rich"), _trend("neutral"), None,
                              _chain(), None, SPOT) == []
