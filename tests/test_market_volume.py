import datetime as dt

import pytest

from earnings_analyzer.models import PriceBar
from market_insights.tradestation import EASTERN
from market_insights.volume import (
    baseline_volume,
    compute_volume_stats,
    project_volume,
    session_fraction,
    split_current_bar,
    volume_zscore,
)

BASE = dt.date(2026, 6, 1)


def _bars(volumes, close=100.0):
    return [
        PriceBar(day=BASE + dt.timedelta(days=i), open=close, high=close,
                 low=close, close=close, volume=v)
        for i, v in enumerate(volumes)
    ]


def _at(hour, minute=0):
    return dt.datetime(2026, 8, 21, hour, minute, tzinfo=EASTERN)


# --------------------------------------------------------------------------- #
# Session clock
# --------------------------------------------------------------------------- #
def test_session_fraction_is_zero_before_the_open_and_one_after_the_close():
    assert session_fraction(_at(8, 0)) == 0.0
    assert session_fraction(_at(9, 30)) == 0.0
    assert session_fraction(_at(16, 0)) == 1.0
    assert session_fraction(_at(19, 0)) == 1.0


def test_session_fraction_rises_monotonically_through_the_day():
    marks = [session_fraction(_at(h, m)) for h, m in
             [(10, 0), (11, 0), (12, 0), (13, 0), (14, 0), (15, 0), (15, 45)]]
    assert marks == sorted(marks)
    assert all(0.0 < m < 1.0 for m in marks)


def test_session_fraction_curve_is_u_shaped_not_linear():
    # The first 30 minutes carry far more than 30/390 of the day's volume,
    # and midday carries less than a linear clock would suggest.
    assert session_fraction(_at(10, 0)) > 30 / 390
    assert session_fraction(_at(12, 30)) < 0.5


def test_session_fraction_converts_naive_and_foreign_timestamps():
    naive = dt.datetime(2026, 8, 21, 12, 0)
    aware_utc = dt.datetime(2026, 8, 21, 16, 0, tzinfo=dt.timezone.utc)  # 12:00 ET
    assert session_fraction(naive) == pytest.approx(session_fraction(aware_utc))


# --------------------------------------------------------------------------- #
# Baseline and z-score
# --------------------------------------------------------------------------- #
def test_baseline_uses_the_median_so_one_spike_cannot_hide_the_next():
    calm = [1_000_000] * 19 + [40_000_000]     # one earnings-day blowout
    assert baseline_volume(_bars(calm)) == 1_000_000
    # A mean would have been dragged to ~3.0M, halving every later RVOL.
    assert sum(calm) / len(calm) > 2_500_000


def test_baseline_ignores_zero_volume_sessions_and_empty_history():
    assert baseline_volume(_bars([0, 0, 1_000_000, 2_000_000])) == 1_500_000
    assert baseline_volume([]) is None
    assert baseline_volume(_bars([0, 0])) is None


def test_baseline_honours_the_lookback_window():
    bars = _bars([5_000_000] * 20 + [1_000_000] * 20)
    assert baseline_volume(bars, lookback=20) == 1_000_000   # recent calm only
    assert baseline_volume(bars, lookback=40) == 3_000_000   # both regimes


def test_zscore_needs_enough_samples_and_real_variance():
    assert volume_zscore(_bars([1e6] * 4), 5e6) is None       # too few
    assert volume_zscore(_bars([1e6] * 20), 5e6) is None      # no variance
    varied = _bars([1e6, 1.1e6, 0.9e6, 1.2e6, 0.8e6, 1.05e6, 0.95e6, 1.15e6])
    assert volume_zscore(varied, 5e6) > 3.0


def test_zscore_is_scale_free_across_a_mega_cap_and_a_small_cap():
    # Same shape of distribution, 100x apart in size, same 3x day: the
    # z-score should agree even though raw share counts do not.
    shape = [1.0, 1.1, 0.9, 1.2, 0.8, 1.05, 0.95, 1.15, 1.0, 1.1]
    small = volume_zscore(_bars([v * 1e5 for v in shape]), 3e5)
    mega = volume_zscore(_bars([v * 1e7 for v in shape]), 3e7)
    assert small == pytest.approx(mega)


# --------------------------------------------------------------------------- #
# Projection
# --------------------------------------------------------------------------- #
def test_projection_scales_a_partial_session_up():
    assert project_volume(500_000, 0.5) == 1_000_000
    assert project_volume(500_000, 1.0) == 500_000


def test_projection_clamps_a_near_zero_fraction():
    # Seconds after the open, dividing by the true fraction would report a
    # single block trade as a hundred-x day.
    assert project_volume(100_000, 0.0) == project_volume(100_000, 0.05)


# --------------------------------------------------------------------------- #
# Assembly
# --------------------------------------------------------------------------- #
def test_compute_volume_stats_projects_only_when_the_session_is_live():
    history = _bars([1_000_000] * 20)
    live = compute_volume_stats("X", history, 1_000_000, 50.0,
                                fraction=0.5, partial=True)
    assert live.rvol == pytest.approx(2.0)
    assert live.projected == 2_000_000
    assert live.dollar_volume == pytest.approx(100_000_000)

    done = compute_volume_stats("X", history, 1_000_000, 50.0,
                                fraction=0.5, partial=False)
    assert done.rvol == pytest.approx(1.0)     # fraction ignored when closed
    assert done.fraction == 1.0


def test_compute_volume_stats_returns_none_without_usable_inputs():
    history = _bars([1_000_000] * 20)
    assert compute_volume_stats("X", history, 0, 50.0) is None
    assert compute_volume_stats("X", history, None, 50.0) is None
    assert compute_volume_stats("X", [], 1_000_000, 50.0) is None
    assert compute_volume_stats("X", _bars([0] * 20), 1_000_000, 50.0) is None


def test_compute_volume_stats_survives_a_missing_price():
    stats = compute_volume_stats("X", _bars([1e6] * 20), 2e6, None)
    assert stats.rvol == pytest.approx(2.0)
    assert stats.dollar_volume == 0.0


def test_split_current_bar_separates_the_live_bar_from_history():
    bars = _bars([1e6, 2e6, 3e6])
    history, current = split_current_bar(bars, partial=True)
    assert len(history) == 2 and current.volume == 3e6
    assert split_current_bar([], partial=False) == ([], None)


def test_split_current_bar_orders_by_day_first():
    bars = _bars([1e6, 2e6, 3e6])
    history, current = split_current_bar(list(reversed(bars)), partial=False)
    assert current.day == BASE + dt.timedelta(days=2)
    assert [b.day for b in history] == [BASE, BASE + dt.timedelta(days=1)]
