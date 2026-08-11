import datetime as dt

from earnings_analyzer.models import (
    OptionLeg,
    PriceBar,
    PricedStrategy,
    Rating,
    TickerAnalysis,
)
from earnings_analyzer.provider import InMemoryProvider
from earnings_analyzer.scorecard import (
    grade_pending,
    load_scorecard,
    record_suggestions,
    save_scorecard,
    summarize,
)

TODAY = dt.date(2026, 8, 10)
EVENT = dt.date(2026, 8, 13)


def _analysis(ticker="RICHCO", strategy="Iron condor", breakevens=(93.8, 106.2)):
    s = PricedStrategy(
        strategy=strategy,
        outlook="neutral",
        legs=[
            OptionLeg(action="sell", option_type="put", strike=95.0,
                      expiry=EVENT, price=1.0)
        ],
        net_premium=1.2,
        breakevens=list(breakevens),
    )
    return TickerAnalysis(
        ticker=ticker, event_date=EVENT, price=100.0,
        rating=Rating(score=80.0, grade="A"), strategies=[s],
    )


def _fresh():
    return {"version": 1, "entries": []}


def test_record_and_dedupe():
    data = _fresh()
    assert record_suggestions(data, [_analysis()], TODAY) == 1
    assert record_suggestions(data, [_analysis()], TODAY + dt.timedelta(days=1)) == 0
    assert len(data["entries"]) == 1
    entry = data["entries"][0]
    assert entry["ticker"] == "RICHCO"
    assert entry["grade"] == "A"
    assert entry["outcome"] is None


def test_grade_pending_win_and_loss(tmp_path):
    data = _fresh()
    record_suggestions(data, [_analysis("WINCO"), _analysis("LOSSCO")], TODAY)

    provider = InMemoryProvider()

    def _bars(before, after):
        return [
            PriceBar(day=EVENT - dt.timedelta(days=1), open=before, high=before,
                     low=before, close=before),
            PriceBar(day=EVENT + dt.timedelta(days=1), open=after, high=after,
                     low=after, close=after),
        ]

    provider.add("WINCO", bars=_bars(100.0, 102.0))    # +2% -> inside condor
    provider.add("LOSSCO", bars=_bars(100.0, 89.0))    # -11% -> blown through

    graded_today = grade_pending(data, provider, TODAY)
    assert graded_today == 0  # event is still in the future

    later = EVENT + dt.timedelta(days=2)
    assert grade_pending(data, provider, later) == 2
    by_ticker = {e["ticker"]: e for e in data["entries"]}
    assert by_ticker["WINCO"]["outcome"] == "win"
    assert by_ticker["LOSSCO"]["outcome"] == "loss"
    assert by_ticker["LOSSCO"]["actual_move_pct"] == -11.0


def test_summarize_counts():
    data = _fresh()
    record_suggestions(
        data, [_analysis("A"), _analysis("B"), _analysis("C")], TODAY
    )
    data["entries"][0]["outcome"] = "win"
    data["entries"][0]["graded_on"] = TODAY.isoformat()
    data["entries"][0]["actual_move_pct"] = 1.0
    data["entries"][1]["outcome"] = "loss"
    data["entries"][1]["graded_on"] = TODAY.isoformat()
    data["entries"][1]["actual_move_pct"] = -9.0

    summary = summarize(data)
    assert summary.graded == 2
    assert summary.wins == 1
    assert summary.losses == 1
    assert summary.win_rate == 0.5
    assert summary.pending == 1
    assert len(summary.recent) == 2


def test_save_load_round_trip_and_prune(tmp_path):
    path = str(tmp_path / "state" / "scorecard.json")
    data = _fresh()
    record_suggestions(data, [_analysis()], TODAY)
    data["entries"].append(
        {**data["entries"][0], "ticker": "OLD",
         "event_date": (TODAY - dt.timedelta(days=400)).isoformat()}
    )
    save_scorecard(path, data, TODAY)
    loaded = load_scorecard(path)
    assert [e["ticker"] for e in loaded["entries"]] == ["RICHCO"]  # OLD pruned


def test_load_missing_or_malformed(tmp_path):
    assert load_scorecard(str(tmp_path / "nope.json"))["entries"] == []
    bad = tmp_path / "bad.json"
    bad.write_text("{broken", encoding="utf-8")
    assert load_scorecard(str(bad))["entries"] == []


def test_scorecard_rendered_in_email():
    from earnings_analyzer.formatting import render_email
    from earnings_analyzer.scorecard import ScorecardSummary

    summary = ScorecardSummary(
        graded=4, wins=3, pending=2,
        recent=[{"ticker": "WINCO", "strategy": "Iron condor",
                 "event_date": "2026-08-06", "outcome": "win",
                 "actual_move_pct": 1.2}],
    )
    _, text, html = render_email([], TODAY, scorecard=summary)
    assert "75% win rate" in text
    assert "WIN  WINCO Iron condor" in text
    assert "Scorecard" in html and "75% win rate" in html

    _, text_none, _ = render_email([], TODAY, scorecard=None)
    assert "Scorecard" not in text_none
