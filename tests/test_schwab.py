import datetime as dt

import pytest

from earnings_analyzer.config import AnalyzerConfig
from earnings_analyzer.models import OptionChain, PriceBar, QuarterResult, Snapshot
from earnings_analyzer.provider import YFinanceMarketData
from earnings_analyzer.schwab import (
    SchwabAuthError,
    SchwabMarketData,
    SchwabSession,
    bars_from_candles,
    build_provider,
    chains_from_payload,
    schwab_symbol,
)


def test_schwab_symbol_share_classes():
    assert schwab_symbol("BRK-B") == "BRK/B"
    assert schwab_symbol("AAPL") == "AAPL"


def test_bars_from_candles():
    ms = int(dt.datetime(2026, 8, 5, 13, 30, tzinfo=dt.timezone.utc).timestamp() * 1000)
    payload = {
        "candles": [
            {"datetime": ms, "open": 99.0, "high": 102.0, "low": 98.0,
             "close": 100.0, "volume": 1e6},
            {"datetime": None, "close": 50.0},   # dropped: no timestamp
            {"datetime": ms, "close": 0.0},      # dropped: bad close
        ]
    }
    bars = bars_from_candles(payload)
    assert len(bars) == 1
    assert bars[0].day == dt.date(2026, 8, 5)
    assert bars[0].close == 100.0
    assert bars_from_candles({}) == []


def test_chains_from_payload():
    payload = {
        "underlyingPrice": 100.5,
        "callExpDateMap": {
            "2026-08-14:5": {
                "100.0": [{
                    "symbol": "X  260814C00100000", "strikePrice": 100.0,
                    "bid": 2.9, "ask": 3.1, "last": 3.0, "volatility": 45.5,
                    "totalVolume": 800, "openInterest": 2000, "inTheMoney": True,
                }],
            },
            "2026-09-18:40": {
                "100.0": [{"strikePrice": 100.0, "bid": 5.0, "ask": 5.4,
                           "volatility": "NaN"}],
            },
        },
        "putExpDateMap": {
            "2026-08-14:5": {
                "100.0": [{"strikePrice": 100.0, "bid": 2.8, "ask": 3.0,
                           "volatility": -999.0}],
            },
        },
    }
    chains = chains_from_payload(payload, "X")
    assert sorted(chains) == [dt.date(2026, 8, 14), dt.date(2026, 9, 18)]
    front = chains[dt.date(2026, 8, 14)]
    assert front.underlying_price == 100.5
    assert front.atm_call().implied_volatility == 0.455   # percent -> decimal
    assert front.atm_put().implied_volatility is None     # sentinel dropped
    assert chains[dt.date(2026, 9, 18)].atm_call().implied_volatility is None


def test_session_requires_credentials_and_token():
    with pytest.raises(SchwabAuthError):
        SchwabSession(app_key="", app_secret="s")
    with pytest.raises(SchwabAuthError):
        SchwabSession(app_key="k", app_secret="s")            # no token at all
    with pytest.raises(SchwabAuthError):
        SchwabSession(app_key="k", app_secret="s", token_json="{not json")
    with pytest.raises(SchwabAuthError):
        SchwabSession(app_key="k", app_secret="s", token_json='{"access_token": "x"}')


class _FallbackProvider:
    def __init__(self):
        self.calls = []

    def earnings_history(self, ticker, limit=20):
        self.calls.append("earnings_history")
        return [QuarterResult(date=dt.date(2026, 5, 5))]

    def price_history(self, ticker, days=700):
        self.calls.append("price_history")
        return [PriceBar(day=dt.date(2026, 8, 5), open=1, high=1, low=1, close=1.0)]

    def snapshot(self, ticker):
        self.calls.append("snapshot")
        return Snapshot(company="Fallback Inc.")

    def option_expiries(self, ticker):
        self.calls.append("option_expiries")
        return [dt.date(2026, 8, 14)]

    def option_chain(self, ticker, expiry):
        self.calls.append("option_chain")
        return OptionChain(ticker=ticker, expiry=expiry, underlying_price=100.0)


class _ExplodingSession:
    def get(self, path, params):
        raise RuntimeError("schwab down")


