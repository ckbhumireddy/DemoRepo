import datetime as dt

import pytest

from earnings_analyzer.models import OptionChain
from earnings_analyzer.provider import (
    InMemoryProvider,
    bars_from_frame,
    contracts_from_frame,
    quarters_from_frame,
    snapshot_from_info,
)

TODAY = dt.date(2026, 8, 9)


def test_quarters_from_frame_past_rows_only():
    pd = pytest.importorskip("pandas")
    frame = pd.DataFrame(
        {
            "EPS Estimate": [1.50, 1.43, 1.30],
            "Reported EPS": [float("nan"), 1.57, 1.35],
            "Surprise(%)": [float("nan"), 9.8, 3.8],
        },
        index=pd.to_datetime(["2026-08-13", "2026-07-31", "2026-04-30"]),
    )
    quarters = quarters_from_frame(frame, TODAY)
    assert [q.date for q in quarters] == [dt.date(2026, 7, 31), dt.date(2026, 4, 30)]
    assert quarters[0].eps_actual == 1.57
    assert quarters[0].surprise_pct == 9.8
    assert quarters_from_frame(None, TODAY) == []


def test_bars_from_frame():
    pd = pytest.importorskip("pandas")
    frame = pd.DataFrame(
        {"Open": [99.0, 101.0], "High": [102.0, 103.0], "Low": [98.0, 100.0],
         "Close": [100.0, 102.0], "Volume": [1e6, 2e6]},
        index=pd.to_datetime(["2026-08-05", "2026-08-06"]),
    )
    bars = bars_from_frame(frame)
    assert len(bars) == 2
    assert bars[0].day == dt.date(2026, 8, 5)
    assert bars[1].close == 102.0


def test_contracts_from_frame():
    pd = pytest.importorskip("pandas")
    frame = pd.DataFrame(
        {
            "contractSymbol": ["X1", "X2"],
            "strike": [100.0, float("nan")],
            "bid": [2.9, 1.0],
            "ask": [3.1, 1.2],
            "lastPrice": [3.0, 1.1],
            "impliedVolatility": [0.55, 0.6],
            "volume": [800, 10],
            "openInterest": [2000, 50],
            "inTheMoney": [False, True],
        }
    )
    expiry = dt.date(2026, 8, 14)
    contracts = contracts_from_frame(frame, "call", expiry)
    assert len(contracts) == 1  # NaN strike dropped
    c = contracts[0]
    assert c.strike == 100.0 and c.expiry == expiry and c.mid == 3.0


def test_snapshot_from_info():
    snap = snapshot_from_info(
        {"longName": "Apple Inc.", "sector": "Technology", "marketCap": 3.41e12,
         "forwardPE": 27.5, "revenueGrowth": 0.08, "profitMargins": 0.25,
         "fiftyTwoWeekLow": 169.21, "fiftyTwoWeekHigh": 260.10}
    )
    assert snap.company == "Apple Inc."
    assert snap.market_cap == 3.41e12
    assert snap.short_pct_float is None
    assert snapshot_from_info({}) is None


def test_snapshot_from_info_short_interest():
    snap = snapshot_from_info(
        {"longName": "Target", "shortPercentOfFloat": 0.0369, "shortRatio": 4.3}
    )
    assert snap.short_pct_float == 0.0369
    assert snap.short_ratio == 4.3


def test_in_memory_provider_round_trip():
    provider = InMemoryProvider()
    chain = OptionChain(ticker="X", expiry=dt.date(2026, 8, 14), underlying_price=100.0)
    provider.add_option_chain(chain)
    assert provider.option_expiries("X") == [dt.date(2026, 8, 14)]
    assert provider.option_chain("X", dt.date(2026, 8, 14)) is chain
    assert provider.option_chain("X", dt.date(2026, 9, 14)) is None
    assert provider.earnings_history("X") == []
    assert provider.snapshot("X") is None
