import datetime as dt
import json
import os

import portfolio_insights.service as service_mod
from earnings_analyzer.provider import InMemoryProvider
from earnings_notifier.earnings import EarningsEvent
from portfolio_insights.config import InsightsConfig
from portfolio_insights.portfolio import PortfolioSnapshot, Position
from portfolio_insights.quotes import Quote
from portfolio_insights.service import run_insights

TODAY = dt.date(2026, 8, 17)


class RecordingNotifier:
    sent = []

    def __init__(self, config):
        self.config = config

    def send(self, subject, text, html):
        if not self.config.dry_run:
            RecordingNotifier.sent.append(subject)


class _Earnings:
    def next_earnings_date(self, ticker):
        return EarningsEvent(ticker, TODAY + dt.timedelta(days=3),
                             timing="after-market")


def _snapshot(day_pct=4.0):
    return PortfolioSnapshot(positions=[Position("TGT", 100, 100.0)])


def _quotes_fn(day_pct=4.0):
    prev = 150.0
    last = prev * (1 + day_pct / 100.0)

    def fn(tickers):
        return {
            "TGT": Quote("TGT", last, prev, day_pct),
            "SPY": Quote("SPY", 650.0, 648.7, 0.2),
        }
    return fn


def _config(tmp_path, dry_run=False, **kw):
    kw.setdefault("insights_state_file", str(tmp_path / "insights.json"))
    return InsightsConfig(
        dry_run=dry_run,
        smtp_host="smtp.example.com",
        email_from="a@example.com",
        email_to=["a@example.com"],
        portfolio_json='{"positions": [{"ticker": "TGT", "quantity": 1}]}',
        **kw,
    )


def _patch(monkeypatch):
    RecordingNotifier.sent = []
    monkeypatch.setattr(service_mod, "EmailNotifier", RecordingNotifier)


def _run(cfg, mode, day_pct=4.0, snapshot=None):
    return run_insights(
        cfg, mode, today=TODAY,
        snapshot=snapshot if snapshot is not None else _snapshot(),
        quotes_fn=_quotes_fn(day_pct),
        provider=InMemoryProvider(),
        earnings_provider=_Earnings(),
    )


def test_eod_sends_once_per_day(tmp_path, monkeypatch):
    _patch(monkeypatch)
    cfg = _config(tmp_path)
    first = _run(cfg, "eod")
    second = _run(cfg, "eod")
    assert first.emails_sent == 1
    assert second.emails_sent == 0
    assert len(RecordingNotifier.sent) == 1
    assert RecordingNotifier.sent[0].startswith("[Portfolio] EOD")


def test_eod_includes_earnings_insight(tmp_path, monkeypatch):
    _patch(monkeypatch)
    result = _run(_config(tmp_path), "eod")
    assert result.insights >= 1   # earnings-ahead from _Earnings


def test_midday_quiet_day_sends_nothing(tmp_path, monkeypatch):
    _patch(monkeypatch)
    result = _run(_config(tmp_path), "midday", day_pct=1.0)
    assert result.alerts == 0 and result.emails_sent == 0
    assert RecordingNotifier.sent == []


def test_midday_trigger_sends_then_dedupes(tmp_path, monkeypatch):
    _patch(monkeypatch)
    cfg = _config(tmp_path)
    first = _run(cfg, "midday", day_pct=6.0)
    second = _run(cfg, "midday", day_pct=6.0)
    assert first.alerts >= 1 and first.emails_sent == 1
    assert second.alerts == 0 and second.emails_sent == 0
    assert RecordingNotifier.sent[0].startswith("[Portfolio] Midday alert")


def test_dry_run_never_writes_or_sends(tmp_path, monkeypatch):
    _patch(monkeypatch)
    cfg = _config(tmp_path, dry_run=True)
    for mode, pct in (("eod", 4.0), ("midday", 6.0)):
        result = _run(cfg, mode, day_pct=pct)
        assert result.emails_sent == 0
    assert not os.path.exists(str(tmp_path / "insights.json"))
    assert RecordingNotifier.sent == []


def test_fetch_error_degraded_eod_and_silent_midday(tmp_path, monkeypatch):
    _patch(monkeypatch)
    broken = PortfolioSnapshot(fetch_error="portfolio JSON is invalid")
    cfg = _config(tmp_path)
    midday = _run(cfg, "midday", snapshot=broken)
    assert midday.emails_sent == 0
    eod = _run(cfg, "eod", snapshot=broken)
    assert eod.emails_sent == 1
    assert "could not load the portfolio" in RecordingNotifier.sent[0]


def test_state_file_created_and_alerts_recorded(tmp_path, monkeypatch):
    _patch(monkeypatch)
    cfg = _config(tmp_path)
    _run(cfg, "midday", day_pct=6.0)
    state = json.loads((tmp_path / "insights.json").read_text())
    assert any(a["ticker"] == "TGT" for a in state["alerts_sent"])


def test_empty_state_file_disables_dedupe(tmp_path, monkeypatch):
    _patch(monkeypatch)
    cfg = _config(tmp_path, insights_state_file="")
    assert _run(cfg, "midday", day_pct=6.0).emails_sent == 1
    assert _run(cfg, "midday", day_pct=6.0).emails_sent == 1
    assert len(RecordingNotifier.sent) == 2


def test_options_excluded_by_default(tmp_path, monkeypatch):
    """Options tracking off: no option alerts, EOD still sends with a note."""
    _patch(monkeypatch)
    from portfolio_insights.portfolio import OptionPosition

    snap = _snapshot()
    snap.options = [
        OptionPosition("SNDK", dt.date(2027, 9, 17), "call", 1500.0, 1, 695.91)
    ]
    result = _run(_config(tmp_path), "midday", day_pct=1.0, snapshot=snap)
    assert result.alerts == 0
    assert result.emails_sent == 0

    eod = _run(_config(tmp_path), "eod", snapshot=snap)
    assert eod.emails_sent == 1
