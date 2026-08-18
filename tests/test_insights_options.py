import datetime as dt

from earnings_analyzer.models import OptionChain, OptionContract
from earnings_analyzer.provider import InMemoryProvider
from portfolio_insights.options import OptionsSummary, build_option_views
from portfolio_insights.portfolio import OptionPosition, parse_portfolio
from portfolio_insights.signals import (
    PortfolioView,
    apply_options,
    option_triggers,
)

TODAY = dt.date(2026, 8, 18)
EXPIRY = dt.date(2027, 9, 17)


def _chain(marks):
    """(type, strike) -> (mid, close_price)."""
    chain = OptionChain(ticker="SNDK", expiry=EXPIRY, underlying_price=250.0)
    for (option_type, strike), (mid, close) in marks.items():
        bucket = chain.calls if option_type == "call" else chain.puts
        bucket.append(OptionContract(
            contract_symbol=f"SNDK-{option_type}-{strike}",
            option_type=option_type, strike=strike, expiry=EXPIRY,
            bid=mid, ask=mid, close_price=close,
        ))
    return chain


def _positions():
    return [
        OptionPosition("SNDK", EXPIRY, "call", 1500.0, 1, 695.91),
        OptionPosition("SNDK", EXPIRY, "call", 1750.0, -1, 630.97),
    ]


def test_option_views_value_day_and_total():
    provider = InMemoryProvider()
    provider.add_option_chain(_chain({
        ("call", 1500.0): (640.70, 771.999),   # long leg fell today
        ("call", 1750.0): (575.30, 680.0),     # short leg fell too
    }))
    views = build_option_views(_positions(), provider, TODAY)
    long_leg, short_leg = views
    assert long_leg.market_value == 64070.0
    assert long_leg.day_pnl == round((640.70 - 771.999) * 100, 2)
    assert long_leg.total_pnl == round((640.70 - 695.91) * 100, 2)
    assert short_leg.market_value == -57530.0
    assert short_leg.day_pnl == round((575.30 - 680.0) * 100 * -1, 2)  # short gains

    summary = OptionsSummary(views)
    assert summary.total_value == 64070.0 - 57530.0
    assert summary.day_pnl == round(
        (640.70 - 771.999) * 100 + (575.30 - 680.0) * -100, 2
    )


def test_unquoted_and_expired_stay_listed_with_notes():
    provider = InMemoryProvider()   # no chains at all
    past = OptionPosition("OLD", TODAY - dt.timedelta(days=5), "call", 10.0, 1)
    views = build_option_views([past] + _positions(), provider, TODAY)
    assert views[0].note == "expired"
    assert all(v.market_value is None for v in views)
    summary = OptionsSummary(views)
    assert summary.total_value == 0.0 and summary.day_pnl is None
    assert len(summary.unvalued) == 3


def test_apply_options_folds_into_portfolio():
    portfolio = PortfolioView(total_value=100000.0, day_pnl=500.0,
                              day_change_pct=0.5, total_pnl=1000.0)
    provider = InMemoryProvider()
    provider.add_option_chain(_chain({
        ("call", 1500.0): (640.70, 660.0),
        ("call", 1750.0): (575.30, 580.0),
    }))
    summary = OptionsSummary(build_option_views(_positions(), provider, TODAY))
    apply_options(portfolio, summary)
    assert portfolio.options_value == 6540.0        # 64,070 - 57,530
    assert portfolio.total_value == 106540.0
    # day: long (640.7-660)*100 = -1930; short (575.3-580)*-100 = +470
    assert portfolio.options_day_pnl == -1460.0
    assert portfolio.day_pnl == 500.0 - 1460.0


def test_option_triggers_dollar_gate_only():
    provider = InMemoryProvider()
    provider.add_option_chain(_chain({
        ("call", 1500.0): (640.70, 771.999),   # day -13,130
        ("call", 1750.0): (575.30, 577.0),     # day +170 (short)
    }))
    views = build_option_views(_positions(), provider, TODAY)
    triggers = option_triggers(views, move_alert_dollars=500.0)
    assert len(triggers) == 1
    assert "SNDK $1500C Sep 27" in triggers[0].ticker
    assert "-13,130" in triggers[0].detail


def test_parse_portfolio_merges_duplicate_contracts():
    snap = parse_portfolio({"positions": [], "options": [
        {"underlying": "NFLX", "expiry": "2026-12-18", "option_type": "call",
         "strike": 110.0, "quantity": 5, "cost_basis": 5.55},
        {"underlying": "NFLX", "expiry": "2026-12-18", "option_type": "call",
         "strike": 110.0, "quantity": 5, "cost_basis": 1.13},
        {"underlying": "BAD"},
    ]})
    assert len(snap.options) == 1
    merged = snap.options[0]
    assert merged.quantity == 10
    assert merged.cost_basis == round((5.55 * 5 + 1.13 * 5) / 10, 4)
    assert merged.label == "NFLX $110C Dec 26"
