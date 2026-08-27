import datetime as dt
import json

import pytest

from earnings_analyzer.models import PriceBar
from market_insights.config import MarketInsightsConfig
from market_insights.emails import render_insights_email
from market_insights.insights import BEARISH, BULLISH, NEUTRAL, classify, intensity
from market_insights.scanner import (
    InsightRow,
    analyze,
    annotate,
    batched,
    prefilter,
    select_unusual,
    sweep,
)
from market_insights.service import run_insights
from market_insights.tradestation import Quote
from market_insights.trend import DOWN, FLAT, UP, TrendView
from market_insights.volume import VolumeStats

TODAY = dt.date(2026, 8, 21)


def _stats(rvol=3.0, price=100.0, partial=False, fraction=1.0):
    return VolumeStats(
        ticker="TEST", volume=3_000_000, projected=3_000_000,
        baseline=3_000_000 / rvol, rvol=rvol, zscore=4.2,
        dollar_volume=3_000_000 * price, fraction=fraction,
        partial=partial, sample_size=20,
    )


def _trend(short=UP, long=UP, position=0.5):
    return TrendView(
        price=100.0, short_term=short, long_term=long, short_score=2,
        long_score=2, ret_5d=0.03, ret_21d=0.06, ret_126d=0.2,
        ma20=98.0, ma50=95.0, ma200=90.0, pct_of_52wk_range=position,
    )


def _row(ticker="TEST", rvol=3.0, price=100.0, change=0.04, short=UP, long=UP,
         held=False):
    stats = _stats(rvol=rvol, price=price)
    trend = _trend(short, long)
    return InsightRow(
        ticker=ticker, stats=stats, insight=classify(stats, trend, change),
        trend=trend, price=price, change_pct=change, held=held,
    )


def _bars(volumes, closes=None, start=dt.date(2025, 8, 1)):
    closes = closes or [100.0] * len(volumes)
    return [
        PriceBar(day=start + dt.timedelta(days=i), open=c, high=c * 1.01,
                 low=c * 0.99, close=c, volume=v)
        for i, (v, c) in enumerate(zip(volumes, closes))
    ]


# --------------------------------------------------------------------------- #
# Classification
# --------------------------------------------------------------------------- #
def test_intensity_tiers():
    assert intensity(6.0) == "extreme"
    assert intensity(3.5) == "heavy"
    assert intensity(2.1) == "elevated"


def test_the_same_volume_reads_opposite_ways_by_direction():
    # This is the whole point of the package: 4x volume is not a signal
    # until you know which way the stock moved and against what trend.
    stats = _stats(rvol=4.0)
    up = classify(stats, _trend(UP, UP), 0.05)
    down = classify(stats, _trend(UP, UP), -0.05)
    assert up.label == "Trend continuation" and up.direction == BULLISH
    assert down.label == "Distribution into strength" and down.direction == BEARISH


def test_buying_against_a_downtrend_is_not_called_bullish():
    insight = classify(_stats(), _trend(UP, DOWN), 0.06)
    assert insight.label == "Counter-trend rally"
    assert insight.direction == NEUTRAL       # a bounce is not a trend


def test_selling_into_an_intact_uptrend_separates_from_acceleration():
    pressure = classify(_stats(), _trend(DOWN, UP), -0.04)
    accelerating = classify(_stats(), _trend(DOWN, DOWN), -0.04)
    assert pressure.label == "Uptrend under pressure"
    assert accelerating.label == "Downtrend acceleration"
    assert accelerating.direction == BEARISH


def test_breakouts_and_breakdowns_come_out_of_a_flat_base():
    assert classify(_stats(), _trend(UP, FLAT), 0.05).label == "Breakout attempt"
    assert classify(_stats(), _trend(DOWN, FLAT), -0.05).label == "Breakdown attempt"


def test_volume_without_a_move_is_called_out_as_directionless():
    insight = classify(_stats(rvol=4.0), _trend(), 0.004)
    assert insight.label == "Volume without direction"
    assert insight.direction == NEUTRAL
    assert "4.0x normal volume" in insight.note
    # A missing day change is treated the same way, not guessed at.
    assert classify(_stats(), _trend(), None).label == "Volume without direction"


def test_classification_survives_a_missing_trend():
    insight = classify(_stats(), None, 0.05)
    assert insight.label == "Unusual volume"
    assert "no trend history" in insight.note


