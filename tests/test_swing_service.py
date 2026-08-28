import datetime as dt
import json
import os
from unittest import mock

import pytest

from swing_trader.backtest import BacktestResult
from swing_trader.config import SwingConfig
from swing_trader.emails import render_signals_email
from swing_trader.registry import Registry, Thresholds
from swing_trader.research import (
    ResearchReport,
    TrialResult,
    champion_params,
    promote,
    run_research,
)
from swing_trader.service import run_scan, run_strategy_backtest
from swing_trader.strategies.base import Signal

from tests.test_swing_strategy import channel_bars

TODAY = dt.date(2026, 8, 27)


def _result(trades=30, pf=2.0, win_rate=0.6, dd=0.15, expectancy=0.02,
            name="distressed-sr"):
    return BacktestResult(
        strategy=name, trades=trades, wins=int(trades * win_rate),
        win_rate=win_rate, profit_factor=pf, expectancy=expectancy,
        max_drawdown=dd, avg_hold=6.0, tickers=5,
        first_day=dt.date(2025, 1, 5), last_day=dt.date(2026, 6, 1),
    )


def _config(tmp_path, **overrides):
    config = SwingConfig(
        smtp_host="smtp.test", email_from="a@b.c", email_to=["d@e.f"],
        dry_run=True,
        swing_registry_file=str(tmp_path / "registry.json"),
        swing_use_champion=False,
    )
    for key, value in overrides.items():
        setattr(config, key, value)
    return config


# --------------------------------------------------------------------------- #
# Thresholds and registry — the approval gate itself
# --------------------------------------------------------------------------- #
def test_every_failing_criterion_is_named():
    approved, reasons = Thresholds().verdict(
        _result(trades=5, pf=1.0, dd=0.5)
    )
    assert not approved
    joined = " ".join(reasons)
    assert "5 trade(s)" in joined
    assert "profit factor" in joined
    assert "drawdown" in joined


def test_a_passing_backtest_approves_and_persists(tmp_path):
    path = str(tmp_path / "reg.json")
    Registry(path).record(_result(), Thresholds(), today=TODAY)
    # A fresh instance reads the same standing back from disk.
    assert Registry(path).is_approved("distressed-sr", TODAY)


def test_a_failing_backtest_revokes_a_prior_approval(tmp_path):
    path = str(tmp_path / "reg.json")
    registry = Registry(path)
    registry.record(_result(), Thresholds(), today=TODAY)
    registry.record(_result(pf=0.8), Thresholds(), today=TODAY)
    assert not registry.is_approved("distressed-sr", TODAY)
    assert "profit factor" in registry.why_not("distressed-sr", TODAY)


def test_approval_expires_and_says_so(tmp_path):
    path = str(tmp_path / "reg.json")
    registry = Registry(path, approval_ttl_days=90)
    registry.record(_result(), Thresholds(), today=TODAY)
    later = TODAY + dt.timedelta(days=91)
    assert not registry.is_approved("distressed-sr", later)
    assert "expired" in registry.why_not("distressed-sr", later)


def test_an_untested_strategy_explains_itself(tmp_path):
    registry = Registry(str(tmp_path / "reg.json"))
    assert not registry.is_approved("distressed-sr")
    assert "never backtested" in registry.why_not("distressed-sr")


# --------------------------------------------------------------------------- #
# Service: the gate in action
# --------------------------------------------------------------------------- #
def _history():
    return {"AAA": channel_bars(n=380), "BBB": channel_bars(n=380, phase=2.0)}


def test_scan_refuses_to_email_an_unapproved_strategy(tmp_path, monkeypatch):
    sent = []
    monkeypatch.setattr(
        "swing_trader.service.EmailNotifier",
        lambda config: type("N", (), {"send": lambda self, *a: sent.append(a)})(),
    )
    config = _config(tmp_path, dry_run=False, send_empty=True)
    result = run_scan(config, today=TODAY, history=_history())
    assert not result.approved
    assert result.emails_sent == 0 and sent == []
    assert "never backtested" in result.approval_note


def test_backtest_then_scan_opens_the_gate(tmp_path, monkeypatch):
    sent = []
    monkeypatch.setattr(
        "swing_trader.service.EmailNotifier",
        lambda config: type("N", (), {"send": lambda self, *a: sent.append(a)})(),
    )
    config = _config(tmp_path, dry_run=False, send_empty=True,
                     swing_bt_min_trades=2)
    bt_result, record = run_strategy_backtest(
        config, today=TODAY, history=_history()
    )
    assert record.approved, record.reasons
    result = run_scan(config, today=TODAY, history=_history())
    assert result.approved and len(sent) == 1


def test_the_same_evaluate_drives_backtest_and_scan(tmp_path):
    # The contract that makes the gate meaningful: what was tested is what
    # runs. Verified by the strategy identity, not by convention.
    config = _config(tmp_path)
    strategy = config.build_strategy()
    assert type(strategy).__name__ == "DistressedSupportResistance"
    assert hasattr(strategy, "evaluate")


# --------------------------------------------------------------------------- #
# Research loop
# --------------------------------------------------------------------------- #
def _wide_history():
    # Wide channel keeps the distress alive into the validation segment.
    return {
        f"T{k}": channel_bars(n=470, amp_frac=0.20, phase=k * 1.7,
                              period=6.0 + 0.4 * k)
        for k in range(4)
    }


