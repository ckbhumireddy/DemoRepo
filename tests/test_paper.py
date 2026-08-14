import datetime as dt

from earnings_analyzer.models import (
    LiquidityReport,
    OptionChain,
    OptionContract,
    OptionLeg,
    PriceBar,
    PricedStrategy,
    Rating,
    TickerAnalysis,
)
from earnings_analyzer.paper import (
    PaperSummary,
    close_due_positions,
    load_paper,
    open_positions,
    save_paper,
    summarize_paper,
)
from earnings_analyzer.provider import InMemoryProvider

TODAY = dt.date(2026, 8, 10)
EVENT = dt.date(2026, 8, 13)
EXPIRY = dt.date(2026, 8, 14)


def _condor(max_loss=3.8, net_premium=1.2):
    """Iron-condor-shaped strategy: shorts 95p/105c, longs 90p/110c."""
    legs = [
        OptionLeg("sell", "put", 95.0, EXPIRY, 1.30),
        OptionLeg("buy", "put", 90.0, EXPIRY, 0.40),
        OptionLeg("sell", "call", 105.0, EXPIRY, 1.10),
        OptionLeg("buy", "call", 110.0, EXPIRY, 0.80),
    ]
    return PricedStrategy(
        strategy="Iron condor", outlook="neutral", legs=legs,
        net_premium=net_premium, max_profit=net_premium,
        max_loss=max_loss, breakevens=[93.8, 106.2],
    )


def _straddle(debit=4.0):
    legs = [
        OptionLeg("buy", "call", 100.0, EXPIRY, 2.0),
        OptionLeg("buy", "put", 100.0, EXPIRY, 2.0),
    ]
    return PricedStrategy(
        strategy="Long straddle", outlook="volatile", legs=legs,
        net_premium=-debit, max_profit=None, max_loss=debit,
        breakevens=[96.0, 104.0],
    )


def _analysis(
    ticker="RICHCO",
    strategy=None,
    grade="A",
    tradeable=True,
    event_date=EVENT,
    timing=None,
    price=100.0,
):
    return TickerAnalysis(
        ticker=ticker,
        event_date=event_date,
        timing=timing,
        price=price,
        liquidity=LiquidityReport(tradeable=tradeable, atm_open_interest=1000,
                                  atm_volume=500, atm_spread_pct=2.0),
        rating=Rating(score=80.0, grade=grade),
        strategies=[strategy or _condor()],
    )


def _fresh(balance=25000.0):
    return {
        "version": 1,
        "start_balance": 25000.0,
        "balance": balance,
        "positions": [],
    }


def _chain(marks):
    """Chain at EXPIRY with (type, strike) -> mid marks (bid == ask == mid)."""
    chain = OptionChain(ticker="RICHCO", expiry=EXPIRY, underlying_price=100.0)
    for (option_type, strike), mid in marks.items():
        bucket = chain.calls if option_type == "call" else chain.puts
        bucket.append(
            OptionContract(
                contract_symbol=f"RICHCO-{option_type}-{strike}",
                option_type=option_type, strike=strike, expiry=EXPIRY,
                bid=mid, ask=mid,
            )
        )
    return chain


# --------------------------------------------------------------------------- #
# Ledger persistence
# --------------------------------------------------------------------------- #
def test_load_missing_starts_fresh(tmp_path):
    data = load_paper(str(tmp_path / "nope.json"), 25000.0)
    assert data["balance"] == 25000.0
    assert data["positions"] == []


def test_round_trip_and_prune(tmp_path):
    path = str(tmp_path / "state" / "paper.json")
    data = _fresh()
    open_positions(data, [_analysis()], TODAY)
    old_closed = {
        **data["positions"][0],
        "ticker": "OLD",
        "status": "closed",
        "event_date": (TODAY - dt.timedelta(days=400)).isoformat(),
    }
    old_open = {**old_closed, "ticker": "OLDOPEN", "status": "open"}
    data["positions"] += [old_closed, old_open]
    save_paper(path, data, TODAY)
    loaded = load_paper(path, 0.0)
    tickers = [p["ticker"] for p in loaded["positions"]]
    assert "OLD" not in tickers          # closed + old -> pruned
    assert "OLDOPEN" in tickers          # open positions never pruned
    assert loaded["balance"] == 25000.0  # start_balance arg ignored on load


