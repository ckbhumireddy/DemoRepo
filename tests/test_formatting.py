import datetime as dt

from earnings_notifier.earnings import EarningsEvent
from earnings_notifier.formatting import render_email, render_text, render_html

TODAY = dt.date(2026, 8, 4)


def _events():
    return [
        EarningsEvent("AAPL", dt.date(2026, 8, 11), True),
        EarningsEvent("NVDA", dt.date(2026, 8, 11), False),
    ]


def _rich_event():
    return EarningsEvent(
        "AAPL",
        dt.date(2026, 8, 11),
        True,
        company="Apple Inc.",
        price=229.35,
        fifty_two_week_low=169.21,
        fifty_two_week_high=260.10,
        market_cap=3.41e12,
        last_earnings_date=dt.date(2026, 7, 31),
        last_eps_estimate=1.43,
        last_eps_actual=1.57,
        last_surprise_pct=9.8,
        last_reaction_pct=6.2,
    )


def test_render_email_returns_three_parts():
    subject, text, html = render_email(_events(), TODAY, 7)
    assert subject and text and html
    assert "AAPL" in text and "AAPL" in html


def test_subject_reports_count():
    subject, _, _ = render_email(_events(), TODAY, 7)
    assert "2 companies" in subject


def test_subject_singular_grammar():
    one = [EarningsEvent("AAPL", dt.date(2026, 8, 11), True)]
    subject, _, _ = render_email(one, TODAY, 7)
    assert "1 company" in subject and "companies" not in subject


def test_empty_digest_is_clear():
    subject, text, html = render_email([], TODAY, 7)
    assert "nothing new" in subject.lower()
    assert "no new s&p 500 earnings" in text.lower()
    assert "No new S&amp;P 500 earnings" in html


def test_text_lists_days_until():
    text = render_text(_events(), TODAY, 7)
    assert "in 7 day(s)" in text


def test_html_marks_estimated_and_confirmed():
    html = render_html(_events(), TODAY, 7)
    assert "estimated" in html
    assert "confirmed" in html


def test_html_escapes_ampersand_in_header():
    html = render_html([], TODAY, 7)
    assert "S&amp;P" in html


def test_text_includes_company_details():
    text = render_text([_rich_event()], TODAY, 7)
    assert "Apple Inc." in text
    assert "$229.35" in text
    assert "$169.21 – $260.10" in text
    assert "$3.41T" in text
    assert (
        "Last earnings (Jul 31): EPS $1.57 vs $1.43 est (beat by 9.8%); "
        "stock jumped 6.2%" in text
    )


def test_html_includes_company_details():
    html = render_html([_rich_event()], TODAY, 7)
    assert "Apple Inc." in html
    assert "$229.35" in html
    assert "$169.21 – $260.10" in html
    assert "$3.41T" in html
    assert "EPS $1.57 vs $1.43 est (beat by 9.8%)" in html
    assert "stock jumped 6.2%" in html


def test_missing_details_render_placeholders():
    text = render_text(_events(), TODAY, 7)
    html = render_html(_events(), TODAY, 7)
    assert "Price —" in text
    assert "Last earnings" not in text  # no data -> line omitted entirely
    assert "—" in html


def test_missed_estimate_wording():
    miss = EarningsEvent(
        "AAPL", dt.date(2026, 8, 11), True,
        last_eps_estimate=2.00, last_eps_actual=1.80,
    )
    text = render_text([miss], TODAY, 7)
    assert "missed by 10.0%" in text  # surprise computed from EPS when absent


def test_reaction_wording_by_magnitude():
    from earnings_notifier.formatting import _reaction_text

    assert _reaction_text(None) is None
    assert _reaction_text(12.0) == "stock soared 12.0%"
    assert _reaction_text(6.2) == "stock jumped 6.2%"
    assert _reaction_text(1.1) == "stock rose 1.1%"
    assert _reaction_text(-1.4) == "stock dipped 1.4%"
    assert _reaction_text(-6.0) == "stock dropped 6.0%"
    assert _reaction_text(-15.3) == "stock crashed 15.3%"


def test_negative_reaction_colored_red_in_html():
    crash = EarningsEvent(
        "AAPL", dt.date(2026, 8, 11), True,
        last_earnings_date=dt.date(2026, 7, 31),
        last_eps_estimate=2.00, last_eps_actual=1.80,
        last_reaction_pct=-12.5,
    )
    html = render_html([crash], TODAY, 7)
    assert "stock crashed 12.5%" in html
    assert "#c62828" in html


def test_ticker_links_to_yahoo_quote_page():
    text = render_text(_events(), TODAY, 7)
    html = render_html(_events(), TODAY, 7)
    assert "https://finance.yahoo.com/quote/AAPL" in text
    assert "<a href='https://finance.yahoo.com/quote/AAPL'" in html


def test_market_cap_formatting():
    from earnings_notifier.formatting import _fmt_market_cap

    assert _fmt_market_cap(None) == "—"
    assert _fmt_market_cap(3.41e12) == "$3.41T"
    assert _fmt_market_cap(850e9) == "$850.00B"
    assert _fmt_market_cap(12.3e6) == "$12.30M"
    assert _fmt_market_cap(999_999) == "$999,999"