def test_52_week_extremes_are_named_in_the_note():
    high = classify(_stats(), _trend(position=0.99), 0.05)
    low = classify(_stats(), _trend(DOWN, DOWN, position=0.01), -0.05)
    middle = classify(_stats(), _trend(position=0.5), 0.05)
    assert "52-week highs" in high.note
    assert "52-week lows" in low.note
    assert "52-week" not in middle.note


# --------------------------------------------------------------------------- #
# Prefilter
# --------------------------------------------------------------------------- #
def test_prefilter_keeps_hot_names_and_drops_quiet_ones():
    quotes = {
        "HOT": Quote("HOT", last=50.0, volume=3_000_000, previous_volume=1_000_000),
        "CALM": Quote("CALM", last=50.0, volume=900_000, previous_volume=1_000_000),
    }
    assert prefilter(quotes, 1.0, 1.5) == ["HOT"]


def test_prefilter_prorates_a_live_session():
    # 700k shares by the 28%-elapsed mark projects to 2.5M — hot against a
    # 1M prior session, even though the raw count is below it.
    quotes = {"X": Quote("X", last=50.0, volume=700_000, previous_volume=1_000_000)}
    assert prefilter(quotes, 0.28, 1.5) == ["X"]
    assert prefilter(quotes, 1.0, 1.5) == []


def test_prefilter_always_includes_held_names_regardless_of_volume():
    quotes = {
        "OWNED": Quote("OWNED", last=2.0, volume=1_000, previous_volume=10_000_000),
        "QUIET": Quote("QUIET", last=50.0, volume=1_000, previous_volume=10_000_000),
    }
    assert prefilter(quotes, 1.0, 1.5, always={"OWNED"}, min_price=5.0) == ["OWNED"]


def test_prefilter_keeps_names_with_no_prior_volume_to_compare():
    quotes = {"NEW": Quote("NEW", last=50.0, volume=5_000_000, previous_volume=None)}
    assert prefilter(quotes, 1.0, 1.5) == ["NEW"]


def test_prefilter_screens_out_penny_stocks_before_spending_a_request():
    quotes = {"CHEAP": Quote("CHEAP", last=1.5, volume=9_000_000,
                             previous_volume=100_000)}
    assert prefilter(quotes, 1.0, 1.5, min_price=5.0) == []


def test_prefilter_caps_the_bar_pass_and_keeps_the_hottest():
    quotes = {
        f"T{i}": Quote(f"T{i}", last=50.0, volume=1_000_000 * i,
                       previous_volume=100_000)
        for i in range(1, 11)
    }
    picked = prefilter(quotes, 1.0, 1.5, limit=3)
    assert picked == ["T10", "T9", "T8"]


def test_the_cap_never_evicts_a_held_name():
    quotes = {
        f"T{i}": Quote(f"T{i}", last=50.0, volume=1_000_000 * i,
                       previous_volume=100_000)
        for i in range(1, 11)
    }
    quotes["OWNED"] = Quote("OWNED", last=50.0, volume=1_000, previous_volume=9e9)
    picked = prefilter(quotes, 1.0, 1.5, always={"OWNED"}, limit=3)
    assert picked[0] == "OWNED" and len(picked) == 3


def test_batched_splits_on_the_quote_endpoint_limit():
    assert batched(list(range(250))) == [
        list(range(100)), list(range(100, 200)), list(range(200, 250))
    ]
    assert batched([]) == []


# --------------------------------------------------------------------------- #
# Analysis
# --------------------------------------------------------------------------- #
def test_analyze_prefers_the_live_quote_over_the_forming_bar():
    bars = _bars([1_000_000] * 30)
    quote = Quote("X", last=120.0, volume=4_000_000, previous_close=100.0)
    row = analyze("X", bars, partial=True, fraction=0.5, quote=quote)
    # Quote volume (4M), projected over a half session, against a 1M median.
    assert row.rvol == pytest.approx(8.0)
    assert row.price == 120.0
    assert row.change_pct == pytest.approx(0.20)


def test_analyze_computes_the_day_move_from_history_without_a_quote():
    bars = _bars([1_000_000] * 30, closes=[100.0] * 29 + [106.0])
    row = analyze("X", bars, partial=False, fraction=1.0)
    assert row.change_pct == pytest.approx(0.06)
    assert row.stats.partial is False


