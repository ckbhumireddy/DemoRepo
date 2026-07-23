from stock_screener.config import ScreenConfig
from stock_screener.analysis.screener import Screener
from stock_screener.demo_data import build_demo_provider


def test_demo_screen_selects_quality_crashes_only():
    provider, tickers, today = build_demo_provider()
    screener = Screener(provider, ScreenConfig())
    results = screener.screen(tickers, today=today)
    picked = {c.ticker for c in results}

    # GOODCO and SOLIDCO: strong fundamentals + real crash -> included.
    assert "GOODCO" in picked
    assert "SOLIDCO" in picked
    # WEAKCO crashed but has bad fundamentals -> excluded.
    assert "WEAKCO" not in picked
    # STEADYCO is high quality but didn't crash -> excluded.
    assert "STEADYCO" not in picked


def test_results_ranked_by_composite_score():
    provider, tickers, today = build_demo_provider()
    results = Screener(provider).screen(tickers, today=today)
    scores = [c.composite_score for c in results]
    assert scores == sorted(scores, reverse=True)


def test_candidates_have_option_suggestions():
    provider, tickers, today = build_demo_provider()
    results = Screener(provider).screen(tickers, today=today)
    assert results
    for c in results:
        assert c.option_suggestions
        strategies = {s.strategy for s in c.option_suggestions}
        assert "Cash-secured put" in strategies
        assert "Bull put credit spread" in strategies


def test_relaxing_score_threshold_admits_more():
    provider, tickers, today = build_demo_provider()
    strict = Screener(provider, ScreenConfig(min_fundamental_score=90))
    loose = Screener(provider, ScreenConfig(min_fundamental_score=0,
                                            require_still_depressed=True))
    assert len(loose.screen(tickers, today=today)) >= len(
        strict.screen(tickers, today=today)
    )