def test_load_malformed_starts_fresh(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{broken", encoding="utf-8")
    assert load_paper(str(bad), 25000.0)["positions"] == []
    bad.write_text('{"version": 99, "positions": []}', encoding="utf-8")
    assert load_paper(str(bad), 25000.0)["balance"] == 25000.0


# --------------------------------------------------------------------------- #
# Opening: sizing, filters, dedupe, capital gate
# --------------------------------------------------------------------------- #
def test_sizing_respects_max_risk():
    data = _fresh()
    assert open_positions(data, [_analysis()], TODAY) == 1
    pos = data["positions"][0]
    assert pos["qty"] == 13                    # floor(5000 / 380)
    assert pos["risk_dollars"] == 4940.0       # 13 * 380 <= 5000
    assert pos["status"] == "open"
    assert pos["legs"][0]["open_price"] == 1.30


def test_oversized_contract_skipped():
    data = _fresh()
    big = _condor(max_loss=60.0)               # one contract risks $6,000
    assert open_positions(data, [_analysis(strategy=big)], TODAY) == 0


def test_entry_filters():
    data = _fresh()
    skipped = [
        _analysis("NOLIQ", tradeable=False),
        _analysis("LOWGRADE", grade="C"),
        _analysis("REPORTED", event_date=TODAY, timing="pre-market"),
        _analysis("UNKNOWN", event_date=TODAY, timing=None),
        _analysis("NOPRICE", price=None),
    ]
    assert open_positions(data, skipped, TODAY) == 0
    taken = [
        _analysis("BGRADE", grade="B"),
        _analysis("TONIGHT", event_date=TODAY, timing="after-market"),
    ]
    assert open_positions(data, taken, TODAY) == 2


def test_dedupe_across_runs_and_closed():
    data = _fresh()
    assert open_positions(data, [_analysis()], TODAY) == 1
    assert open_positions(data, [_analysis()], TODAY + dt.timedelta(days=1)) == 0
    data["positions"][0]["status"] = "closed"
    assert open_positions(data, [_analysis()], TODAY + dt.timedelta(days=1)) == 0


def test_capital_gate_skips_but_keeps_scanning():
    data = _fresh(balance=6000.0)
    candidates = [
        _analysis("BIG"),                                  # risk 4940
        _analysis("BIG2"),                                 # risk 4940 - no room
        _analysis("SMALL", strategy=_condor(max_loss=8.0)),  # qty 6, risk 4800 - no
    ]
    # BIG fits (4940 <= 6000); BIG2 would take total to 9880 > 6000; SMALL
    # would take total to 9740 > 6000. Only BIG opens.
    assert open_positions(data, candidates, TODAY) == 1
    assert data["positions"][0]["ticker"] == "BIG"

    tiny = _analysis("TINY", strategy=_condor(max_loss=2.5))  # qty 20, risk 5000
    assert open_positions(data, [tiny], TODAY) == 0           # 4940+5000 > 6000
    # With a smaller per-position cap the same setup fits the headroom.
    tiny2 = _analysis("TINY2", strategy=_condor(max_loss=10.0))  # qty 1, risk 1000
    assert open_positions(data, [tiny2], TODAY, max_risk=1000.0) == 1


# --------------------------------------------------------------------------- #
# Closing: valuation, timing gates, fallbacks
# --------------------------------------------------------------------------- #
def _opened(strategy=None, ticker="RICHCO"):
    data = _fresh()
    open_positions(data, [_analysis(ticker, strategy=strategy)], TODAY)
    assert len(data["positions"]) == 1
    return data


def test_close_credit_profit():
    data = _opened()
    provider = InMemoryProvider()
    provider.add_option_chain(_chain({
        ("put", 95.0): 0.05, ("put", 90.0): 0.01,
        ("call", 105.0): 0.05, ("call", 110.0): 0.01,
    }))
    closed = close_due_positions(data, provider, EVENT + dt.timedelta(days=1))
    assert len(closed) == 1
    pos = closed[0]
    # value = +0.01 +0.01 -0.05 -0.05 = -0.08; pnl/share = -0.08 + 1.2 = 1.12
    assert pos["close_value"] == -0.08
    assert pos["realized_pnl"] == round(1.12 * 100 * 13, 2)
    assert pos["close_method"] == "chain"
    assert data["balance"] == 25000.0 + pos["realized_pnl"]


def test_close_debit_profit_and_loss():
    for marks, expected_per_share in (
        ({("call", 100.0): 4.0, ("put", 100.0): 2.5}, 2.5),   # sold for 6.5
        ({("call", 100.0): 0.5, ("put", 100.0): 0.5}, -3.0),  # sold for 1.0
    ):
        data = _opened(strategy=_straddle())
        qty = data["positions"][0]["qty"]  # floor(5000/400) = 12
        provider = InMemoryProvider()
        provider.add_option_chain(_chain(marks))
        closed = close_due_positions(data, provider, EVENT + dt.timedelta(days=1))
        assert closed[0]["realized_pnl"] == round(expected_per_share * 100 * qty, 2)


def test_close_timing_gates():
    provider = InMemoryProvider()
    provider.add_option_chain(_chain({
        ("put", 95.0): 0.05, ("put", 90.0): 0.01,
        ("call", 105.0): 0.05, ("call", 110.0): 0.01,
    }))
    data = _opened()
    assert close_due_positions(data, provider, EVENT - dt.timedelta(days=1)) == []
    assert close_due_positions(data, provider, EVENT) == []  # timing None
    data["positions"][0]["timing"] = "after-market"
    assert close_due_positions(data, provider, EVENT) == []
    data["positions"][0]["timing"] = "pre-market"
    assert len(close_due_positions(data, provider, EVENT)) == 1


def test_intrinsic_settlement_after_expiry():
    data = _opened()
    provider = InMemoryProvider()  # no chain at all
    spot_bar = PriceBar(day=EXPIRY, open=92.0, high=92.0, low=92.0, close=92.0)
    provider.add("RICHCO", bars=[spot_bar])
    closed = close_due_positions(data, provider, EXPIRY + dt.timedelta(days=1))
    assert len(closed) == 1
    pos = closed[0]
    # At spot 92: short 95p intrinsic 3.0, long 90p 0.0, calls 0.0
    # value = -3.0; pnl/share = -3.0 + 1.2 = -1.8
    assert pos["close_value"] == -3.0
    assert pos["close_method"] == "intrinsic"
    assert pos["realized_pnl"] == round(-1.8 * 100 * 13, 2)


def test_unvaluable_position_stays_open():
    data = _opened()
    provider = InMemoryProvider()  # no chain, no bars
    assert close_due_positions(data, provider, EXPIRY + dt.timedelta(days=1)) == []
    assert data["positions"][0]["status"] == "open"
    assert data["balance"] == 25000.0
    # Before expiry with no quotes: also stays open.
    assert close_due_positions(data, provider, EVENT + dt.timedelta(days=1)) == []


def test_partial_leg_valuation_stays_open():
    data = _opened()
    provider = InMemoryProvider()
    provider.add_option_chain(_chain({("put", 95.0): 0.05}))  # 3 legs missing
    assert close_due_positions(data, provider, EVENT + dt.timedelta(days=1)) == []
    assert data["positions"][0]["status"] == "open"


# --------------------------------------------------------------------------- #
# Summary + rendering
# --------------------------------------------------------------------------- #
def test_summarize_counts():
    data = _fresh()
    open_positions(data, [_analysis("A"), _analysis("B")], TODAY)
    data["positions"][0]["status"] = "closed"
    data["positions"][0]["close_date"] = TODAY.isoformat()
    data["positions"][0]["realized_pnl"] = 500.0
    data["balance"] += 500.0
    summary = summarize_paper(data)
    assert summary.balance == 25500.0
    assert summary.total_pnl == 500.0
    assert summary.closed_total == 1 and summary.wins == 1
    assert summary.win_rate == 1.0
    assert len(summary.open_positions) == 1
    assert summary.open_risk == 4940.0


def test_paper_rendered_in_email():
    from earnings_analyzer.formatting import render_email

    summary = PaperSummary(
        balance=25612.0, start_balance=25000.0,
        open_positions=[{
            "ticker": "TGT", "strategy": "Iron condor", "qty": 13,
            "event_date": "2026-08-19", "risk_dollars": 4940.0,
        }],
        closed_this_run=[{
            "ticker": "WINCO", "strategy": "Long straddle", "qty": 12,
            "event_date": "2026-08-06", "realized_pnl": 612.0,
        }],
        closed_total=1, wins=1,
    )
    _, text, html = render_email([], TODAY, paper=summary)
    assert "Paper portfolio" in text
    assert "$25,612.00 (+612.00)" in text
    assert "CLOSED WINCO Long straddle x12" in text
    assert "OPEN TGT Iron condor x13" in text
    assert "Paper portfolio" in html and "100% win rate" in html

    _, text_none, html_none = render_email([], TODAY, paper=None)
    assert "Paper portfolio" not in text_none
    assert "Paper portfolio" not in html_none
