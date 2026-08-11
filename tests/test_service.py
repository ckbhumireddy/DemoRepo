import datetime as dt

from earnings_notifier.config import Config
from earnings_notifier.earnings import EarningsEvent
from earnings_notifier.service import run
from earnings_notifier.state import event_key, save_state

TODAY = dt.date(2026, 8, 4)


class _StubProvider:
    def __init__(self, events):
        self._events = {e.ticker: e for e in events}

    def next_earnings_date(self, ticker):
        return self._events.get(ticker)


def _upcoming(ticker="AAPL", days_out=3):
    return EarningsEvent(ticker, TODAY + dt.timedelta(days=days_out))


def test_dry_run_does_not_suppress_already_notified(tmp_path):
    ev = _upcoming()
    state_file = str(tmp_path / "notified.json")
    save_state(state_file, {event_key(ev)}, TODAY)

    cfg = Config(dry_run=True, state_file=state_file)
    result = run(cfg, today=TODAY, provider=_StubProvider([ev]), tickers=["AAPL"])

    # Already notified, but a dry-run preview still reports it.
    assert [e.ticker for e in result.new_events] == ["AAPL"]


def test_real_run_emails_whole_window_until_earnings_day(tmp_path, monkeypatch):
    """An already-notified company stays in the daily email, without the
    NEW badge, until its earnings date passes."""
    ev = _upcoming()
    state_file = str(tmp_path / "notified.json")
    save_state(state_file, {event_key(ev)}, TODAY)  # AAPL already notified

    sent = {}

    class _Recorder:
        def __init__(self, config):
            pass

        def send(self, subject, text, html):
            sent["subject"], sent["text"], sent["html"] = subject, text, html

    monkeypatch.setattr("earnings_notifier.service.EmailNotifier", _Recorder)

    cfg = Config(
        smtp_host="smtp.example.com",
        email_from="notifier@example.com",
        email_to=["someone@example.com"],
        state_file=state_file,
    )
    result = run(cfg, today=TODAY, provider=_StubProvider([ev]), tickers=["AAPL"])

    assert result.new_events == []       # not new anymore...
    assert result.notified is True       # ...but the email still goes out
    assert "AAPL" in sent["text"]
    assert "[NEW]" not in sent["text"]   # and carries no NEW badge


def test_past_earnings_leave_the_daily_email(tmp_path, monkeypatch):
    """Once the earnings date passes, the company drops out of the window."""
    monkeypatch.setattr("earnings_notifier.service.EmailNotifier", lambda cfg: None)
    ev = _upcoming(days_out=-1)  # reported yesterday
    cfg = Config(dry_run=True, state_file=str(tmp_path / "s.json"))
    result = run(cfg, today=TODAY, provider=_StubProvider([ev]), tickers=["AAPL"])
    assert result.in_window == []
    assert result.notified is False
