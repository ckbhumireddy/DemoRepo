import datetime as dt

import pytest

from earnings_analyzer.models import PriceBar
from market_insights.trend import (
    DOWN,
    FLAT,
    UP,
    compute_trend_view,
    pct_change,
)

BASE = dt.date(2024, 1, 1)


def _series(closes):
    return [
        PriceBar(day=BASE + dt.timedelta(days=i), open=c, high=c, low=c,
                 close=c, volume=1_000_000)
        for i, c in enumerate(closes)
    ]


def _ramp(n, start, end):
    step = (end - start) / max(n - 1, 1)
    return [start + step * i for i in range(n)]


# --------------------------------------------------------------------------- #
# Returns
# --------------------------------------------------------------------------- #
def test_pct_change_measures_the_window_and_refuses_short_history():
    closes = [100.0, 110.0, 120.0, 130.0]
    assert pct_change(closes, 1) == pytest.approx(130 / 120 - 1)
    assert pct_change(closes, 3) == pytest.approx(0.30)
    assert pct_change(closes, 4) is None       # needs window + 1 samples
    assert pct_change([], 1) is None


# --------------------------------------------------------------------------- #
# Labels
# --------------------------------------------------------------------------- #
def test_a_steady_advance_reads_up_on_both_horizons():
    view = compute_trend_view(_series(_ramp(300, 50.0, 150.0)))
    assert view.short_term == UP and view.long_term == UP
    assert view.aligned and not view.diverging
    assert view.ret_126d > 0 and view.ma50 > view.ma200


def test_a_steady_decline_reads_down_on_both_horizons():
    view = compute_trend_view(_series(_ramp(300, 150.0, 50.0)))
    assert view.short_term == DOWN and view.long_term == DOWN
    assert view.aligned
    assert view.ma50 < view.ma200


def test_a_flat_tape_gets_no_direction():
    view = compute_trend_view(_series([100.0] * 300))
    assert view.short_term == FLAT and view.long_term == FLAT
    assert not view.aligned and not view.diverging


def test_a_sharp_pullback_inside_an_uptrend_diverges():
    # Nine months up, then a fast 12% drop: long-term structure holds while
    # the short horizon rolls over. This is the case the whole two-horizon
    # split exists to catch.
    closes = _ramp(285, 50.0, 150.0) + _ramp(15, 150.0, 132.0)
    view = compute_trend_view(_series(closes))
    assert view.short_term == DOWN
    assert view.long_term == UP
    assert view.diverging and not view.aligned


def test_a_bounce_inside_a_downtrend_diverges_the_other_way():
    closes = _ramp(285, 150.0, 60.0) + _ramp(15, 60.0, 72.0)
    view = compute_trend_view(_series(closes))
    assert view.short_term == UP
    assert view.long_term == DOWN
    assert view.diverging


def test_short_history_still_scores_the_short_horizon():
    # Four months of data: no 200-day average, no 6-month return — the long
    # score simply has fewer votes rather than blowing up.
    view = compute_trend_view(_series(_ramp(80, 50.0, 90.0)))
    assert view.short_term == UP
    assert view.ma200 is None
    assert view.ret_126d is None
    assert view.long_term in {UP, FLAT}


def test_no_usable_bars_returns_none():
    assert compute_trend_view([]) is None
    assert compute_trend_view(_series([0.0, 0.0])) is None


def test_view_reports_position_in_the_52_week_range():
    view = compute_trend_view(_series(_ramp(300, 50.0, 150.0)))
    assert view.pct_of_52wk_range == pytest.approx(1.0, abs=0.01)


def test_price_hugging_its_moving_average_casts_no_vote():
    # An exactly flat tape ties price against every average; without the
    # neutral band those ties would resolve bearish and label a dead-quiet
    # stock a downtrend.
    view = compute_trend_view(_series([100.0] * 300))
    assert view.long_score == 0 and view.short_score == 0

    # A hair above the average is still a tie, not a trend.
    closes = [100.0] * 299 + [100.2]
    assert compute_trend_view(_series(closes)).long_term == FLAT
