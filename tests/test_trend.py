import datetime as dt

from earnings_analyzer.models import PriceBar
from earnings_analyzer.trend import compute_trend

START = dt.date(2025, 8, 1)


def _bars(closes):
    return [
        PriceBar(day=START + dt.timedelta(days=i), open=c, high=c, low=c, close=c)
        for i, c in enumerate(closes)
    ]


def test_uptrend_is_bullish():
    closes = [100 + 0.2 * i for i in range(260)]  # steady climb
    trend = compute_trend(_bars(closes))
    assert trend.bias == "bullish"
    assert trend.price > trend.ma50 > trend.ma200
    assert trend.pct_of_52wk_range == 1.0


def test_downtrend_is_bearish():
    closes = [150 - 0.2 * i for i in range(260)]
    trend = compute_trend(_bars(closes))
    assert trend.bias == "bearish"
    assert trend.pct_of_52wk_range == 0.0


def test_flat_is_neutral():
    trend = compute_trend(_bars([100.0] * 260))
    assert trend.bias == "neutral"
    assert trend.pct_of_52wk_range is None  # no range on a flat line


def test_streak_breaks_neutral_tie():
    flat = _bars([100.0] * 260)
    assert compute_trend(flat, streak=3).bias == "bullish"
    assert compute_trend(flat, streak=-4).bias == "bearish"
    assert compute_trend(flat, streak=2).bias == "neutral"


def test_short_history_has_no_long_mas():
    trend = compute_trend(_bars([100.0 + i for i in range(30)]))
    assert trend.ma20 is not None
    assert trend.ma50 is None
    assert trend.ma200 is None
    assert trend.bias == "neutral"


def test_empty_bars_returns_none():
    assert compute_trend([]) is None
