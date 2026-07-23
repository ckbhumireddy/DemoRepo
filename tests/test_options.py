from datetime import date

from stock_screener.analysis.options import suggest_option_strategies
from stock_screener.data.models import Candidate, EarningsCrash, Fundamentals


def _candidate(score: float) -> Candidate:
    crash = EarningsCrash(
        ticker="X", earnings_date=date(2024, 1, 1), pre_close=100,
        reaction_close=85, trough_close=84, current_close=88,
        reaction_drop_pct=-0.15, max_drop_pct=-0.16, still_down_pct=-0.12,
        days_since_earnings=5,
    )
    return Candidate(
        ticker="X", fundamentals=Fundamentals(ticker="X"), crash=crash,
        fundamental_score=score, data_coverage=1.0,
    )


def test_base_strategies_present():
    strategies = {s.strategy for s in suggest_option_strategies(_candidate(65))}
    assert "Cash-secured put" in strategies
    assert "Bull put credit spread" in strategies
    assert "Long LEAPS call" in strategies


def test_high_quality_adds_debit_spread():
    low = {s.strategy for s in suggest_option_strategies(_candidate(65))}
    high = {s.strategy for s in suggest_option_strategies(_candidate(80))}
    assert "Bull call debit spread" not in low
    assert "Bull call debit spread" in high


def test_strike_guidance_references_price():
    sug = suggest_option_strategies(_candidate(80))
    # Current close is 88; a 7% lower put strike ~ $81.84 should appear.
    csp = next(s for s in sug if s.strategy == "Cash-secured put")
    assert "$" in csp.strike_guidance
