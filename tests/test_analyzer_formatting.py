import datetime as dt

import pytest

from earnings_analyzer.config import AnalyzerConfig
from earnings_analyzer.demo_data import build_demo_provider
from earnings_analyzer.formatting import render_email, render_html, render_text
from earnings_analyzer.service import run_analyzer

TODAY = dt.date(2026, 8, 9)


@pytest.fixture(scope="module")
def analyses():
    provider, entries = build_demo_provider(TODAY)
    result = run_analyzer(
        AnalyzerConfig(dry_run=True), today=TODAY, provider=provider, entries=entries
    )
    return result.analyses


def test_email_has_three_parts(analyses):
    subject, text, html = render_email(analyses, TODAY)
    assert subject and text and html
    assert "BULLCO" in text and "BULLCO" in html


def test_subject_counts_a_or_better(analyses):
    subject, _, _ = render_email(analyses, TODAY)
    a_count = sum(1 for a in analyses if a.rating.grade in {"A+", "A"})
    assert f"{a_count} rated A or better" in subject


def test_text_card_contents(analyses):
    text = render_text(analyses, TODAY)
    assert "Iron condor" in text
    assert "Put credit spread" in text
    assert "NO TRADE" in text
    assert "IV rank" in text and "(proxy)" in text
    assert "https://finance.yahoo.com/quote/RICHCO" in text
    assert "not investment advice" in text


def test_timing_shown_when_known(analyses):
    import dataclasses

    timed = [dataclasses.replace(a, timing="after-market") for a in analyses]
    text = render_text(timed, TODAY)
    html = render_html(timed, TODAY)
    assert "· after-market" in text
    assert "after-market" in html


def test_timing_hidden_when_unknown(analyses):
    text = render_text(analyses, TODAY)
    assert "pre-market" not in text and "after-market" not in text


def test_short_interest_shown_when_known(analyses):
    import dataclasses

    shorted = [
        dataclasses.replace(
            a,
            snapshot=dataclasses.replace(
                a.snapshot, short_pct_float=0.21, short_ratio=6.5
            ),
        )
        for a in analyses
        if a.snapshot is not None
    ]
    text = render_text(shorted, TODAY)
    html = render_html(shorted, TODAY)
    assert "Short 21.0% of float (6.5d to cover)" in text
    assert "Short 21.0% of float (6.5d to cover)" in html


def test_short_interest_hidden_when_unknown(analyses):
    text = render_text(analyses, TODAY)
    assert "of float" not in text


def test_html_card_contents(analyses):
    html = render_html(analyses, TODAY)
    assert "Iron condor" in html
    assert "NO TRADE" in html
    assert "#c62828" in html                      # no-trade shown in red
    assert "finance.yahoo.com/quote/BULLCO" in html
    assert "not investment advice" in html
    assert "max-width:640px" in html              # mobile-friendly container


def test_grade_chip_rendered(analyses):
    html = render_html(analyses, TODAY)
    top = analyses[0]
    assert f"{top.rating.grade} {top.rating.score:.0f}" in html


def test_empty_sheet_renders():
    subject, text, html = render_email([], TODAY)
    assert "no setups" in subject
    assert "No upcoming earnings to analyze." in text
    assert "No upcoming earnings to analyze." in html


def test_strategy_figures_present(analyses):
    text = render_text(analyses, TODAY)
    assert "credit $" in text
    assert "max loss $" in text
    assert "BE " in text
