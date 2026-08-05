from earnings_notifier.sp500 import load_fallback, normalize_ticker, _dedupe


def test_normalize_share_class_dot_to_hyphen():
    assert normalize_ticker("BRK.B") == "BRK-B"
    assert normalize_ticker("bf.b") == "BF-B"
    assert normalize_ticker("  aapl ") == "AAPL"


def test_fallback_list_loads_and_is_large():
    tickers = load_fallback()
    # A healthy S&P 500 snapshot should have several hundred names.
    assert len(tickers) > 450
    assert "AAPL" in tickers
    assert "MSFT" in tickers
    # dots normalized
    assert "BRK-B" in tickers
    assert all("." not in t for t in tickers)


def test_fallback_skips_comments_and_blanks():
    tickers = load_fallback()
    assert all(not t.startswith("#") for t in tickers)
    assert all(t for t in tickers)


def test_dedupe_preserves_order():
    assert _dedupe(["A", "B", "A", "C", "B"]) == ["A", "B", "C"]
