import datetime as dt

from portfolio_insights.emails import (
    DISCLAIMER,
    render_eod_email,
    render_midday_email,
)
from portfolio_insights.portfolio import PortfolioSnapshot, Position
from portfolio_insights.quotes import Quote
from portfolio_insights.signals import AlertTrigger, Insight, build_views

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
    subject, text, html = render_eod_email(views, portfolio, insights, TODAY)
    assert subject.startswith("[Portfolio] EOD — Mon Aug 17")
    assert "+600" in subject and "1 watch item(s)" in subject
    assert "Total value $16,600.00" in text
    assert "SPY +0.20%" in text
    flat = " ".join(text.split())
    assert "TGT" in flat and "+4.00%" in flat
    assert "$15,600 " in flat or "$15,600" in flat   # whole dollars, no cents
    assert "$15,600.00" not in text                  # in the positions table
    assert "VALUE" in text and "DAY" in text         # aligned table header
    assert "Biggest movers by day P&L:" in text
    assert "<table" in html and "Ticker" in html     # real table in HTML
    assert "By day P&L" in html
    assert "Consider trimming" in text
    assert DISCLAIMER in text
    assert "Portfolio EOD insights" in html and DISCLAIMER in html


def test_eod_iv_context_card():
    from earnings_analyzer.models import IVRank

    views, portfolio = _views()
    ranks = [
        IVRank(ticker="MSFT", current_iv=0.22, hv_low=0.15, hv_high=0.45,
               iv_rank=0.23, iv_percentile=0.2, window_days=21, sample_size=38),
        IVRank(ticker="TGT", current_iv=0.55, hv_low=0.2, hv_high=0.6,
               iv_rank=0.88, iv_percentile=0.9, window_days=21, sample_size=38),
    ]
    _, text, html = render_eod_email(views, portfolio, [], TODAY,
                                     iv_context=ranks)
    assert "Options premium, top positions" in text
    assert "MSFT" in text and "premium cheap" in text
    assert "TGT" in text and "premium rich" in text
    assert "IV rank, proxy" in html

    _, text_none, _ = render_eod_email(views, portfolio, [], TODAY)
    assert "Options premium" not in text_none


def test_eod_degraded_mode():
    subject, text, html = render_eod_email(
        [], None, [], TODAY, fetch_error="no portfolio source: x"
    )
    assert "could not load the portfolio" in subject
    assert "no portfolio source: x" in text
    assert "no portfolio source: x" in html


def test_eod_empty_portfolio():
    subject, text, _ = render_eod_email([], None, [], TODAY)
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
    flat = " ".join(text.split())
    assert "DAY P&L VALUE WT" in flat            # column headers
    assert "TGT +4.00% +600 $15,600 100.0%" in flat
    assert "PORTFOLIO TRIGGER:" in text
    assert "<table" in html and "Weight" in html
    assert DISCLAIMER in text and DISCLAIMER in html
