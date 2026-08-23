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


# --------------------------------------------------------------------------- #
# .env parsing — the shapes real files actually arrive in
# --------------------------------------------------------------------------- #
def _load(tmp_path, text, encoding="utf-8"):
    path = tmp_path / ".env"
    path.write_text(text, encoding=encoding)
    with mock.patch.dict(os.environ, {}, clear=True):
        assert tradestation_auth.load_env_file(str(path)) is True
        return dict(os.environ)


def test_env_file_survives_a_utf8_bom(tmp_path):
    # Notepad and PowerShell redirects write a BOM; without utf-8-sig it
    # glues to the first key and that single line silently vanishes.
    env = _load(tmp_path, "TRADESTATION_CLIENT_ID=abc\n", encoding="utf-8-sig")
    assert env["TRADESTATION_CLIENT_ID"] == "abc"


def test_env_file_accepts_an_export_prefix(tmp_path):
    env = _load(tmp_path, "export TRADESTATION_CLIENT_ID=abc\n")
    assert env["TRADESTATION_CLIENT_ID"] == "abc"


def test_commented_lines_stay_inert(tmp_path):
    # .env.example ships these commented; copying it must not look like it
    # configured something.
    env = _load(tmp_path, "# TRADESTATION_CLIENT_ID=abc\n")
    assert "TRADESTATION_CLIENT_ID" not in env


def test_env_file_tolerates_spacing_and_quotes(tmp_path):
    env = _load(
        tmp_path,
        'TRADESTATION_CLIENT_ID = "abc"  \nTRADESTATION_CLIENT_SECRET=\'s3\'\n',
    )
    assert env["TRADESTATION_CLIENT_ID"] == "abc"
    assert env["TRADESTATION_CLIENT_SECRET"] == "s3"


def test_a_loaded_env_file_explains_a_missing_client_id(tmp_path, monkeypatch,
                                                        capsys):
    # The failure the user actually hits: .env found, variable commented out.
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("# TRADESTATION_CLIENT_ID=abc\n", encoding="utf-8")
    with mock.patch.dict(os.environ, {}, clear=True):
        with mock.patch("sys.argv", ["tradestation_auth.py"]):
            assert tradestation_auth.main() == 2
    stderr = capsys.readouterr().err
    assert "was loaded but TRADESTATION_CLIENT_ID was not set" in stderr
    assert "commented out" in stderr


def test_setup_stops_before_a_confusing_401_when_the_secret_is_missing(
    tmp_path, monkeypatch, capsys
):
    # Without the secret TradeStation answers the exchange with a bare
    # "401 access_denied", which says nothing about the cause.
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(
        "TRADESTATION_CLIENT_ID=abc\n# TRADESTATION_CLIENT_SECRET=s3\n",
        encoding="utf-8",
    )
    with mock.patch.dict(os.environ, {}, clear=True):
        with mock.patch("sys.argv", ["tradestation_auth.py"]):
            assert tradestation_auth.main() == 2
    stderr = capsys.readouterr().err
    assert "TRADESTATION_CLIENT_SECRET is not set" in stderr
    assert "commented out" in stderr
    assert "--public-client" in stderr


def test_a_public_pkce_client_may_proceed_without_a_secret(tmp_path, monkeypatch):
    # The escape hatch must not be blocked by the check above; stop at the
    # interactive prompt rather than going near the network.
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("builtins.input", lambda *a: "")
    with mock.patch.dict(os.environ, {"TRADESTATION_CLIENT_ID": "abc"}, clear=True):
        with mock.patch("sys.argv", ["tradestation_auth.py", "--public-client"]):
            # No ?code= in the pasted value, so it exits 1 having never
            # reached the token call.
            assert tradestation_auth.main() == 1
