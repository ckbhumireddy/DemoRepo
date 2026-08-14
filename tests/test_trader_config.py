import os
from unittest import mock

from earnings_trader.config import TraderConfig


def test_defaults_from_empty_env():
    with mock.patch.dict(os.environ, {}, clear=True):
        cfg = TraderConfig.from_env()
    assert cfg.trader_ledger_file == "state/trader.json"
    assert cfg.trader_start_balance == 25000.0
    assert cfg.trader_max_risk == 5000.0
    assert cfg.rich_threshold == 1.25
    assert cfg.window_file == "out/window.json"


def test_env_overrides():
    env = {
        "TRADER_LEDGER_FILE": "x/ledger.json",
        "TRADER_START_BALANCE": "50000",
        "TRADER_MAX_RISK": "2500",
        "TRADER_RICH_THRESHOLD": "1.4",
        "TRADER_MAX_WORKERS": "2",
        "WINDOW_FILE": "elsewhere/window.json",
        "SCHWAB_APP_KEY": "k",
    }
    with mock.patch.dict(os.environ, env, clear=True):
        cfg = TraderConfig.from_env()
    assert cfg.trader_ledger_file == "x/ledger.json"
    assert cfg.trader_start_balance == 50000.0
    assert cfg.trader_max_risk == 2500.0
    assert cfg.rich_threshold == 1.4
    assert cfg.trader_max_workers == 2
    assert cfg.window_file == "elsewhere/window.json"
    assert cfg.schwab_app_key == "k"


def test_empty_ledger_file_disables():
    with mock.patch.dict(os.environ, {"TRADER_LEDGER_FILE": ""}, clear=True):
        cfg = TraderConfig.from_env()
    assert cfg.trader_ledger_file == ""
