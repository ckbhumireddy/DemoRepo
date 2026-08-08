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


def test_service_run_end_to_end_dry_run():
    provider = _StaticProvider(
        {
            "AAPL": EarningsEvent("AAPL", TODAY + dt.timedelta(days=7), True),
            "MSFT": EarningsEvent("MSFT", TODAY + dt.timedelta(days=2), True),
        }
    )
    cfg = Config(dry_run=True)
    result = run(cfg, today=TODAY, provider=provider, tickers=["AAPL", "MSFT"])
    assert result.total_tickers == 2
    assert result.resolved == 2
    assert [e.ticker for e in result.selected] == ["AAPL"]
    assert result.notified is False


def test_service_appends_watchlist_tickers():
    provider = _StaticProvider(
        {
            "AAPL": EarningsEvent("AAPL", TODAY + dt.timedelta(days=7), True),
            "NBIS": EarningsEvent("NBIS", TODAY + dt.timedelta(days=7), True),
        }
    )
    cfg = Config(dry_run=True, extra_tickers=["nbis", "AAPL"])  # dup + lowercase
    result = run(cfg, today=TODAY, provider=provider, tickers=["AAPL"])
    # NBIS added once (deduped against the existing AAPL), both selected.
    assert result.total_tickers == 2
    assert {e.ticker for e in result.selected} == {"AAPL", "NBIS"}
