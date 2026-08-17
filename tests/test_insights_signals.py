import datetime as dt

from earnings_analyzer.models import PriceBar
from earnings_notifier.earnings import EarningsEvent
from portfolio_insights.portfolio import PortfolioSnapshot, Position
from portfolio_insights.quotes import Quote
from portfolio_insights.signals import (
    biggest_movers,
    build_views,
    check_alert_triggers,
    earnings_ahead,
    generate_insights,
    volatility_spikes,
)

TODAY = dt.date(2026, 8, 17)


def _snapshot(*positions, cash=None):
    return PortfolioSnapshot(positions=list(positions), cash=cash)


def _quote(ticker, last, prev):
    return Quote(ticker=ticker, last=last, prev_close=prev,
                 day_change_pct=(last - prev) / prev * 100.0)


def _views(**quote_overrides):
    """Two positions: TGT 100 @ cost 100, SNDK 10 @ cost 40."""
    snapshot = _snapshot(
        Position("TGT", 100, 100.0),
        Position("SNDK", 10, 40.0),
        cash=1000.0,
    )
    quotes = {
        "TGT": _quote("TGT", 156.0, 150.0),
        "SNDK": _quote("SNDK", 44.0, 40.0),
        "SPY": _quote("SPY", 650.0, 648.7),
    }
    quotes.update(quote_overrides)
    return build_views(snapshot, quotes)


def test_build_views_math():
    views, portfolio = _views()
    tgt = next(v for v in views if v.ticker == "TGT")
    assert tgt.market_value == 15600.0
    assert tgt.day_pnl == 600.0
    assert tgt.day_change_pct == 4.0
    assert tgt.total_pnl == 5600.0
    assert tgt.total_pnl_pct == 56.0
    assert round(tgt.vs_spy_pct, 1) == 3.8            # 4.0 - 0.2
    assert round(tgt.weight_pct, 1) == 97.3           # 15600 / 16040
    assert portfolio.total_value == 15600.0 + 440.0 + 1000.0
    assert portfolio.day_pnl == 640.0
    assert portfolio.unquoted == 0


def test_missing_quote_excluded_with_count():
    snapshot = _snapshot(Position("TGT", 100, 100.0), Position("GHOST", 5, 10.0))
    views, portfolio = build_views(snapshot, {"TGT": _quote("TGT", 156.0, 150.0)})
    ghost = next(v for v in views if v.ticker == "GHOST")
    assert ghost.market_value is None and ghost.weight_pct is None
    assert portfolio.unquoted == 1
    assert portfolio.total_value == 15600.0


def test_concentration_rule_and_winner_suppression():
    views, _ = _views()
    insights = generate_insights(views, [], [], max_weight_pct=20.0,
                                 move_alert_pct=5.0)
    cats = [(i.category, i.ticker) for i in insights]
    assert ("concentration", "TGT") in cats
    # TGT is also up 56% at high weight, but rule 1 suppresses rule 6.
    assert ("winner-creep", "TGT") not in cats


def test_winner_creep_fires_below_concentration_threshold():
    views, _ = _views()
    insights = generate_insights(views, [], [], max_weight_pct=99.0,
                                 move_alert_pct=5.0)
    assert [(i.category, i.ticker) for i in insights] == [("winner-creep", "TGT")]


def test_big_loss_and_underwater_rules():
    views, _ = _views(
        TGT=_quote("TGT", 70.0, 76.0),   # day -7.9%, total -30% vs cost 100
    )
    insights = generate_insights(views, [], [], max_weight_pct=99.0,
                                 move_alert_pct=5.0)
    cats = {i.category for i in insights if i.ticker == "TGT"}
    assert "big-loss" in cats and "underwater" in cats


def test_volatility_spike_vs_flat_history():
    views, _ = _views()
    quiet = [
        PriceBar(day=TODAY - dt.timedelta(days=i), open=100, high=100,
                 low=100, close=100.0 * (1.005 ** (i % 2)))
        for i in range(31, 0, -1)
    ]
    spikes = volatility_spikes(views, {"TGT": quiet}, mult=2.0)
    assert [s.ticker for s in spikes] == ["TGT"]      # 4% day vs ~0.5% typical
    flat = [
        PriceBar(day=TODAY - dt.timedelta(days=i), open=100, high=100,
                 low=100, close=100.0)
        for i in range(31, 0, -1)
    ]
    assert volatility_spikes(views, {"TGT": flat}, mult=2.0) == []  # avg 0
    assert volatility_spikes(views, {}, mult=2.0) == []             # no history


def test_earnings_ahead_window_and_timing():
    events = [
        EarningsEvent("TGT", TODAY + dt.timedelta(days=2), timing="pre-market"),
        EarningsEvent("FAR", TODAY + dt.timedelta(days=30)),
        EarningsEvent("PAST", TODAY - dt.timedelta(days=1)),
    ]
    out = earnings_ahead(events, TODAY, 7)
    assert len(out) == 1
    assert "pre-market" in out[0].fact and out[0].ticker == "TGT"


def test_alert_triggers():
    views, portfolio = _views()
    triggers = check_alert_triggers(views, portfolio, move_alert_pct=5.0,
                                    portfolio_alert_pct=2.0)
    # TGT +4% below 5%; SNDK +10% triggers; portfolio +3.99% triggers.
    kinds = {(t.kind, t.ticker) for t in triggers}
    assert ("position", "SNDK") in kinds
    assert ("portfolio", "PORTFOLIO") in kinds
    assert ("position", "TGT") not in kinds


def test_biggest_movers_split():
    views, _ = _views(SNDK=_quote("SNDK", 36.0, 40.0))
    gainers, losers = biggest_movers(views)
    assert [v.ticker for v in gainers] == ["TGT"]
    assert [v.ticker for v in losers] == ["SNDK"]
