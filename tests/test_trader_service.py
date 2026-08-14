import datetime as dt
import os

import earnings_trader.service as service_mod
from earnings_analyzer.models import OptionChain, OptionContract, PriceBar, QuarterResult
from earnings_analyzer.provider import InMemoryProvider
from earnings_trader.config import TraderConfig
from earnings_trader.service import run_trader

TODAY = dt.date(2026, 8, 17)
EVENT = dt.date(2026, 8, 18)
EXPIRY = dt.date(2026, 8, 21)
NOW = dt.datetime(2026, 8, 17, 19, 35, tzinfo=dt.timezone.utc)


class RecordingNotifier:
    sent = []

    def __init__(self, config):
        self.config = config

    def send(self, subject, text, html):
        if not self.config.dry_run:
            RecordingNotifier.sent.append(subject)


def _window(events):
    return {"today": TODAY, "lead_days": 7, "events": events}


def _event(ticker, date=EVENT, timing="pre-market"):
    return {"ticker": ticker, "date": date, "is_estimate": True, "timing": timing}


def _provider():
    """Rich-vol setup: candidate T opens an iron condor."""
    provider = InMemoryProvider()
    quarters = [
        QuarterResult(date=TODAY - dt.timedelta(days=90 * (i + 1)),
                      eps_estimate=1.0, eps_actual=None, surprise_pct=1.0,
                      reaction_pct=r)
        for i, r in enumerate([4.0, -4.0, 4.0, -4.0])
    ]
    bars = [
        PriceBar(day=TODAY - dt.timedelta(days=i), open=100, high=100,
                 low=100, close=100.0)
        for i in range(30, 0, -1)
    ]
    provider.add("T", quarters=quarters, bars=bars)
    chain = OptionChain(ticker="T", expiry=EXPIRY, underlying_price=100.0)
    for strike in range(70, 135, 5):
        for option_type in ("call", "put"):
            intrinsic = max(
                0.0, 100 - strike if option_type == "call" else strike - 100
            )
            mid = intrinsic + max(0.25, 4.0 - 0.5 * abs(strike - 100) / 5.0)
            bucket = chain.calls if option_type == "call" else chain.puts
            bucket.append(OptionContract(
                contract_symbol=f"T-{option_type}-{strike}",
                option_type=option_type, strike=float(strike), expiry=EXPIRY,
                bid=mid - 0.05, ask=mid + 0.05, volume=800.0,
                open_interest=2000.0,
            ))
    provider.add_option_chain(chain)
    return provider


def _config(tmp_path, dry_run=False, **kw):
    return TraderConfig(
        dry_run=dry_run,
        smtp_host="smtp.example.com",
        email_from="a@example.com",
        email_to=["a@example.com"],
        trader_ledger_file=str(tmp_path / "trader.json"),
        **kw,
    )


def _patch_notifier(monkeypatch):
    RecordingNotifier.sent = []
    monkeypatch.setattr(service_mod, "EmailNotifier", RecordingNotifier)


def test_entry_opens_and_emails_each_trade(tmp_path, monkeypatch):
    _patch_notifier(monkeypatch)
    result = run_trader(
        _config(tmp_path), "entry", today=TODAY, now=NOW,
        provider=_provider(), window=_window([_event("T")]),
    )
    assert result.opened == 1
    assert result.emails_sent == 1
    assert RecordingNotifier.sent[0].startswith("[Paper] Opened T ")
    assert os.path.exists(str(tmp_path / "trader.json"))


def test_morning_closes_plans_and_emails(tmp_path, monkeypatch):
    _patch_notifier(monkeypatch)
    cfg = _config(tmp_path)
    run_trader(cfg, "entry", today=TODAY, now=NOW,
               provider=_provider(), window=_window([_event("T")]))

    # Next morning: T reported pre-market; the morning run closes it.
    RecordingNotifier.sent = []
    result = run_trader(
        cfg, "morning", today=EVENT, now=NOW + dt.timedelta(days=1),
        provider=_provider(), window=_window([]),
    )
    assert result.closed == 1
    assert result.opened == 0
    assert result.emails_sent == 2      # one close email + the plan email
    assert RecordingNotifier.sent[0].startswith("[Paper] Closed T ")
    assert RecordingNotifier.sent[1].startswith("[Paper] Daily plan")


def test_morning_never_opens(tmp_path, monkeypatch):
    _patch_notifier(monkeypatch)
    result = run_trader(
        _config(tmp_path), "morning", today=TODAY, now=NOW,
        provider=_provider(), window=_window([_event("T")]),
    )
    assert result.opened == 0
    assert result.candidates == 1
    # The plan email still previews the trade.
    assert RecordingNotifier.sent[-1].startswith("[Paper] Daily plan")
    assert "1 planned trade(s)" in RecordingNotifier.sent[-1]


def test_entry_is_safety_net_for_missed_morning_close(tmp_path, monkeypatch):
    _patch_notifier(monkeypatch)
    cfg = _config(tmp_path)
    run_trader(cfg, "entry", today=TODAY, now=NOW,
               provider=_provider(), window=_window([_event("T")]))
    RecordingNotifier.sent = []
    result = run_trader(
        cfg, "entry", today=EVENT, now=NOW + dt.timedelta(days=1),
        provider=_provider(), window=_window([]),
    )
    assert result.closed == 1
    assert RecordingNotifier.sent[0].startswith("[Paper] Closed T ")


def test_dry_run_never_writes_or_sends(tmp_path, monkeypatch):
    _patch_notifier(monkeypatch)
    for phase in ("morning", "entry"):
        result = run_trader(
            _config(tmp_path, dry_run=True), phase, today=TODAY, now=NOW,
            provider=_provider(), window=_window([_event("T")]),
        )
        assert result.emails_sent == 0
        assert result.opened == 0
    assert not os.path.exists(str(tmp_path / "trader.json"))
    assert RecordingNotifier.sent == []


def test_missing_window_still_sends_plan(tmp_path, monkeypatch):
    _patch_notifier(monkeypatch)
    result = run_trader(
        _config(tmp_path), "morning", today=TODAY, now=NOW,
        provider=InMemoryProvider(), window=None,
    )
    assert result.candidates == 0
    assert result.emails_sent == 1
    assert RecordingNotifier.sent[0].startswith("[Paper] Daily plan")


def test_empty_ledger_file_disables_trading(tmp_path, monkeypatch):
    _patch_notifier(monkeypatch)
    cfg = _config(tmp_path)
    cfg.trader_ledger_file = ""
    result = run_trader(cfg, "entry", today=TODAY, now=NOW,
                        provider=_provider(), window=_window([_event("T")]))
    assert result.opened == 0 and result.emails_sent == 0
