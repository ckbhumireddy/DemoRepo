import datetime as dt
import math

from earnings_analyzer.models import PriceBar
from earnings_analyzer.volatility import compute_iv_rank, realized_vol_series

START = dt.date(2026, 1, 1)


def _bars(closes):
    return [
        PriceBar(day=START + dt.timedelta(days=i), open=c, high=c, low=c, close=c)
        for i, c in enumerate(closes)
    ]


def _wobbly_closes(n=120, base=100.0, amp=0.02):
    return [base * (1 + amp * math.sin(i * 1.7)) for i in range(n)]


def test_realized_vol_series_rolls():
    series = realized_vol_series(_wobbly_closes(), window=21)
    assert len(series) > 50
    assert all(v > 0 for v in series)


def test_realized_vol_short_series_single_estimate():
    assert len(realized_vol_series([100, 101, 99, 102], window=21)) == 1
    assert realized_vol_series([100], window=21) == []


def test_iv_rank_within_range():
    bars = _bars(_wobbly_closes())
    hv = realized_vol_series([b.close for b in bars], window=21)
    mid_iv = (min(hv) + max(hv)) / 2
    rank = compute_iv_rank("test", mid_iv, bars)
    assert 0.0 < rank.iv_rank < 1.0
    assert 0.0 < rank.iv_percentile <= 1.0
    assert rank.method.endswith("(proxy)")


def test_iv_rank_extremes_clamped():
    bars = _bars(_wobbly_closes())
    assert compute_iv_rank("t", 10.0, bars).iv_rank == 1.0   # way above range
    assert compute_iv_rank("t", 0.0001, bars).iv_rank == 0.0


def test_iv_rank_none_without_data():
    assert compute_iv_rank("t", None, _bars(_wobbly_closes())) is None
    assert compute_iv_rank("t", 0.5, _bars([100.0] * 5)) is None
