import datetime as dt

from earnings_notifier.earnings import EarningsEvent
from earnings_notifier.formatting import render_email, render_text, render_html

TODAY = dt.date(2026, 8, 4)


def _events():
    return [
        EarningsEvent("AAPL", dt.date(2026, 8, 11), True),
        EarningsEvent("NVDA", dt.date(2026, 8, 11), False),
    ]


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
