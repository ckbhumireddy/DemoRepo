import datetime as dt

from portfolio_insights.emails import (
    DISCLAIMER,
    render_eod_email,
    render_midday_email,
)
from portfolio_insights.portfolio import PortfolioSnapshot, Position
from portfolio_insights.quotes import Quote
from portfolio_insights.signals import (
    AlertTrigger,
    Insight,
    biggest_movers,
    build_views,
)

TODAY = dt.date(2026, 8, 17)


def _views():
    snapshot = PortfolioSnapshot(
        positions=[Position("TGT", 100, 100.0)], cash=1000.0
    )
    quotes = {
        "TGT": Quote("TGT", 156.0, 150.0, 4.0),
        "SPY": Quote("SPY", 650.0, 648.7, 0.2),
    }
    return build_views(snapshot, quotes)


def test_eod_email_contents():
    views, portfolio = _views()
    insights = [Insight("concentration", "TGT",
                        "TGT is 94.0% of your portfolio (threshold 20%).",
                        "Consider trimming or hedging.")]
    subject, text, html = render_eod_email(
        views, portfolio, insights, biggest_movers(views), TODAY
    )
    assert subject.startswith("[Portfolio] EOD — Mon Aug 17")
    assert "+600" in subject and "1 watch item(s)" in subject
    assert "Total value $16,600.00" in text
    assert "SPY +0.20%" in text
    flat = " ".join(text.split())
    assert "TGT" in flat and "+4.00%" in flat
    assert "VALUE" in text and "DAY" in text     # aligned table header
    assert "<table" in html and "Ticker" in html  # real table in HTML
    assert "Consider trimming" in text
    assert DISCLAIMER in text
    assert "Portfolio EOD insights" in html and DISCLAIMER in html


def test_eod_degraded_mode():
    subject, text, html = render_eod_email(
        [], None, [], ([], []), TODAY, fetch_error="no portfolio source: x"
    )
    assert "could not load the portfolio" in subject
    assert "no portfolio source: x" in text
    assert "no portfolio source: x" in html


def test_eod_empty_portfolio():
    subject, text, _ = render_eod_email([], None, [], ([], []), TODAY)
    assert "no positions" in subject
    assert "no equity positions" in text


def test_midday_email_contents():
    views, portfolio = _views()
    triggers = [
        AlertTrigger("position", "TGT", "TGT +6.2%"),
        AlertTrigger("portfolio", "PORTFOLIO", "portfolio +2.4%"),
    ]
    subject, text, html = render_midday_email(triggers, views, portfolio, TODAY)
    assert subject == "[Portfolio] Midday alert — TGT +6.2% · portfolio +2.4%"
    assert "TGT +6.2%" in text
    assert "% of portfolio" in text          # position context attached
    assert DISCLAIMER in text and DISCLAIMER in html
