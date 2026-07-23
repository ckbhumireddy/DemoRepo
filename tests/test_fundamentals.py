from stock_screener.config import ScreenConfig
from stock_screener.analysis.fundamentals import score_fundamentals
from stock_screener.data.models import Fundamentals


def test_strong_company_scores_high():
    f = Fundamentals(
        ticker="X", market_cap=50e9, trailing_pe=20, profit_margin=0.2,
        operating_margin=0.25, return_on_equity=0.28, revenue_growth=0.14,
        free_cash_flow=4e9, debt_to_equity=0.4, current_ratio=1.8,
    )
    score, coverage, passed, failed = score_fundamentals(f, ScreenConfig())
    assert score >= 90
    assert coverage == 1.0
    assert not failed


def test_weak_company_scores_low():
    f = Fundamentals(
        ticker="X", market_cap=3e9, trailing_pe=-5, profit_margin=-0.1,
        operating_margin=-0.05, return_on_equity=-0.2, revenue_growth=-0.08,
        free_cash_flow=-5e8, debt_to_equity=3.5, current_ratio=0.6,
    )
    score, coverage, passed, failed = score_fundamentals(f, ScreenConfig())
    assert score < 30
    assert len(passed) <= 1


def test_missing_metrics_excluded_from_denominator():
    # Only two metrics present; both pass -> score should be 100 but coverage low.
    f = Fundamentals(ticker="X", profit_margin=0.2, return_on_equity=0.28)
    score, coverage, passed, failed = score_fundamentals(f, ScreenConfig())
    assert score == 100.0
    assert coverage < 0.5
    assert len(passed) == 2


def test_negative_pe_fails():
    f = Fundamentals(ticker="X", trailing_pe=-3)
    score, coverage, passed, failed = score_fundamentals(f, ScreenConfig())
    assert any("Reasonable P/E" in x for x in failed)
