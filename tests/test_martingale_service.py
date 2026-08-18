import datetime as dt
import json
import os

import pytest

import martingale_trader.service as service_mod
from earnings_analyzer.models import PriceBar
from martingale_trader.config import MartingaleConfig
from martingale_trader.service import run_martingale

D1 = dt.date(2026, 8, 17)
D2 = dt.date(2026, 8, 18)


class RecordingNotifier:
    sent = []

    def __init__(self, config):
        self.config = config

    def send(self, subject, text, html):
        if not self.config.dry_run:
            RecordingNotifier.sent.append(subject)


class FakeProvider:
    def __init__(self, closes):
        self._bars = [
            PriceBar(day=day, open=c, high=c, low=c, close=c)
            for day, c in closes
        ]

    def price_history(self, ticker, days=700):
        return self._bars


def _config(tmp_path, dry_run=False, **kw):
    return MartingaleConfig(
        dry_run=dry_run,
        smtp_host="smtp.example.com",
        email_from="a@example.com",
        email_to=["a@example.com"],
        martingale_state_file=str(tmp_path / "martingale.json"),
        **kw,
    )


def _patch_notifier(monkeypatch):
    RecordingNotifier.sent = []
    monkeypatch.setattr(service_mod, "EmailNotifier", RecordingNotifier)


def _state(config):
    with open(config.martingale_state_file, encoding="utf-8") as fh:
        return json.load(fh)


def test_first_run_opens_round_and_emails(tmp_path, monkeypatch):
    _patch_notifier(monkeypatch)
    config = _config(tmp_path)
    result = run_martingale(
        config, today=D1, provider=FakeProvider([(D1, 6400.0)])
    )
    assert (result.settled, result.opened, result.emails_sent) == (0, 1, 1)
    assert "first round opened" in result.subject
    state = _state(config)
    assert state["open_round"]["entry_price"] == 6400.0
    assert state["open_round"]["notional"] == 5000.0
    assert state["balance"] == 25000.0


def test_duplicate_run_same_day_skips(tmp_path, monkeypatch):
    _patch_notifier(monkeypatch)
    config = _config(tmp_path)
    provider = FakeProvider([(D1, 6400.0)])
    run_martingale(config, today=D1, provider=provider)
    result = run_martingale(config, today=D1, provider=provider)
    assert result.skipped is True
    assert len(RecordingNotifier.sent) == 1


def test_next_day_settles_loss_and_doubles(tmp_path, monkeypatch):
    _patch_notifier(monkeypatch)
    config = _config(tmp_path)
    run_martingale(config, today=D1, provider=FakeProvider([(D1, 6400.0)]))
    result = run_martingale(
        config, today=D2,
        provider=FakeProvider([(D1, 6400.0), (D2, 6336.0)]),   # -1%
    )
    assert (result.settled, result.opened) == (1, 1)
    state = _state(config)
    assert state["balance"] == 24950.0
    assert state["step"] == 1
    assert state["open_round"]["notional"] == 10000.0
    assert state["rounds"][0]["pnl"] == -50.0


def test_bust_stops_trading(tmp_path, monkeypatch):
    _patch_notifier(monkeypatch)
    config = _config(tmp_path, martingale_start_balance=100.0)
    run_martingale(config, today=D1, provider=FakeProvider([(D1, 6400.0)]))
    result = run_martingale(
        config, today=D2,
        provider=FakeProvider([(D1, 6400.0), (D2, 6272.0)]),   # -2% = -$100
    )
    assert (result.settled, result.opened) == (1, 0)
    assert "BUSTED" in result.subject
    state = _state(config)
    assert state["busted"] is True
    assert state["open_round"] is None
    # Later runs do nothing and send nothing.
    later = run_martingale(
        config, today=D2 + dt.timedelta(days=1),
        provider=FakeProvider([(D2 + dt.timedelta(days=1), 6000.0)]),
    )
    assert later.skipped is True
    assert len(RecordingNotifier.sent) == 2


def test_dry_run_saves_nothing(tmp_path, monkeypatch):
    _patch_notifier(monkeypatch)
    config = _config(tmp_path, dry_run=True)
    result = run_martingale(
        config, today=D1, provider=FakeProvider([(D1, 6400.0)])
    )
    assert result.emails_sent == 0
    assert not os.path.exists(config.martingale_state_file)
    assert RecordingNotifier.sent == []


def test_future_bars_are_ignored(tmp_path, monkeypatch):
    _patch_notifier(monkeypatch)
    config = _config(tmp_path)
    run_martingale(
        config, today=D1,
        provider=FakeProvider([(D1, 6400.0), (D2, 9999.0)]),
    )
    assert _state(config)["open_round"]["entry_price"] == 6400.0


def test_no_bars_raises(tmp_path, monkeypatch):
    _patch_notifier(monkeypatch)
    config = _config(tmp_path)
    with pytest.raises(RuntimeError):
        run_martingale(config, today=D1, provider=FakeProvider([]))


def test_missing_schwab_credentials_abort_the_run(tmp_path, monkeypatch):
    # Schwab only, no Yahoo fallback: without credentials the run fails
    # before it touches any market data.
    from earnings_notifier.config import ConfigError

    _patch_notifier(monkeypatch)
    config = _config(tmp_path)
    with pytest.raises(ConfigError, match="SCHWAB_APP_KEY"):
        run_martingale(config, today=D1)


def test_schwab_history_requests_spx_daily_bars(monkeypatch):
    from martingale_trader.service import SchwabHistory

    calls = []

    class FakeSession:
        def get(self, path, params):
            calls.append((path, params))
            return {"candles": [
                {"datetime": 1755475200000, "open": 6400.0, "high": 6410.0,
                 "low": 6390.0, "close": 6405.0, "volume": 1.0},
            ]}

    bars = SchwabHistory(FakeSession()).price_history("$SPX")
    assert calls[0][0] == "/pricehistory"
    assert calls[0][1]["symbol"] == "$SPX"
    assert calls[0][1]["frequencyType"] == "daily"
    assert len(bars) == 1 and bars[0].close == 6405.0
