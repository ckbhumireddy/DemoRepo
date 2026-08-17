from portfolio_insights.quotes import Quote, fetch_quotes, quotes_from_payload


def test_quotes_from_payload():
    payload = {
        "TGT": {"quote": {"lastPrice": 156.0, "closePrice": 150.0,
                          "netPercentChange": 4.0}},
        "BRK/B": {"quote": {"lastPrice": 500.0, "closePrice": 495.0}},
        "BAD": {"quote": {"lastPrice": 0.0}},
        "NONE": {},
    }
    quotes = quotes_from_payload(payload)
    assert quotes["TGT"].day_change_pct == 4.0
    assert quotes["BRK-B"].ticker == "BRK-B"          # slash -> dash
    assert round(quotes["BRK-B"].day_change_pct, 4) == round(5 / 495 * 100, 4)
    assert "BAD" not in quotes and "NONE" not in quotes


class _Session:
    def __init__(self, payload=None, fail=False):
        self.payload = payload or {}
        self.fail = fail
        self.symbols = None

    def get(self, path, params):
        if self.fail:
            raise RuntimeError("down")
        self.symbols = params["symbols"]
        return self.payload


def test_fetch_batches_and_includes_spy():
    session = _Session({
        "TGT": {"quote": {"lastPrice": 156.0, "closePrice": 150.0}},
        "SPY": {"quote": {"lastPrice": 650.0, "closePrice": 648.0}},
    })
    quotes = fetch_quotes(session, ["TGT"], fallback=lambda t: None)
    assert "SPY" in session.symbols
    assert quotes["SPY"].last == 650.0


def test_missing_symbols_use_fallback():
    session = _Session({
        "TGT": {"quote": {"lastPrice": 156.0, "closePrice": 150.0}},
    })
    calls = []

    def fallback(ticker):
        calls.append(ticker)
        return Quote(ticker=ticker, last=1.0)

    quotes = fetch_quotes(session, ["TGT", "MISSING"], fallback=fallback)
    assert calls == ["MISSING", "SPY"]
    assert quotes["MISSING"].last == 1.0


def test_schwab_failure_all_fallback():
    quotes = fetch_quotes(
        _Session(fail=True), ["A"],
        fallback=lambda t: Quote(ticker=t, last=2.0),
    )
    assert quotes["A"].last == 2.0 and "SPY" in quotes


def test_no_session_all_fallback():
    quotes = fetch_quotes(
        None, ["A"], fallback=lambda t: Quote(ticker=t, last=3.0)
    )
    assert quotes["A"].last == 3.0