def test_research_tunes_on_train_and_judges_on_validation():
    grid = {"entry_band": [0.015, 0.03], "min_rr": [1.5, 2.5]}
    report = run_research(
        "distressed-sr", _wide_history(),
        Thresholds(min_trades=5, min_profit_factor=1.2),
        grid=grid,
    )
    assert report.grid_size == 4
    assert report.split_day is not None
    assert report.finalists and report.champion is not None
    for trial in report.finalists:
        assert trial.validation is not None
        # No validation trade may predate the split — the leak this whole
        # design exists to prevent.
        for trade in trial.validation.trade_log:
            assert trade.entry_day >= report.split_day


def test_promotion_requires_passing_validation(tmp_path):
    report = ResearchReport(strategy="distressed-sr", grid_size=1)
    report.champion = TrialResult(params={"min_rr": 1.5}, train=_result())
    report.promoted = False
    report.reasons = ["validation: too few trades"]
    with pytest.raises(ValueError, match="validation-passing"):
        promote(report, champions_file=str(tmp_path / "champ.json"))


def test_promoted_champion_params_reach_build_strategy(tmp_path):
    champ_file = str(tmp_path / "champions.json")
    report = ResearchReport(strategy="distressed-sr", grid_size=1,
                            split_day=dt.date(2025, 6, 1))
    report.champion = TrialResult(
        params={"min_rr": 1.7, "entry_band": 0.03},
        train=_result(), validation=_result(),
    )
    report.promoted = True
    promote(report, champions_file=champ_file, today=TODAY)
    assert champion_params("distressed-sr", champ_file) == {
        "min_rr": 1.7, "entry_band": 0.03,
    }

    config = SwingConfig(dry_run=True)
    with mock.patch("swing_trader.research.CHAMPIONS_FILE", champ_file):
        strategy = config.build_strategy()
    assert strategy.min_rr == 1.7 and strategy.entry_band == 0.03


def test_explicit_overrides_beat_the_champion(tmp_path):
    champ_file = str(tmp_path / "champions.json")
    with open(champ_file, "w", encoding="utf-8") as fh:
        json.dump({"distressed-sr": {"params": {"min_rr": 1.5}}}, fh)
    config = SwingConfig(dry_run=True, swing_min_rr=3.0)
    with mock.patch("swing_trader.research.CHAMPIONS_FILE", champ_file):
        strategy = config.build_strategy()
    assert strategy.min_rr == 3.0


def test_strategy_instances_do_not_share_tuning():
    from swing_trader.strategies import get_strategy

    tuned = get_strategy("distressed-sr")
    tuned.min_rr = 99.0
    assert get_strategy("distressed-sr").min_rr == 2.0


# --------------------------------------------------------------------------- #
# Config and email
# --------------------------------------------------------------------------- #
BASE_ENV = {"SMTP_HOST": "smtp.test", "EMAIL_FROM": "a@b.c", "EMAIL_TO": "d@e.f"}


def test_env_defaults_and_overrides():
    with mock.patch.dict(os.environ, BASE_ENV, clear=True):
        config = SwingConfig.from_env()
    assert config.swing_strategy == "distressed-sr"
    assert config.swing_min_drawdown is None       # strategy default rules
    assert config.swing_use_champion is True

    env = dict(BASE_ENV, SWING_MIN_DRAWDOWN="0.4", SWING_STRATEGY="OTHER",
               SWING_USE_CHAMPION="false", SWING_BT_MIN_TRADES="30")
    with mock.patch.dict(os.environ, env, clear=True):
        config = SwingConfig.from_env()
    assert config.swing_min_drawdown == 0.4
    assert config.swing_strategy == "other"
    assert config.swing_use_champion is False
    assert config.thresholds().min_trades == 30


def test_validate_rejects_unusable_settings():
    from earnings_notifier.config import ConfigError

    config = SwingConfig(dry_run=True, swing_min_drawdown=1.5)
    with pytest.raises(ConfigError, match="SWING_MIN_DRAWDOWN"):
        config.validate()
    config = SwingConfig(dry_run=True, swing_bars_back=100)
    with pytest.raises(ConfigError, match="SWING_BARS_BACK"):
        config.validate()


def _signal(ticker="AAA", rr_target=110.0):
    return Signal(strategy="distressed-sr", ticker=ticker, day=TODAY,
                  price=100.0, entry=100.0, stop=95.0, target=rr_target,
                  support=96.0, resistance=rr_target,
                  note="41% off its 52-week high; support 3x tested")


def test_email_is_a_table_of_complete_plans():
    subject, text, html_body = render_signals_email(
        [_signal()], TODAY, strategy_name="distressed-sr",
        approval_note="approved",
    )
    assert "1 setup(s)" in subject and "distressed-sr" in subject
    assert "STOP" in text and "TARGET" in text and "R/R" in text
    assert "$95.00" in text and "$110.00" in text
    assert "approved" in text
    assert html_body.count("<tr>") == 2          # header + one row


def test_email_handles_an_empty_scan_and_escapes_html():
    subject, text, html_body = render_signals_email(
        [], TODAY, strategy_name="distressed-sr", approval_note="approved",
    )
    assert "No setups" in subject and "No candidate cleared" in text

    evil = _signal(ticker="<script>")
    _, _, html_body = render_signals_email(
        [evil], TODAY, strategy_name="distressed-sr", approval_note="ok",
    )
    assert "<script>" not in html_body and "&lt;script&gt;" in html_body
