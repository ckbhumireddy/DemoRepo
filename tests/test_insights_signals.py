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
    """Two positions: TGT 100 @ cost 100, SNDK 20 @ cost 40."""
    snapshot = _snapshot(
        Position("TGT", 100, 100.0),
        Position("SNDK", 20, 40.0),
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
    assert round(tgt.weight_pct, 1) == 94.7           # 15600 / 16480
    assert portfolio.total_value == 15600.0 + 880.0 + 1000.0
    assert portfolio.day_pnl == 680.0
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
    # TGT +4% below 5% and +$600 below $5,000; SNDK +10% triggers;
    # portfolio +3.99% triggers by percent.
    kinds = {(t.kind, t.ticker) for t in triggers}
    assert ("position", "SNDK") in kinds
    assert ("portfolio", "PORTFOLIO") in kinds
    assert ("position", "TGT") not in kinds


def test_dollar_threshold_triggers_below_percent_gate():
    """A huge position: -3.2% is below the 5% gate but -$8,148 is not."""
    snapshot = _snapshot(Position("MSFT", 516.65, 387.16))
    quotes = {
        "MSFT": _quote("MSFT", 479.63, 495.40),   # -3.18% day
        "SPY": _quote("SPY", 650.0, 648.7),
    }
    views, portfolio = build_views(snapshot, quotes)
    triggers = check_alert_triggers(
        views, portfolio, move_alert_pct=5.0, portfolio_alert_pct=99.0,
        move_alert_dollars=5000.0, portfolio_alert_dollars=1e12,
    )
    position = [t for t in triggers if t.kind == "position"]
    assert len(position) == 1 and position[0].ticker == "MSFT"
    assert "%" in position[0].detail and "(-8," in position[0].detail

    # Raise the dollar gate and the same move stays silent.
    assert check_alert_triggers(
        views, portfolio, move_alert_pct=5.0, portfolio_alert_pct=99.0,
        move_alert_dollars=1e12, portfolio_alert_dollars=1e12,
    ) == []


def test_portfolio_dollar_threshold():
    views, portfolio = _views()   # day P&L +$680
    triggers = check_alert_triggers(
        views, portfolio, move_alert_pct=99.0, portfolio_alert_pct=99.0,
        move_alert_dollars=1e12, portfolio_alert_dollars=500.0,
    )
    assert [t.kind for t in triggers] == ["portfolio"]
    assert "(+680)" in triggers[0].detail


def test_noise_floor_suppresses_dust_positions():
    """A $44 lottery ticket down 95% must not flood the email or alerts."""
    snapshot = _snapshot(
        Position("TGT", 100, 100.0),
        Position("DUST", 10, 90.0),           # value 10 x 4.40 = $44
        cash=0.0,
    )
    quotes = {
        "TGT": _quote("TGT", 156.0, 150.0),
        "DUST": _quote("DUST", 4.40, 5.00),   # day -12%, total -95%
        "SPY": _quote("SPY", 650.0, 648.7),
    }
    views, portfolio = build_views(snapshot, quotes)
    insights = generate_insights(
        views, [],
        earnings_ahead(
            [EarningsEvent("DUST", TODAY + dt.timedelta(days=2))], TODAY, 7
        ),
        max_weight_pct=99.0, move_alert_pct=5.0,
    )
    assert all(i.ticker != "DUST" for i in insights)
    triggers = check_alert_triggers(views, portfolio, move_alert_pct=5.0,
                                    portfolio_alert_pct=99.0)
    assert all(t.ticker != "DUST" for t in triggers)


def test_tiny_move_is_not_a_vol_spike():
    views, _ = _views(TGT=_quote("TGT", 100.05, 100.0))   # +0.05% day
    calm = [
        PriceBar(day=TODAY - dt.timedelta(days=i), open=100, high=100,
                 low=100, close=100.0 * (1.0001 ** (i % 2)))
        for i in range(31, 0, -1)
    ]
    assert volatility_spikes(views, {"TGT": calm}, mult=2.0) == []


def test_biggest_movers_split():
    views, _ = _views(SNDK=_quote("SNDK", 36.0, 40.0))
    gainers, losers = biggest_movers(views)
    assert [v.ticker for v in gainers] == ["TGT"]
    assert [v.ticker for v in losers] == ["SNDK"]
