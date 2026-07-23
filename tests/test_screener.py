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


def test_options_enrichment_populates_iv_and_strategies():
    provider, tickers, today = build_demo_provider()
    results = Screener(provider).screen(tickers, today=today, include_options=True)
    assert results
    for c in results:
        assert c.iv_rank is not None
        assert 0.0 <= c.iv_rank.iv_rank <= 1.0
        assert c.priced_strategies
        names = {s.strategy for s in c.priced_strategies}
        assert "Cash-secured put" in names
        assert "Long LEAPS call" in names
        # Every priced strategy has a defined (finite) max loss.
        assert all(s.max_loss is not None for s in c.priced_strategies)


def test_options_enrichment_is_opt_in():
    provider, tickers, today = build_demo_provider()
    results = Screener(provider).screen(tickers, today=today)  # no include_options
    assert results
    assert all(c.iv_rank is None for c in results)
    assert all(not c.priced_strategies for c in results)


def test_relaxing_score_threshold_admits_more():
    provider, tickers, today = build_demo_provider()
    strict = Screener(provider, ScreenConfig(min_fundamental_score=90))
    loose = Screener(provider, ScreenConfig(min_fundamental_score=0,
                                            require_still_depressed=True))
    assert len(loose.screen(tickers, today=today)) >= len(
        strict.screen(tickers, today=today)
    )