def test_analyze_returns_none_when_there_is_nothing_to_measure():
    assert analyze("X", [], partial=False, fraction=1.0) is None
    assert analyze("X", _bars([0] * 5), partial=False, fraction=1.0) is None


def test_analyze_lets_todays_move_reach_the_short_term_trend():
    # 200 flat sessions then a live +12% day: the short horizon must see the
    # move, which only happens because the live price is appended to the
    # bars handed to the trend.
    bars = _bars([1_000_000] * 200)
    quote = Quote("X", last=112.0, volume=5_000_000, previous_close=100.0)
    row = analyze("X", bars, partial=True, fraction=1.0, quote=quote)
    assert row.trend.short_term == UP
    assert row.insight.label in {"Breakout attempt", "Trend continuation"}


# --------------------------------------------------------------------------- #
# Selection
# --------------------------------------------------------------------------- #
def test_select_unusual_ranks_by_relative_volume():
    rows = [_row("A", rvol=2.5), _row("B", rvol=9.0), _row("C", rvol=4.0)]
    picked = select_unusual(rows, min_rvol=2.0, min_dollar_volume=0,
                            min_price=0, top_n=10)
    assert [r.ticker for r in picked] == ["B", "C", "A"]


def test_select_unusual_applies_the_threshold_and_the_cap():
    rows = [_row("A", rvol=2.5), _row("B", rvol=9.0), _row("C", rvol=1.2)]
    picked = select_unusual(rows, min_rvol=2.0, min_dollar_volume=0,
                            min_price=0, top_n=1)
    assert [r.ticker for r in picked] == ["B"]


def test_liquidity_floors_keep_thin_tape_out_of_the_email():
    # A $2 stock at 8x its tiny usual volume is not a market insight, and
    # without the floors it outranks everything real.
    junk = _row("JUNK", rvol=8.0, price=2.0)
    junk.stats.dollar_volume = 200_000
    real = _row("REAL", rvol=3.0, price=100.0)
    picked = select_unusual([junk, real], min_rvol=2.0,
                            min_dollar_volume=25e6, min_price=5.0, top_n=10)
    assert [r.ticker for r in picked] == ["REAL"]


def test_annotate_marks_portfolio_holdings():
    rows = [_row("A"), _row("B")]
    annotate(rows, {"B"})
    assert [r.held for r in rows] == [False, True]


def test_sweep_survives_individual_failures():
    def fetch(ticker):
        if ticker == "BOOM":
            raise RuntimeError("api down")
        if ticker == "NONE":
            return None
        return _row(ticker)

    rows, failures = sweep(["A", "BOOM", "NONE", "B"], fetch, max_workers=2)
    assert sorted(r.ticker for r in rows) == ["A", "B"]
    assert failures == 2


# --------------------------------------------------------------------------- #
# Email
# --------------------------------------------------------------------------- #
def test_email_leads_with_the_count_and_flags_holdings():
    rows = [_row("AAA", rvol=6.0, held=True), _row("BBB", rvol=3.0)]
    subject, text, html_body = render_insights_email(rows, TODAY)
    assert "2 unusual" in subject and "1 in your portfolio" in subject
    assert "AAA" in text and "6.0x" in text
    assert "HELD" in html_body
    assert html_body.count("<div") == html_body.count("</div>")


def test_email_says_when_the_numbers_are_a_live_projection():
    live = _row("AAA")
    live.stats.partial = True
    live.stats.fraction = 0.28
    _, text, html_body = render_insights_email([live], TODAY)
    assert "28% elapsed" in text and "projected" in text
    assert "projected" in html_body


def test_email_handles_an_empty_scan():
    subject, text, html_body = render_insights_email([], TODAY)
    assert "Nothing unusual" in subject
    assert "No name cleared" in text and "No name cleared" in html_body


def test_email_escapes_untrusted_text():
    row = _row("<script>")
    _, _, html_body = render_insights_email([row], TODAY)
    assert "<script>" not in html_body
    assert "&lt;script&gt;" in html_body


def test_email_carries_the_degraded_note():
    _, text, html_body = render_insights_email(
        [_row("AAA")], TODAY, scan_note="feed unavailable"
    )
    assert "feed unavailable" in text and "feed unavailable" in html_body


# --------------------------------------------------------------------------- #
# Service
# --------------------------------------------------------------------------- #
def _config(**overrides):
    config = MarketInsightsConfig(
        smtp_host="smtp.test", email_from="a@b.c", email_to=["d@e.f"],
        dry_run=True, insights_state_file="",
    )
    for key, value in overrides.items():
        setattr(config, key, value)
    return config


