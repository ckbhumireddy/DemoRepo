import datetime as dt

from earnings_notifier.config import Config
from earnings_notifier.earnings import EarningsEvent
from earnings_notifier.notifier import EmailNotifier
from earnings_notifier.service import run

TODAY = dt.date(2026, 8, 4)


def test_notifier_build_message_multipart():
    cfg = Config(
        smtp_host="smtp.test",
        email_from="bot@example.com",
        email_to=["a@example.com", "b@example.com"],
    )
    msg = EmailNotifier(cfg)._build_message("subj", "text body", "<b>html</b>")
    assert msg["Subject"] == "subj"
    assert msg["From"] == "bot@example.com"
    assert msg["To"] == "a@example.com, b@example.com"
    assert msg.is_multipart()


def test_dry_run_does_not_send(caplog):
    cfg = Config(dry_run=True, email_to=["x@example.com"])
    with caplog.at_level("INFO"):
        EmailNotifier(cfg).send("subj", "text", "<b>html</b>")
    assert "dry-run" in caplog.text.lower()


class _StaticProvider:
    def __init__(self, mapping):
        self._mapping = mapping

    def next_earnings_date(self, ticker):
        return self._mapping.get(ticker)


def test_service_reports_whole_week_window():
    provider = _StaticProvider(
        {
            "AAPL": EarningsEvent("AAPL", TODAY + dt.timedelta(days=7), True),
            "MSFT": EarningsEvent("MSFT", TODAY + dt.timedelta(days=2), True),
        }
    )
    cfg = Config(dry_run=True, use_state=False)
    result = run(cfg, today=TODAY, provider=provider, tickers=["AAPL", "MSFT"])
    assert result.total_tickers == 2
    # Both are within the next week (2 and 7 days out), not just the exact 7.
    assert {e.ticker for e in result.new_events} == {"AAPL", "MSFT"}
    assert result.notified is False


def test_service_appends_watchlist_tickers():
    provider = _StaticProvider(
        {
            "AAPL": EarningsEvent("AAPL", TODAY + dt.timedelta(days=7), True),
            "NBIS": EarningsEvent("NBIS", TODAY + dt.timedelta(days=4), True),
        }
    )
    cfg = Config(dry_run=True, use_state=False, extra_tickers=["nbis", "AAPL"])
    result = run(cfg, today=TODAY, provider=provider, tickers=["AAPL"])
    # NBIS added once (deduped) and, at 4 days out, is inside the week.
    assert result.total_tickers == 2
    assert {e.ticker for e in result.new_events} == {"AAPL", "NBIS"}


def test_service_emails_each_earnings_only_once(tmp_path):
    """Second run on a later day must not re-email the same earnings."""
    state_file = str(tmp_path / "notified.json")
    provider = _StaticProvider(
        {"NBIS": EarningsEvent("NBIS", dt.date(2026, 8, 12), True)}
    )
    cfg = Config(dry_run=False, use_state=True, state_file=state_file,
                 smtp_host="smtp.test", email_from="b@x.com", email_to=["m@x.com"])

    class _CountingNotifier:
        sent = 0

    import earnings_notifier.service as svc

    class _FakeNotifier:
        def __init__(self, config):
            pass

        def send(self, *a, **k):
            _CountingNotifier.sent += 1

    svc.EmailNotifier = _FakeNotifier
    try:
        # Day 1: NBIS is 4 days out -> emailed, state written.
        r1 = run(cfg, today=dt.date(2026, 8, 8), provider=provider, tickers=["NBIS"])
        # Day 2: NBIS now 3 days out, still in window, but already notified.
        r2 = run(cfg, today=dt.date(2026, 8, 9), provider=provider, tickers=["NBIS"])
    finally:
        svc.EmailNotifier = EmailNotifier

    assert [e.ticker for e in r1.new_events] == ["NBIS"]
    assert r2.new_events == []          # suppressed the second day
    assert _CountingNotifier.sent == 1  # exactly one email across both runs
