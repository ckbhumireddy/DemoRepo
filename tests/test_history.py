import datetime as dt

from earnings_analyzer.history import (
    attach_reactions,
    compute_history_stats,
    compute_reaction_stats,
    reactions_from_bars,
)
from earnings_analyzer.models import PriceBar, QuarterResult


def _bar(day, close):
    return PriceBar(day=day, open=close, high=close, low=close, close=close)


def _q(days_ago, est=None, act=None, surprise=None, reaction=None):
    return QuarterResult(
        date=dt.date(2026, 8, 9) - dt.timedelta(days=days_ago),
        eps_estimate=est, eps_actual=act,
        surprise_pct=surprise, reaction_pct=reaction,
    )


def test_reactions_from_bars_brackets_weekend():
    # Earnings Fri Jul 31; bars skip the weekend.
    bars = [
        _bar(dt.date(2026, 7, 29), 100.0),
        _bar(dt.date(2026, 7, 30), 102.0),
        _bar(dt.date(2026, 8, 3), 110.5),
        _bar(dt.date(2026, 8, 4), 111.0),
    ]
    moves = reactions_from_bars(bars, [dt.date(2026, 7, 31)])
    assert round(moves[dt.date(2026, 7, 31)], 2) == 8.33


def test_reactions_from_bars_skips_unbracketed_dates():
    bars = [_bar(dt.date(2026, 7, 30), 100.0)]
    assert reactions_from_bars(bars, [dt.date(2026, 7, 31)]) == {}


def test_attach_reactions_fills_matching_dates():
    q = _q(9)  # 2026-07-31
    out = attach_reactions([q], {q.date: 5.5})
    assert out[0].reaction_pct == 5.5


def test_history_stats_beat_rate_and_streak():
    quarters = [
        _q(10, surprise=4.0),    # newest: beat
        _q(100, surprise=2.0),   # beat
        _q(190, surprise=-1.0),  # miss ends the streak
        _q(280, surprise=3.0),
    ]
    stats = compute_history_stats(quarters)
    assert stats.quarters == 4
    assert stats.beat_rate == 0.75
    assert stats.streak == 2
    assert round(stats.avg_surprise_pct, 2) == 2.0


def test_history_stats_miss_streak_negative():
    stats = compute_history_stats([_q(10, surprise=-2.0), _q(100, surprise=-1.0)])
    assert stats.streak == -2


def test_history_stats_surprise_computed_from_eps():
    stats = compute_history_stats([_q(10, est=2.0, act=1.8)])
    assert round(stats.avg_surprise_pct, 1) == -10.0
    assert stats.beat_rate == 0.0


def test_history_stats_none_without_data():
    assert compute_history_stats([_q(10)]) is None
    assert compute_history_stats([]) is None


def test_history_stats_caps_quarters():
    quarters = [_q(30 * i + 10, surprise=1.0) for i in range(20)]
    assert compute_history_stats(quarters, max_quarters=12).quarters == 12


def test_reaction_stats_distribution():
    quarters = [
        _q(10, reaction=6.0), _q(100, reaction=-3.0),
        _q(190, reaction=4.0), _q(280, reaction=-9.0),
    ]
    stats = compute_reaction_stats(quarters)
    assert stats.samples == 4
    assert stats.avg_abs_move_pct == 5.5
    assert stats.up_rate == 0.5
    assert stats.best_pct == 6.0
    assert stats.worst_pct == -9.0


def test_reaction_stats_needs_min_samples():
    assert compute_reaction_stats([_q(10, reaction=5.0)]) is None
