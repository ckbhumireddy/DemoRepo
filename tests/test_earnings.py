import datetime as dt

from earnings_notifier.earnings import (
    EarningsEvent,
    YFinanceProvider,
    collect_upcoming,
    select_for_notification,
)

TODAY = dt.date(2026, 8, 4)


def _ev(ticker, days_out, is_estimate=True):
    return EarningsEvent(ticker, TODAY + dt.timedelta(days=days_out), is_estimate)


def test_selects_everything_within_the_week():
    # 4 days out (like NBIS) must be caught, not just an exact-7-day match.
    events = [_ev("AAPL", 7), _ev("NBIS", 4), _ev("NVDA", 8), _ev("AMZN", 30)]
    selected = select_for_notification(events, TODAY, lead_days=7)
    assert [e.ticker for e in selected] == ["NBIS", "AAPL"]  # sorted by date


def test_min_days_lower_bound():
    events = [_ev("A", 0), _ev("B", 2), _ev("C", 7)]
    selected = select_for_notification(events, TODAY, lead_days=7, min_days=2)
    assert {e.ticker for e in selected} == {"B", "C"}


def test_includes_today_by_default():
    events = [_ev("A", -1), _ev("B", 0), _ev("C", 7)]
    selected = select_for_notification(events, TODAY, lead_days=7)
    assert [e.ticker for e in selected] == ["B", "C"]  # past excluded, today kept


def test_results_sorted_by_date_then_ticker():
    events = [_ev("ZZZ", 7), _ev("AAA", 7), _ev("MMM", 6)]
    selected = select_for_notification(events, TODAY, lead_days=7)
    assert [e.ticker for e in selected] == ["MMM", "AAA", "ZZZ"]


def test_empty_when_nothing_in_window():
    events = [_ev("A", 8), _ev("B", 40)]
    assert select_for_notification(events, TODAY, lead_days=7) == []


def test_days_until():
    assert _ev("X", 7).days_until(TODAY) == 7


class _FlakyProvider:
    """Returns events for some tickers, raises for others, None for the rest."""

    def next_earnings_date(self, ticker):
        if ticker == "BOOM":
            raise RuntimeError("network blip")
        if ticker == "NONE":
            return None
        return _ev(ticker, 7)


def test_collect_upcoming_tolerates_failures():
    tickers = ["AAPL", "BOOM", "NONE", "MSFT"]
    events = collect_upcoming(tickers, _FlakyProvider(), max_workers=2)
    assert {e.ticker for e in events} == {"AAPL", "MSFT"}


def test_collect_upcoming_empty_tickers():
    assert collect_upcoming([], _FlakyProvider()) == []


def test_provider_retries_transient_failure():
    provider = YFinanceProvider(attempts=3, backoff=0)
    calls = {"n": 0}

    def fake_lookup(ticker):
        calls["n"] += 1
        if calls["n"] < 2:  # fail once (simulated rate-limit 404), then succeed
            raise RuntimeError("rate limited")
        return _ev(ticker, 7)

    provider._lookup_once = fake_lookup
    event = provider.next_earnings_date("BK")
    assert event is not None and event.ticker == "BK"
    assert calls["n"] == 2


def test_provider_gives_up_after_attempts():
    provider = YFinanceProvider(attempts=2, backoff=0)
    calls = {"n": 0}

    def always_none(ticker):
        calls["n"] += 1
        return None

    provider._lookup_once = always_none
    assert provider.next_earnings_date("PXD") is None
    assert calls["n"] == 2  # retried up to the attempt limit
