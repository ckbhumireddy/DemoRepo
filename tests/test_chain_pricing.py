from datetime import date, timedelta

from stock_screener.analysis.chain_pricing import (
    build_priced_strategies,
    select_expiry,
)
from stock_screener.data.models import OptionChain, OptionContract


def _c(kind, strike, bid, ask, expiry, iv=0.4):
    return OptionContract(
        contract_symbol=f"{kind}{strike}", option_type=kind, strike=strike,
        expiry=expiry, bid=bid, ask=ask, last=(bid + ask) / 2,
        implied_volatility=iv,
    )


def _near_chain(expiry):
    return OptionChain(
        ticker="X", expiry=expiry, underlying_price=100.0,
        puts=[
            _c("put", 90, 0.9, 1.1, expiry),   # mid 1.0
            _c("put", 93, 1.4, 1.6, expiry),   # mid 1.5
            _c("put", 95, 1.9, 2.1, expiry),   # mid 2.0
        ],
        calls=[
            _c("call", 100, 4.9, 5.1, expiry),  # mid 5.0
            _c("call", 110, 1.4, 1.6, expiry),  # mid 1.5
        ],
    )


def test_select_expiry_prefers_window():
    today = date(2024, 1, 1)
    expiries = [today + timedelta(days=d) for d in (5, 35, 200)]
    chosen = select_expiry(expiries, today, min_dte=25, max_dte=50)
    assert (chosen - today).days == 35


def test_select_expiry_falls_back_to_nearest_beyond_min():
    today = date(2024, 1, 1)
    expiries = [today + timedelta(days=d) for d in (5, 70, 200)]
    chosen = select_expiry(expiries, today, min_dte=25, max_dte=50)
    assert (chosen - today).days == 70


def test_cash_secured_put_pricing():
    exp = date(2024, 2, 15)
    strategies = build_priced_strategies(_near_chain(exp), None)
    csp = next(s for s in strategies if s.strategy == "Cash-secured put")
    # target ~93 strike, mid 1.5
    leg = csp.legs[0]
    assert leg.action == "sell" and leg.strike == 93
    assert csp.net_premium == 1.5
    assert csp.is_credit is True
    assert csp.max_loss == 93 - 1.5
    assert csp.breakeven == 93 - 1.5


def test_bull_put_spread_pricing():
    exp = date(2024, 2, 15)
    strategies = build_priced_strategies(_near_chain(exp), None)
    sp = next(s for s in strategies if s.strategy == "Bull put credit spread")
    # short 95 (2.0) / long 90 (1.0) -> credit 1.0, width 5
    assert round(sp.net_premium, 2) == 1.0
    assert round(sp.max_loss, 2) == 4.0
    assert round(sp.breakeven, 2) == 94.0


def test_debit_spread_only_for_high_quality():
    exp = date(2024, 2, 15)
    low = build_priced_strategies(_near_chain(exp), None, high_quality=False)
    high = build_priced_strategies(_near_chain(exp), None, high_quality=True)
    assert not any(s.strategy == "Bull call debit spread" for s in low)
    ds = next(s for s in high if s.strategy == "Bull call debit spread")
    # long 100 (5.0) / short 110 (1.5) -> debit 3.5, width 10
    assert ds.is_credit is False
    assert round(ds.max_loss, 2) == 3.5
    assert round(ds.max_profit, 2) == 6.5
    assert round(ds.breakeven, 2) == 103.5


def test_leaps_call_from_long_chain():
    near = _near_chain(date(2024, 2, 15))
    leaps_exp = date(2024, 12, 20)
    leaps = OptionChain(
        ticker="X", expiry=leaps_exp, underlying_price=100.0,
        calls=[_c("call", 95, 11.9, 12.1, leaps_exp)],  # mid 12.0
    )
    strategies = build_priced_strategies(near, leaps)
    lc = next(s for s in strategies if s.strategy == "Long LEAPS call")
    assert lc.is_credit is False
    assert lc.max_loss == 12.0
    assert lc.max_profit is None
    assert lc.breakeven == 95 + 12.0


def test_empty_chain_yields_no_strategies():
    assert build_priced_strategies(None, None) == []
