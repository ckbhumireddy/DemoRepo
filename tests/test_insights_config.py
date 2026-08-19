import os
from unittest import mock

import pytest

from earnings_notifier.config import ConfigError
from portfolio_insights.config import InsightsConfig


def test_defaults_from_empty_env():
    with mock.patch.dict(os.environ, {}, clear=True):
        cfg = InsightsConfig.from_env()
    assert cfg.insights_state_file == "state/insights.json"
    assert cfg.insights_move_alert_pct == 5.0
    assert cfg.insights_move_alert_dollars == 5000.0
    assert cfg.insights_portfolio_alert_pct == 2.0
    assert cfg.insights_portfolio_alert_dollars == 15000.0
    assert cfg.insights_max_weight_pct == 20.0
    assert cfg.insights_vol_spike_mult == 2.0
    assert cfg.insights_event_days == 7
    assert cfg.portfolio_file == "portfolio.json"
    assert cfg.portfolio_json == ""
    assert cfg.insights_track_options is False


def test_options_tracking_env_opt_in():
    with mock.patch.dict(os.environ, {"INSIGHTS_TRACK_OPTIONS": "true"},
                         clear=True):
        assert InsightsConfig.from_env().insights_track_options is True


def test_env_overrides():
    env = {
        "INSIGHTS_MOVE_ALERT_PCT": "3.5",
        "INSIGHTS_MOVE_ALERT_DOLLARS": "2500",
        "INSIGHTS_PORTFOLIO_ALERT_PCT": "1.0",
        "INSIGHTS_PORTFOLIO_ALERT_DOLLARS": "7500",
        "INSIGHTS_MAX_WEIGHT_PCT": "25",
        "INSIGHTS_EVENT_DAYS": "10",
        "PORTFOLIO_JSON": '{"positions": []}',
        "PORTFOLIO_FILE": "elsewhere.json",
        "SCHWAB_APP_KEY": "k",
    }
    with mock.patch.dict(os.environ, env, clear=True):
        cfg = InsightsConfig.from_env()
    assert cfg.insights_move_alert_pct == 3.5
    assert cfg.insights_move_alert_dollars == 2500.0
    assert cfg.insights_portfolio_alert_pct == 1.0
    assert cfg.insights_portfolio_alert_dollars == 7500.0
    assert cfg.insights_max_weight_pct == 25.0
    assert cfg.insights_event_days == 10
    assert cfg.portfolio_json == '{"positions": []}'
    assert cfg.portfolio_file == "elsewhere.json"
    assert cfg.schwab_app_key == "k"


def test_bad_float_raises():
    with mock.patch.dict(os.environ, {"INSIGHTS_MOVE_ALERT_PCT": "abc"},
                         clear=True):
        with pytest.raises(ConfigError):
            InsightsConfig.from_env()


def test_validate_requires_a_portfolio_source():
    cfg = InsightsConfig(dry_run=True, portfolio_json="", portfolio_file="")
    with pytest.raises(ConfigError):
        cfg.validate()
    cfg2 = InsightsConfig(dry_run=True, portfolio_file="portfolio.json")
    cfg2.validate()  # file path configured is enough at validation time
