import datetime as dt
import json
import os

import pytest

import condor_trader.service as service_mod
from earnings_analyzer.models import OptionChain, OptionContract, PriceBar
from condor_trader.config import CondorConfig
from condor_trader.service import run_condor

MONDAY = dt.date(2026, 3, 2)
EXPIRY = dt.date(2026, 3, 9)
NEXT_EXPIRY = dt.date(2026, 3, 16)


class RecordingNotifier:
    sent = []

    def __init__(self, config):
        self.config = config

    def send(self, subject, text, html):
        if not self.config.dry_run:
            RecordingNotifier.sent.append(subject)


def _chain(expiry, spot=6730.0):
    chain = OptionChain(ticker="$SPX", expiry=expiry, underlying_price=spot)
    for strike, kind, mid in ((6645.0, "put", 2.0), (6640.0, "put", 1.4),
                              (6815.0, "call", 1.5), (6820.0, "call", 1.0)):
        bucket = chain.calls if kind == "call" else chain.puts
        bucket.append(OptionContract(
            contract_symbol=f"{strike}{kind}", option_type=kind,
            strike=strike, expiry=expiry, bid=mid, ask=mid,
        ))
    return chain


class FakeChains:
    def __init__(self, *chains):
        self._chains = {c.expiry: c for c in chains}

    def expiries(self, today):
        return sorted(self._chains)

    def chain(self, expiry, today):
        return self._chains.get(expiry)


class FakeHistory:
    def __init__(self, closes):
        self._bars = [PriceBar(day=d, open=c, high=c, low=c, close=c)
                      for d, c in closes]

    def price_history(self, ticker, days=30):
        return self._bars


def _config(tmp_path, dry_run=False, **kw):
    return CondorConfig(
        dry_run=dry_run,
        smtp_host="smtp.example.com",
        email_from="a@example.com",
        email_to=["a@example.com"],
        condor_state_file=str(tmp_path / "condor.json"),
        **kw,
    )


def _patch(monkeypatch):
    RecordingNotifier.sent = []
    monkeypatch.setattr(service_mod, "EmailNotifier", RecordingNotifier)


def _state(config):
    with open(config.condor_state_file, encoding="utf-8") as fh:
        return json.load(fh)


def test_first_run_opens_condor(tmp_path, monkeypatch):
    _patch(monkeypatch)
    config = _config(tmp_path)
    result = run_condor(
        config, today=MONDAY,
        history=FakeHistory([(MONDAY, 6730.0)]),
        chains=FakeChains(_chain(EXPIRY)),
    )
    assert (result.settled, result.opened, result.emails_sent) == (0, 1, 1)
    pos = _state(config)["positions"][0]
    assert pos["qty"] == 1 and pos["step"] == 0
    assert pos["credit"] == 1.1
    assert pos["risk_dollars"] == 390.0
    assert "opened" in result.subject


def test_duplicate_run_skips(tmp_path, monkeypatch):
    _patch(monkeypatch)
    config = _config(tmp_path)
    kw = dict(history=FakeHistory([(MONDAY, 6730.0)]),
              chains=FakeChains(_chain(EXPIRY)))
    run_condor(config, today=MONDAY, **kw)
    result = run_condor(config, today=MONDAY, **kw)
    assert result.skipped is True
    assert len(RecordingNotifier.sent) == 1


def test_win_settles_resets_and_reopens(tmp_path, monkeypatch):
    _patch(monkeypatch)
    config = _config(tmp_path)
    run_condor(config, today=MONDAY,
               history=FakeHistory([(MONDAY, 6730.0)]),
               chains=FakeChains(_chain(EXPIRY)))
    result = run_condor(
        config, today=EXPIRY + dt.timedelta(days=1),
        history=FakeHistory([(MONDAY, 6730.0), (EXPIRY, 6730.0)]),
        chains=FakeChains(_chain(NEXT_EXPIRY)),
    )
    assert (result.settled, result.opened) == (1, 1)
    state = _state(config)
    assert state["balance"] == 25110.0
    assert state["step"] == 0
    new = [p for p in state["positions"] if p["status"] == "open"][0]
    assert new["expiry"] == NEXT_EXPIRY.isoformat() and new["qty"] == 1


def test_loss_doubles_the_next_condor(tmp_path, monkeypatch):
    _patch(monkeypatch)
    config = _config(tmp_path)
    run_condor(config, today=MONDAY,
               history=FakeHistory([(MONDAY, 6730.0)]),
               chains=FakeChains(_chain(EXPIRY)))
    result = run_condor(
        config, today=EXPIRY + dt.timedelta(days=1),
        history=FakeHistory([(MONDAY, 6730.0), (EXPIRY, 6600.0)]),
        chains=FakeChains(_chain(NEXT_EXPIRY)),
    )
    assert (result.settled, result.opened) == (1, 1)
    state = _state(config)
    assert state["balance"] == 24610.0     # -390 on 1 contract
    assert state["step"] == 1
    new = [p for p in state["positions"] if p["status"] == "open"][0]
    assert new["qty"] == 2                 # ladder doubled


def test_defined_risk_cannot_bust_only_shrink(tmp_path, monkeypatch):
    # The balance cap keeps risk <= balance, so a settlement can never
    # take the account below zero: instead it becomes too small to
    # trade and the trader goes quiet.
    _patch(monkeypatch)
    config = _config(tmp_path, condor_start_balance=500.0)
    run_condor(config, today=MONDAY,
               history=FakeHistory([(MONDAY, 6730.0)]),
               chains=FakeChains(_chain(EXPIRY)))
    result = run_condor(
        config, today=EXPIRY + dt.timedelta(days=1),
        history=FakeHistory([(MONDAY, 6730.0), (EXPIRY, 6000.0)]),
        chains=FakeChains(_chain(NEXT_EXPIRY)),
    )
    assert result.settled == 1 and result.opened == 0
    state = _state(config)
    assert state["balance"] == 110.0       # 500 - 390, above zero
    assert state["busted"] is False
    assert state["step"] == 1
    later = run_condor(
        config, today=EXPIRY + dt.timedelta(days=2),
        history=FakeHistory([(EXPIRY, 6000.0)]),
        chains=FakeChains(_chain(NEXT_EXPIRY)),
    )
    assert later.skipped is True           # qty 0: too small to trade
    assert len(RecordingNotifier.sent) == 2


def test_dry_run_saves_nothing(tmp_path, monkeypatch):
    _patch(monkeypatch)
    config = _config(tmp_path, dry_run=True)
    result = run_condor(config, today=MONDAY,
                        history=FakeHistory([(MONDAY, 6730.0)]),
                        chains=FakeChains(_chain(EXPIRY)))
    assert result.opened == 1 and result.emails_sent == 0
    assert not os.path.exists(config.condor_state_file)
    assert RecordingNotifier.sent == []


def test_no_expiry_in_window_skips(tmp_path, monkeypatch):
    _patch(monkeypatch)
    config = _config(tmp_path)
    far = dt.date(2026, 4, 20)
    result = run_condor(config, today=MONDAY,
                        history=FakeHistory([(MONDAY, 6730.0)]),
                        chains=FakeChains(_chain(far)))
    assert result.skipped is True


def test_missing_schwab_credentials_abort(tmp_path, monkeypatch):
    from earnings_notifier.config import ConfigError

    _patch(monkeypatch)
    config = _config(tmp_path)
    with pytest.raises(ConfigError, match="SCHWAB_APP_KEY"):
        run_condor(config, today=MONDAY)
