import datetime as dt

from earnings_notifier.config import Config
from earnings_notifier.earnings import EarningsEvent
from earnings_notifier.service import run
from earnings_notifier.state import event_key, save_state
from earnings_notifier.window import read_window_file, write_window_file

TODAY = dt.date(2026, 8, 4)


def test_round_trip(tmp_path):
    path = str(tmp_path / "out" / "window.json")
    events = [
        EarningsEvent("AAPL", dt.date(2026, 8, 11), True, timing="after-market"),
        EarningsEvent("NVDA", dt.date(2026, 8, 10), False),
    ]
    write_window_file(path, events, TODAY, 7)
    data = read_window_file(path)
    assert data["today"] == TODAY
    assert data["lead_days"] == 7
    assert data["events"] == [
        {
            "ticker": "AAPL",
            "date": dt.date(2026, 8, 11),
            "is_estimate": True,
            "timing": "after-market",
        },
        {
            "ticker": "NVDA",
            "date": dt.date(2026, 8, 10),
            "is_estimate": False,
            "timing": None,
        },
    ]


def test_read_tolerates_missing_timing(tmp_path):
    """Window files written before the timing field must still load."""
    path = str(tmp_path / "window.json")
    write_window_file(
        path, [EarningsEvent("AAPL", dt.date(2026, 8, 11), True)], TODAY, 7
    )
    import json

    with open(path, "r", encoding="utf-8") as fh:
        raw = json.load(fh)
    for e in raw["events"]:
        del e["timing"]
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(raw, fh)
    data = read_window_file(path)
    assert data["events"][0]["timing"] is None


def test_missing_file_returns_none(tmp_path):
    assert read_window_file(str(tmp_path / "nope.json")) is None
    assert read_window_file("") is None


def test_malformed_file_returns_none(tmp_path):
    path = tmp_path / "window.json"
    path.write_text("{not json", encoding="utf-8")
    assert read_window_file(str(path)) is None
    path.write_text('{"version": 99, "events": []}', encoding="utf-8")
    assert read_window_file(str(path)) is None


class _StubProvider:
    def __init__(self, events):
        self._events = {e.ticker: e for e in events}

    def next_earnings_date(self, ticker):
        return self._events.get(ticker)


def test_service_writes_full_window_before_dedupe(tmp_path):
    """The window file must contain already-notified events too."""
    ev = EarningsEvent("AAPL", TODAY + dt.timedelta(days=3))
    state_file = str(tmp_path / "notified.json")
    save_state(state_file, {event_key(ev)}, TODAY)  # AAPL already notified

    window_file = str(tmp_path / "window.json")
    cfg = Config(dry_run=True, state_file=state_file, window_file=window_file)
    run(cfg, today=TODAY, provider=_StubProvider([ev]), tickers=["AAPL"])

    data = read_window_file(window_file)
    assert [e["ticker"] for e in data["events"]] == ["AAPL"]


def test_service_skips_window_file_when_unset(tmp_path):
    ev = EarningsEvent("AAPL", TODAY + dt.timedelta(days=3))
    cfg = Config(dry_run=True, state_file=str(tmp_path / "s.json"))
    run(cfg, today=TODAY, provider=_StubProvider([ev]), tickers=["AAPL"])
    assert not list(tmp_path.glob("window*"))
