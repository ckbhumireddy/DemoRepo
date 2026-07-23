from datetime import date, timedelta

from stock_screener.config import ScreenConfig
from stock_screener.analysis.earnings import detect_earnings_crash
from stock_screener.data.models import PriceBar


def _series(start, closes):
    return [
        PriceBar(day=start + timedelta(days=i), open=c, high=c, low=c, close=c)
        for i, c in enumerate(closes)
    ]


def test_detects_qualifying_crash():
    start = date(2024, 1, 1)
    # 5 flat days at 100, then earnings gap to 85, drifting to 88.
    closes = [100] * 5 + [85, 86, 87, 88, 88]
    prices = _series(start, closes)
    earnings = start + timedelta(days=5)
    crash = detect_earnings_crash("X", prices, earnings, ScreenConfig(),
                                  today=start + timedelta(days=9))
    assert crash is not None
    assert crash.pre_close == 100
    assert round(crash.max_drop_pct, 2) == -0.15
    assert crash.still_down_pct < 0


def test_small_dip_does_not_qualify():
    start = date(2024, 1, 1)
    closes = [100] * 5 + [98, 98, 99, 99, 99]  # only a 2% dip
    prices = _series(start, closes)
    earnings = start + timedelta(days=5)
    crash = detect_earnings_crash("X", prices, earnings, ScreenConfig(),
                                  today=start + timedelta(days=9))
    assert crash is None


def test_old_earnings_excluded_by_lookback():
    start = date(2024, 1, 1)
    closes = [100] * 5 + [80] * 60
    prices = _series(start, closes)
    earnings = start + timedelta(days=5)
    # today is far past the lookback window.
    crash = detect_earnings_crash("X", prices, earnings, ScreenConfig(),
                                  today=start + timedelta(days=64))
    assert crash is None


def test_recovered_name_excluded_when_required():
    start = date(2024, 1, 1)
    # Crashes to 85 then fully recovers above 100.
    closes = [100] * 5 + [85, 90, 98, 103, 105]
    prices = _series(start, closes)
    earnings = start + timedelta(days=5)
    cfg = ScreenConfig()
    assert cfg.require_still_depressed is True
    crash = detect_earnings_crash("X", prices, earnings, cfg,
                                  today=start + timedelta(days=9))
    assert crash is None
    # But allowed when we opt in to recovered names.
    cfg.require_still_depressed = False
    crash2 = detect_earnings_crash("X", prices, earnings, cfg,
                                   today=start + timedelta(days=9))
    assert crash2 is not None


def test_no_earnings_date_returns_none():
    prices = _series(date(2024, 1, 1), [100] * 10)
    assert detect_earnings_crash("X", prices, None, ScreenConfig()) is None
