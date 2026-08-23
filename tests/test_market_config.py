import importlib.util
import os
import pathlib
from unittest import mock

import pytest

from earnings_notifier.config import ConfigError
from market_insights.config import MarketInsightsConfig

_spec = importlib.util.spec_from_file_location(
    "tradestation_auth",
    pathlib.Path(__file__).resolve().parent.parent / "scripts" / "tradestation_auth.py",
)
tradestation_auth = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tradestation_auth)


BASE_ENV = {
    "SMTP_HOST": "smtp.test",
    "EMAIL_FROM": "a@b.c",
    "EMAIL_TO": "d@e.f",
}


def test_defaults_are_sane_without_any_insights_variables():
    with mock.patch.dict(os.environ, BASE_ENV, clear=True):
        config = MarketInsightsConfig.from_env()
    assert config.insights_top_n == 25
    assert config.insights_universe == "sp500"
    assert config.insights_min_rvol == 2.0
    assert config.insights_min_price == 5.0
    assert config.insights_max_candidates == 150
    assert config.tradestation_environment == "live"
    assert config.email_to == ["d@e.f"]        # base config still applies


def test_environment_overrides_are_typed():
    env = dict(BASE_ENV, INSIGHTS_TOP_N="10", INSIGHTS_MIN_RVOL="3.5",
               INSIGHTS_MIN_DOLLAR_VOLUME="1e7", INSIGHTS_UNIVERSE="PORTFOLIO",
               INSIGHTS_MAX_CANDIDATES="40", TRADESTATION_ENVIRONMENT="SIM",
               TRADESTATION_CLIENT_ID=" abc ")
    with mock.patch.dict(os.environ, env, clear=True):
        config = MarketInsightsConfig.from_env()
    assert config.insights_top_n == 10
    assert config.insights_min_rvol == 3.5
    assert config.insights_min_dollar_volume == 1e7
    assert config.insights_universe == "portfolio"    # normalized
    assert config.insights_max_candidates == 40
    assert config.tradestation_environment == "sim"
    assert config.tradestation_client_id == "abc"     # trimmed


def test_a_non_numeric_threshold_is_rejected_with_its_name():
    with mock.patch.dict(os.environ, dict(BASE_ENV, INSIGHTS_MIN_RVOL="lots"),
                         clear=True):
        with pytest.raises(ConfigError, match="INSIGHTS_MIN_RVOL"):
            MarketInsightsConfig.from_env()


def test_validate_rejects_unusable_scan_settings():
    config = MarketInsightsConfig(dry_run=True)
    config.insights_lookback = 2
    with pytest.raises(ConfigError, match="INSIGHTS_LOOKBACK"):
        config.validate()

    config = MarketInsightsConfig(dry_run=True, insights_min_rvol=0)
    with pytest.raises(ConfigError, match="INSIGHTS_MIN_RVOL"):
        config.validate()

    config = MarketInsightsConfig(dry_run=True, insights_top_n=0)
    with pytest.raises(ConfigError, match="INSIGHTS_TOP_N"):
        config.validate()


def test_validate_still_enforces_the_base_email_requirements():
    config = MarketInsightsConfig(dry_run=False)
    with pytest.raises(ConfigError, match="SMTP_HOST"):
        config.validate()
    # dry-run skips the email checks, as everywhere else in the repo.
    MarketInsightsConfig(dry_run=True).validate()


# --------------------------------------------------------------------------- #
# Auth script
# --------------------------------------------------------------------------- #
def test_authorize_url_requests_offline_access_and_the_api_audience():
    url = tradestation_auth.authorize_url("client-123", "http://localhost")
    assert url.startswith("https://signin.tradestation.com/authorize?")
    assert "response_type=code" in url
    assert "client_id=client-123" in url
    assert "offline_access" in url          # without it there is no refresh token
    assert "MarketData" in url
    assert "audience=https%3A%2F%2Fapi.tradestation.com" in url
    assert "redirect_uri=http%3A%2F%2Flocalhost" in url


def test_auth_script_exits_2_without_a_client_id(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)   # no .env here
    with mock.patch.dict(os.environ, {}, clear=True):
        with mock.patch("sys.argv", ["tradestation_auth.py"]):
            assert tradestation_auth.main() == 2
