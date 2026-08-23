import datetime as dt

from earnings_analyzer.models import PriceBar
from market_insights.diagnostics import check_feed, format_check
from market_insights.tradestation import Quote


class FakeSession:
    """Stands in for a TradeStationSession without touching the network."""

    def __init__(self, quotes=None, bars=None, partial=False,
                 quote_error=None, bars_error=None):
        self._quotes = quotes if quotes is not None else {}
        self._bars = bars if bars is not None else []
        self._partial = partial
        self._quote_error = quote_error
        self._bars_error = bars_error

    def quotes(self, tickers):
        if self._quote_error:
            raise self._quote_error
        return self._quotes

    def bars(self, ticker, barsback):
        if self._bars_error:
            raise self._bars_error
        return self._bars, self._partial


def _bars(n=30, start=dt.date(2026, 7, 1)):
    return [
        PriceBar(day=start + dt.timedelta(days=i), open=100.0, high=101.0,
                 low=99.0, close=100.0 + i, volume=1_000_000 + i)
        for i in range(n)
    ]


def _healthy(**overrides):
    defaults = dict(
        quotes={"MSFT": Quote("MSFT", last=410.5, volume=22_000_000,
                              previous_volume=18_000_000, previous_close=400.0)},
        bars=_bars(),
    )
    defaults.update(overrides)
    return FakeSession(**defaults)


def test_a_healthy_feed_reports_ok_with_the_parsed_numbers():
    check = check_feed(_healthy(), "MSFT")
    assert check.ok
    assert check.quote_ok and check.bars_ok
    assert check.last == 410.5
    assert check.volume == 22_000_000
    assert check.bar_count == 30
    assert check.last_day == dt.date(2026, 7, 30)
    assert not check.errors

    report = format_check(check)
    assert "quotes      OK" in report
    assert "barcharts   OK" in report
    assert "All good" in report
    assert "22,000,000" in report          # thousands separators, not raw floats


def test_a_missing_quote_row_is_reported_not_raised():
    check = check_feed(FakeSession(quotes={"OTHER": Quote("OTHER")},
                                   bars=_bars()), "MSFT")
    assert not check.ok and not check.quote_ok
    assert check.bars_ok                    # the other call still succeeded
    assert any("no row for MSFT" in e for e in check.errors)
    assert "quotes      FAILED" in format_check(check)


def test_an_auth_failure_surfaces_as_a_problem_line():
    from market_insights.tradestation import TradeStationAuthError

    session = FakeSession(
        quote_error=TradeStationAuthError("token refresh failed (401)"),
        bars_error=TradeStationAuthError("token refresh failed (401)"),
    )
    check = check_feed(session, "MSFT")
    assert not check.ok
    report = format_check(check)
    assert "TradeStationAuthError" in report
    assert "401" in report


def test_empty_bars_are_flagged_rather_than_passing_silently():
    check = check_feed(_healthy(bars=[]), "MSFT")
    assert not check.bars_ok
    assert any("no usable bars" in e for e in check.errors)


def test_partially_parsed_bars_are_called_out():
    # The feed answered, but most rows failed to parse — the symptom of a
    # changed payload shape, and invisible without this warning.
    check = check_feed(_healthy(bars=_bars(4)), "MSFT")
    assert check.bars_ok                     # something parsed...
    assert not check.ok                      # ...but not enough
    assert any("parsed only 4" in e for e in check.errors)


def test_an_open_session_is_reported_so_projection_is_expected():
    check = check_feed(_healthy(partial=True), "MSFT")
    assert check.session_open
    assert "still open" in format_check(check)
    assert "closed (no projection needed)" in format_check(
        check_feed(_healthy(partial=False), "MSFT")
    )


def test_empty_quote_fields_are_named_individually():
    session = _healthy(quotes={"MSFT": Quote("MSFT", last=None, volume=None)})
    check = check_feed(session, "MSFT")
    assert check.quote_ok                    # the row parsed...
    assert not check.ok                      # ...but the fields were empty
    assert any("Last was empty" in e for e in check.errors)
    assert any("Volume was empty" in e for e in check.errors)
    assert "(empty)" in format_check(check)
