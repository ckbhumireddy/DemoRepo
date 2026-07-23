from datetime import date, timedelta

from stock_screener.analysis.volatility import (
    compute_iv_rank,
    realized_vol_series,
)
from stock_screener.data.models import PriceBar


def _bars(closes):
    start = date(2024, 1, 1)
    return [
        PriceBar(day=start + timedelta(days=i), open=c, high=c, low=c, close=c)
        for i, c in enumerate(closes)
    ]


def test_flat_series_has_zero_vol():
    series = realized_vol_series([100.0] * 40, window=21)
    assert all(v == 0.0 for v in series)


def test_oscillating_series_has_positive_vol():
    closes = [100 * (1.02 if i % 2 else 0.98) for i in range(40)]
    series = realized_vol_series(closes, window=21)
    assert series and all(v > 0 for v in series)


def test_iv_rank_bounds_and_monotonicity():
    # A series with a clear spread of realized-vol values.
    closes = []
    price = 100.0
    for i in range(60):
        price *= 1 + 0.02 * ((-1) ** i) * (1 + i / 60.0)
        closes.append(price)
    bars = _bars(closes)
    hv = realized_vol_series(closes, window=21)
    lo, hi = min(hv), max(hv)

    low_rank = compute_iv_rank("X", lo, bars)
    mid_rank = compute_iv_rank("X", (lo + hi) / 2, bars)
    high_rank = compute_iv_rank("X", hi, bars)

    assert low_rank is not None and high_rank is not None
    assert low_rank.iv_rank == 0.0
    assert high_rank.iv_rank == 1.0
    assert low_rank.iv_rank <= mid_rank.iv_rank <= high_rank.iv_rank
    assert 0.0 <= mid_rank.iv_percentile <= 1.0


def test_iv_rank_none_without_iv_or_data():
    bars = _bars([100 + i for i in range(30)])
    assert compute_iv_rank("X", None, bars) is None
    assert compute_iv_rank("X", 0.3, _bars([100, 101])) is None


def test_iv_above_range_is_elevated():
    closes = [100 * (1.01 if i % 2 else 0.99) for i in range(40)]
    bars = _bars(closes)
    hv = realized_vol_series(closes, window=21)
    rank = compute_iv_rank("X", max(hv) * 2, bars)
    assert rank is not None
    assert rank.iv_rank == 1.0
    assert rank.is_elevated is True
