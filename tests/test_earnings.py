import datetime as dt

from earnings_notifier.earnings import (
    EarningsEvent,
    YFinanceProvider,
    _as_float,
    _last_reported_earnings,
    _post_earnings_move,
    collect_upcoming,
    enrich_events,
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


def test_as_float_rejects_nan_and_garbage():
    assert _as_float(1.5) == 1.5
    assert _as_float("2.5") == 2.5
    assert _as_float(None) is None
    assert _as_float(float("nan")) is None
    assert _as_float("n/a") is None
    assert _as_float(True) is None


def test_last_reported_earnings_picks_most_recent_past_row():
    import pytest

    pd = pytest.importorskip("pandas")

    df = pd.DataFrame(
        {
            "EPS Estimate": [1.50, 1.43, 1.30, float("nan")],
            "Reported EPS": [float("nan"), 1.57, 1.35, 1.20],
            "Surprise(%)": [float("nan"), 9.8, 3.8, float("nan")],
        },
        index=pd.to_datetime(
            ["2026-08-11", "2026-07-31", "2026-04-30", "2026-01-29"]
        ),
    )
    last = _last_reported_earnings(df, TODAY)
    # The 08-11 row is in the future and the most recent past row wins.
    assert last == {
        "last_earnings_date": dt.date(2026, 7, 31),
        "last_eps_estimate": 1.43,
        "last_eps_actual": 1.57,
        "last_surprise_pct": 9.8,
    }


def test_last_reported_earnings_empty_when_no_reported_eps():
    import pytest

    pd = pytest.importorskip("pandas")

    df = pd.DataFrame(
        {"EPS Estimate": [1.5], "Reported EPS": [float("nan")], "Surprise(%)": [float("nan")]},
        index=pd.to_datetime(["2026-04-30"]),
    )
    assert _last_reported_earnings(df, TODAY) == {}


def test_post_earnings_move_brackets_the_report_date():
    import pytest

    pd = pytest.importorskip("pandas")
    # Report on Fri Jul 31 (after close); the weekend gap must not matter.
    history = pd.DataFrame(
        {"Close": [100.0, 102.0, 110.5, 111.0]},
        index=pd.to_datetime(["2026-07-29", "2026-07-30", "2026-08-03", "2026-08-04"]),
    )
    move = _post_earnings_move(history, dt.date(2026, 7, 31))
    # Last close before (102.0) -> first close after (110.5) = +8.33%.
    assert move == pytest.approx(8.33, abs=0.01)


def test_post_earnings_move_none_when_not_bracketed():
    import pytest

    pd = pytest.importorskip("pandas")
    only_before = pd.DataFrame(
        {"Close": [100.0]}, index=pd.to_datetime(["2026-07-30"])
    )
    assert _post_earnings_move(only_before, dt.date(2026, 7, 31)) is None
    assert _post_earnings_move(None, dt.date(2026, 7, 31)) is None


class _EnrichingProvider(_FlakyProvider):
    def enrich(self, event):
        if event.ticker == "BOOM":
            raise RuntimeError("quote lookup failed")
        return EarningsEvent(
            event.ticker, event.date, event.is_estimate,
            company=f"{event.ticker} Corp", price=100.0,
        )


def test_enrich_events_fills_details_and_tolerates_failures():
    events = [_ev("AAPL", 7), _ev("BOOM", 6)]
    enriched = enrich_events(events, _EnrichingProvider(), max_workers=2)
    by_ticker = {e.ticker: e for e in enriched}
    assert by_ticker["AAPL"].company == "AAPL Corp"
    assert by_ticker["AAPL"].price == 100.0
    assert by_ticker["BOOM"] == events[1]  # failure leaves the event unchanged


def test_enrich_events_noop_without_enrich_method():
    events = [_ev("AAPL", 7)]
    assert enrich_events(events, _FlakyProvider()) == events
