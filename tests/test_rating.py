import datetime as dt

from earnings_analyzer.models import (
    EarningsHistoryStats,
    ImpliedMove,
    IVRank,
    LiquidityReport,
    ReactionStats,
    TrendContext,
)
from earnings_analyzer.rating import compute_rating

EXPIRY = dt.date(2026, 8, 14)


def _history(beat=0.75, surprise=4.0, streak=3):
    return EarningsHistoryStats(
        quarters=12, beat_rate=beat, avg_surprise_pct=surprise, streak=streak
    )


def _reactions(up=0.75, worst=-8.0):
    return ReactionStats(
        samples=8, avg_abs_move_pct=5.0, up_rate=up, best_pct=10.0, worst_pct=worst
    )


def _implied(ratio=1.5, verdict="rich", diluted=False):
    return ImpliedMove(
        expiry=EXPIRY, straddle_debit=6.0, implied_move_pct=6.0,
        historical_avg_abs_move_pct=4.0, ratio=ratio, verdict=verdict,
        diluted=diluted,
    )


def _iv_rank(rank=0.8):
    return IVRank(
        ticker="X", current_iv=0.6, hv_low=0.2, hv_high=0.7,
        iv_rank=rank, iv_percentile=rank, window_days=21, sample_size=80,
    )


def _liquidity(tradeable=True, spread=3.0, oi=1500.0, vol=600.0):
    return LiquidityReport(
        atm_open_interest=oi, atm_volume=vol, atm_spread_pct=spread,
        tradeable=tradeable, reasons=[] if tradeable else ["spread too wide"],
    )


def _trend(price=110.0, ma20=105.0, ma50=100.0, ma200=90.0, rng=0.8):
    return TrendContext(
        price=price, ma20=ma20, ma50=ma50, ma200=ma200,
        pct_of_52wk_range=rng, bias="bullish",
    )


def _full_rating(**overrides):
    args = dict(
        history=_history(), reactions=_reactions(), implied=_implied(),
        iv_rank=_iv_rank(), liquidity=_liquidity(), trend=_trend(),
    )
    args.update(overrides)
    return compute_rating(**args)


def test_strong_setup_rates_high_with_full_coverage():
    rating = _full_rating()
    assert rating.coverage == 1.0
    assert rating.score >= 75
    assert rating.grade in {"A+", "A", "B"}
    assert set(rating.components) == {
        "earnings_quality", "vol_edge", "liquidity",
        "reaction_consistency", "trend_alignment",
    }


def test_missing_component_reweights_not_zeroes():
    partial = _full_rating(implied=None, iv_rank=None)
    assert partial.coverage == 0.75  # vol_edge's 25 points missing
    assert "vol_edge" not in partial.components
    assert partial.score > 0


def test_illiquid_caps_score():
    rating = _full_rating(liquidity=_liquidity(tradeable=False))
    assert rating.score <= 64
    assert rating.components["liquidity"] == 0.0


def test_low_coverage_caps_score():
    rating = compute_rating(
        history=_history(beat=1.0, surprise=10.0, streak=4),
        reactions=None, implied=None, iv_rank=None, liquidity=None, trend=None,
    )
    assert rating.coverage == 0.25
    assert rating.score <= 59


def test_no_data_is_f():
    rating = compute_rating(None, None, None, None, None, None)
    assert rating.score == 0.0
    assert rating.grade == "F"
    assert rating.coverage == 0.0


def test_no_vol_edge_at_ratio_one():
    fair = _full_rating(implied=_implied(ratio=1.0, verdict="fair"))
    assert fair.components["vol_edge"] < 20  # only the neutral confirmation half


def test_diluted_implied_caps_vol_edge():
    diluted = _full_rating(implied=_implied(ratio=2.0, diluted=True))
    assert diluted.components["vol_edge"] <= 50


def test_grade_bands():
    from earnings_analyzer.rating import _grade

    assert _grade(95) == "A+"
    assert _grade(80) == "A"
    assert _grade(65) == "B"
    assert _grade(50) == "C"
    assert _grade(35) == "D"
    assert _grade(20) == "F"
