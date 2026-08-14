import datetime as dt

from earnings_trader.candidates import find_candidates, next_trading_day

FRIDAY = dt.date(2026, 8, 14)
MONDAY = dt.date(2026, 8, 17)
TUESDAY = dt.date(2026, 8, 18)


def _window(events, today=MONDAY):
    return {"today": today, "lead_days": 7, "events": events}


def _event(ticker, date, timing):
    return {"ticker": ticker, "date": date, "is_estimate": True, "timing": timing}


def test_next_trading_day_skips_weekend():
    assert next_trading_day(FRIDAY) == MONDAY
    assert next_trading_day(MONDAY) == TUESDAY


def test_today_amc_and_tomorrow_bmo_selected():
    window = _window([
        _event("TONIGHT", MONDAY, "after-market"),
        _event("TMRW", TUESDAY, "pre-market"),
    ])
    candidates, skipped = find_candidates(window, MONDAY)
    assert [(c.ticker, c.session) for c in candidates] == [
        ("TONIGHT", "today-amc"), ("TMRW", "tomorrow-bmo"),
    ]
    assert skipped == []


def test_friday_selects_monday_bmo():
    window = _window([_event("MON", MONDAY, "pre-market")], today=FRIDAY)
    candidates, _ = find_candidates(window, FRIDAY)
    assert [(c.ticker, c.session) for c in candidates] == [("MON", "tomorrow-bmo")]


def test_already_reported_and_out_of_scope_ignored():
    window = _window([
        _event("REPORTED", MONDAY, "pre-market"),     # reported this morning
        _event("TMRWNIGHT", TUESDAY, "after-market"),  # tomorrow's entry run
        _event("FARO", MONDAY + dt.timedelta(days=5), "after-market"),
    ])
    candidates, skipped = find_candidates(window, MONDAY)
    assert candidates == [] and skipped == []


def test_unknown_timing_skipped_with_reason():
    window = _window([_event("MYSTERY", MONDAY, None)])
    candidates, skipped = find_candidates(window, MONDAY)
    assert candidates == []
    assert skipped[0].ticker == "MYSTERY"
    assert skipped[0].reason == "unknown report time"


def test_missing_window_is_empty():
    assert find_candidates(None, MONDAY) == ([], [])
