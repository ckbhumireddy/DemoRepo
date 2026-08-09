import datetime as dt

from earnings_analyzer.liquidity import screen_liquidity
from earnings_analyzer.models import OptionChain, OptionContract

EXPIRY = dt.date(2026, 8, 14)


def _chain(oi=1500.0, vol=500.0, bid=2.90, ask=3.10):
    def _c(kind):
        return OptionContract(
            contract_symbol=f"X{kind}", option_type=kind, strike=100.0,
            expiry=EXPIRY, bid=bid, ask=ask, last=(bid + ask) / 2,
            volume=vol, open_interest=oi,
        )

    return OptionChain(
        ticker="X", expiry=EXPIRY, underlying_price=100.0,
        calls=[_c("call")], puts=[_c("put")],
    )


def test_liquid_chain_passes():
    report = screen_liquidity(_chain())
    assert report.tradeable is True
    assert report.reasons == []
    assert round(report.atm_spread_pct, 1) == 6.7  # 0.20 / 3.00


def test_low_open_interest_fails():
    report = screen_liquidity(_chain(oi=50))
    assert report.tradeable is False
    assert any("open interest" in r for r in report.reasons)


def test_low_volume_fails():
    report = screen_liquidity(_chain(vol=5))
    assert report.tradeable is False
    assert any("volume" in r for r in report.reasons)


def test_wide_spread_fails():
    report = screen_liquidity(_chain(bid=2.0, ask=4.0))  # ~67% of mid
    assert report.tradeable is False
    assert any("spread" in r for r in report.reasons)


def test_empty_chain_fails():
    empty = OptionChain(ticker="X", expiry=EXPIRY, underlying_price=100.0)
    report = screen_liquidity(empty)
    assert report.tradeable is False
    assert report.reasons == ["no ATM quotes"]


def test_missing_quote_fields_fail_with_reasons():
    report = screen_liquidity(_chain(oi=None, vol=None))
    assert report.tradeable is False
    assert "open interest unavailable" in report.reasons
    assert "volume unavailable" in report.reasons