def test_run_reports_what_it_found():
    rows = {"AAA": _row("AAA", rvol=6.0), "BBB": _row("BBB", rvol=1.1)}
    result = run_insights(
        _config(), today=TODAY, tickers=["AAA", "BBB"],
        fetch=lambda t: rows[t], held=set(), fraction=1.0,
    )
    assert result.analyzed == 2
    assert result.unusual == 1          # BBB is below the 2.0x threshold
    assert result.emails_sent == 0      # dry run
    assert "1 unusual" in result.subject


def test_run_stays_quiet_when_nothing_is_unusual():
    result = run_insights(
        _config(), today=TODAY, tickers=["AAA"],
        fetch=lambda t: _row("AAA", rvol=1.0), held=set(), fraction=1.0,
    )
    assert result.unusual == 0
    assert "Nothing unusual" in result.subject


def test_send_empty_opts_into_the_quiet_day_email(monkeypatch):
    sent = []
    monkeypatch.setattr(
        "market_insights.service.EmailNotifier",
        lambda config: type("N", (), {"send": lambda self, *a: sent.append(a)})(),
    )
    config = _config(dry_run=False, send_empty=True)
    result = run_insights(
        config, today=TODAY, tickers=["AAA"],
        fetch=lambda t: _row("AAA", rvol=1.0), held=set(), fraction=1.0,
    )
    assert result.emails_sent == 1 and len(sent) == 1


def test_holdings_are_flagged_through_the_run():
    result = run_insights(
        _config(), today=TODAY, tickers=["AAA"],
        fetch=lambda t: _row("AAA", rvol=6.0), held={"AAA"}, fraction=1.0,
    )
    assert "in your portfolio" in result.subject


def test_the_daily_marker_stops_a_second_send(tmp_path):
    marker = tmp_path / "insights.json"
    marker.write_text(json.dumps({"last_sent": TODAY.isoformat()}), encoding="utf-8")
    result = run_insights(
        _config(insights_state_file=str(marker)), today=TODAY, tickers=["AAA"],
        fetch=lambda t: _row("AAA", rvol=6.0), held=set(), fraction=1.0,
    )
    assert result.emails_sent == 0 and result.analyzed == 0


def test_a_stale_marker_does_not_block_today(tmp_path):
    marker = tmp_path / "insights.json"
    marker.write_text(json.dumps({"last_sent": "2026-08-20"}), encoding="utf-8")
    result = run_insights(
        _config(insights_state_file=str(marker)), today=TODAY, tickers=["AAA"],
        fetch=lambda t: _row("AAA", rvol=6.0), held=set(), fraction=1.0,
    )
    assert result.unusual == 1


def _quiet_body(monkeypatch, quotes, sent):
    """Run a scan that finds nothing, returning the email text."""
    monkeypatch.setattr("market_insights.service.build_session",
                        lambda config: object())
    monkeypatch.setattr("market_insights.service.fetch_quotes",
                        lambda session, tickers, gate: quotes)
    monkeypatch.setattr("market_insights.service.make_tradestation_fetch",
                        lambda *a: (lambda t: None))
    monkeypatch.setattr(
        "market_insights.service.EmailNotifier",
        lambda config: type("N", (), {
            "send": lambda self, subject, text, html: sent.update(text=text)
        })(),
    )
    config = _config(dry_run=False, send_empty=True, insights_universe="portfolio")
    return run_insights(config, today=TODAY, held={"AAA"}, fraction=1.0)


def test_a_dead_quote_feed_is_not_reported_as_a_quiet_market(monkeypatch):
    # An empty quote pass and a genuinely calm session both produce zero
    # rows; only one of them means "nothing happened", and the inbox cannot
    # tell them apart unless the email says so.
    sent = {}
    result = _quiet_body(monkeypatch, {}, sent)
    assert result.unusual == 0
    assert "feed looks down" in sent["text"]
    assert "not a quiet market" in sent["text"]


def test_a_genuinely_quiet_market_carries_no_failure_note(monkeypatch):
    sent = {}
    quotes = {"AAA": Quote("AAA", last=50.0, volume=1_000, previous_volume=1_000_000)}
    result = _quiet_body(monkeypatch, quotes, sent)
    assert result.unusual == 0
    assert "feed looks down" not in sent["text"]
