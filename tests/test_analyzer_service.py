import datetime as dt

from earnings_analyzer.config import AnalyzerConfig
from earnings_analyzer.demo_data import build_demo_provider
from earnings_analyzer.service import resolve_entries, run_analyzer
from earnings_notifier.earnings import EarningsEvent
from earnings_notifier.window import write_window_file

TODAY = dt.date(2026, 8, 9)


def _config(**kw):
    return AnalyzerConfig(dry_run=True, **kw)


def _demo_run(**kw):
    provider, entries = build_demo_provider(TODAY)
    return run_analyzer(
        _config(**kw), today=TODAY, provider=provider, entries=entries
    )


def test_demo_run_analyzes_all_scenarios():
    result = _demo_run()
    assert result.requested == 5
    assert result.analyzed == 5
    assert result.notified is False  # dry run


def test_results_sorted_by_rating():
    result = _demo_run()
    scores = [a.rating.score for a in result.analyses]
    assert scores == sorted(scores, reverse=True)


def test_every_strategy_branch_covered():
    result = _demo_run()
    by_ticker = {a.ticker: a for a in result.analyses}
    assert [s.strategy for s in by_ticker["RICHCO"].strategies] == ["Iron condor"]
    assert [s.strategy for s in by_ticker["BULLCO"].strategies] == ["Put credit spread"]
    assert [s.strategy for s in by_ticker["CHEAPCO"].strategies] == [
        "Long straddle", "Call calendar",
    ]
    assert [s.strategy for s in by_ticker["BEARCO"].strategies] == ["Put debit spread"]
    assert by_ticker["THINCO"].strategies == []           # illiquid -> no trade
    assert by_ticker["THINCO"].liquidity.tradeable is False


def test_subject_counts_setups():
    result = _demo_run()
    assert "5 setups" in result.subject


def test_ticker_limit_applies():
    result = _demo_run(analyzer_ticker_limit=2)
    assert result.requested == 2


def test_empty_entries_no_email():
    result = run_analyzer(_config(), today=TODAY, provider=None, entries=[])
    assert result.analyzed == 0
    assert result.notified is False
    assert "no setups" in result.subject


def test_resolve_entries_from_window_file(tmp_path):
    path = str(tmp_path / "window.json")
    write_window_file(
        path,
        [
            EarningsEvent("RICHCO", TODAY + dt.timedelta(days=3), timing="pre-market"),
            EarningsEvent("STALE", TODAY - dt.timedelta(days=1)),  # already passed
        ],
        TODAY,
        7,
    )
    entries = resolve_entries(_config(window_file=path), TODAY)
    assert entries == [("RICHCO", TODAY + dt.timedelta(days=3), "pre-market")]


def test_timing_flows_from_entries_to_analysis():
    provider, entries = build_demo_provider(TODAY)
    entries = [(t, d, "after-market") for t, d in entries]
    result = run_analyzer(
        _config(), today=TODAY, provider=provider, entries=entries
    )
    assert all(a.timing == "after-market" for a in result.analyses)


def test_failed_ticker_does_not_kill_the_run():
    provider, entries = build_demo_provider(TODAY)

    class _Exploding:
        def __getattr__(self, name):
            def boom(*args, **kwargs):
                raise RuntimeError("boom")

            return boom

    class _Mixed:
        """Real data for everyone except one ticker that always raises."""

        def __init__(self):
            self._good = provider
            self._bad = _Exploding()

        def _pick(self, ticker):
            return self._bad if ticker == "RICHCO" else self._good

        def earnings_history(self, ticker, limit=20):
            return self._pick(ticker).earnings_history(ticker, limit)

        def price_history(self, ticker, days=700):
            return self._pick(ticker).price_history(ticker, days)

        def snapshot(self, ticker):
            return self._pick(ticker).snapshot(ticker)

        def option_expiries(self, ticker):
            return self._pick(ticker).option_expiries(ticker)

        def option_chain(self, ticker, expiry):
            return self._pick(ticker).option_chain(ticker, expiry)

    result = run_analyzer(_config(), today=TODAY, provider=_Mixed(), entries=entries)
    assert result.requested == 5
    assert result.analyzed == 4
    assert "RICHCO" not in {a.ticker for a in result.analyses}