def test_hybrid_falls_back_and_disables_on_error():
    fallback = _FallbackProvider()
    provider = SchwabMarketData(_ExplodingSession(), fallback)

    assert provider.price_history("AAPL")[0].close == 1.0
    assert provider._disabled is True
    # Once disabled, no further Schwab attempts — straight to fallback.
    assert provider.option_expiries("AAPL") == [dt.date(2026, 8, 14)]
    assert fallback.calls.count("price_history") == 1
    assert fallback.calls.count("option_expiries") == 1


def test_hybrid_always_delegates_earnings_and_snapshot():
    fallback = _FallbackProvider()
    provider = SchwabMarketData(_ExplodingSession(), fallback)
    assert provider.earnings_history("AAPL")[0].date == dt.date(2026, 5, 5)
    assert provider.snapshot("AAPL").company == "Fallback Inc."
    assert provider._disabled is False  # earnings/snapshot never touch Schwab


class _CannedSession:
    """Serves one chains payload; fails price history."""

    def get(self, path, params):
        if path == "/chains":
            return {
                "underlyingPrice": 100.0,
                "callExpDateMap": {"2026-08-14:5": {"100.0": [
                    {"strikePrice": 100.0, "bid": 2.9, "ask": 3.1}]}},
                "putExpDateMap": {"2026-08-14:5": {"100.0": [
                    {"strikePrice": 100.0, "bid": 2.8, "ask": 3.0}]}},
            }
        raise RuntimeError("not implemented")


def test_hybrid_serves_schwab_chains_and_caches():
    fallback = _FallbackProvider()
    provider = SchwabMarketData(_CannedSession(), fallback)
    assert provider.option_expiries("X") == [dt.date(2026, 8, 14)]
    chain = provider.option_chain("X", dt.date(2026, 8, 14))
    assert chain.underlying_price == 100.0
    assert chain.atm_call().mid == 3.0
    assert "option_chain" not in fallback.calls  # served from Schwab cache
    # Unknown expiry falls through to the fallback provider.
    provider.option_chain("X", dt.date(2026, 12, 18))
    assert "option_chain" in fallback.calls


class _LeapsSession:
    """Serves a chain only for the exact-expiry request (a 2027 LEAPS)."""

    def __init__(self):
        self.requests = []

    def get(self, path, params):
        self.requests.append(params)
        if params.get("fromDate") == "2027-09-17":
            return {
                "underlyingPrice": 250.0,
                "callExpDateMap": {"2027-09-17:395": {"1500.0": [
                    {"strikePrice": 1500.0, "bid": 640.0, "ask": 641.4,
                     "closePrice": 772.0, "netChange": -131.3}]}},
                "putExpDateMap": {},
            }
        return {"underlyingPrice": 250.0, "callExpDateMap": {},
                "putExpDateMap": {}}


def test_option_chain_at_fetches_exact_far_expiry():
    fallback = _FallbackProvider()
    session = _LeapsSession()
    provider = SchwabMarketData(session, fallback)
    expiry = dt.date(2027, 9, 17)
    chain = provider.option_chain_at("SNDK", expiry)
    assert chain is not None and chain.expiry == expiry
    contract = chain.calls[0]
    assert contract.day_change == -131.3          # Schwab netChange captured
    assert "option_chain" not in fallback.calls   # no Yahoo fallback needed
    # Second call is served from the cache — no new request.
    provider.option_chain_at("SNDK", expiry)
    assert len(session.requests) == 1


def test_option_chain_at_falls_back_when_schwab_has_nothing():
    fallback = _FallbackProvider()
    provider = SchwabMarketData(_LeapsSession(), fallback)
    chain = provider.option_chain_at("SNDK", dt.date(2027, 12, 17))
    assert chain is not None                      # served by the fallback
    assert "option_chain" in fallback.calls


def test_build_provider_without_schwab_config_is_yahoo():
    cfg = AnalyzerConfig(dry_run=True)
    assert isinstance(build_provider(cfg), YFinanceMarketData)


def test_build_provider_with_key_but_no_token_falls_back():
    cfg = AnalyzerConfig(dry_run=True, schwab_app_key="k", schwab_app_secret="s")
    assert isinstance(build_provider(cfg), YFinanceMarketData)


def test_build_provider_with_full_config_is_hybrid():
    cfg = AnalyzerConfig(
        dry_run=True, schwab_app_key="k", schwab_app_secret="s",
        schwab_token='{"access_token": "a", "refresh_token": "r", "expires_at": 0}',
    )
    assert isinstance(build_provider(cfg), SchwabMarketData)
