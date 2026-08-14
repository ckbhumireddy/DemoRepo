import datetime as dt

from earnings_analyzer.models import (
    ImpliedMove,
    LiquidityReport,
    OptionChain,
    OptionContract,
    OptionLeg,
    PriceBar,
    PricedStrategy,
    TrendContext,
)
from earnings_analyzer.provider import InMemoryProvider
from earnings_trader.candidates import Candidate
from earnings_trader.ledger import (
    close_due_positions,
    load_ledger,
    open_from_decisions,
    save_ledger,
    summarize,
)
from earnings_trader.pipeline import TradeDecision

TODAY = dt.date(2026, 8, 17)
EVENT = dt.date(2026, 8, 18)
EXPIRY = dt.date(2026, 8, 21)
NOW = dt.datetime(2026, 8, 17, 19, 35, tzinfo=dt.timezone.utc)


def _condor(max_loss=3.8, net_premium=1.2):
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
        net_premium=-debit, max_loss=debit, breakevens=[96.0, 104.0],
    )


def _decision(ticker="RICHCO", strategy=None, ratio=1.5, timing="after-market",
              event_date=EVENT):
    return TradeDecision(
        candidate=Candidate(ticker, event_date, timing,
                            "today-amc" if timing == "after-market" else "tomorrow-bmo"),
        action="open",
        spot=100.0,
        implied=ImpliedMove(expiry=EXPIRY, straddle_debit=8.0,
                            implied_move_pct=8.0,
                            historical_avg_abs_move_pct=8.0 / ratio,
                            ratio=ratio, verdict="rich" if ratio > 1 else "cheap"),
        liquidity=LiquidityReport(tradeable=True, atm_spread_pct=2.0),
        trend=TrendContext(price=100.0, bias="neutral"),
        strategy=strategy or _condor(),
    )


def _fresh(balance=25000.0):
    return {"version": 2, "start_balance": 25000.0, "balance": balance,
            "positions": []}


def _chain(marks):
    chain = OptionChain(ticker="RICHCO", expiry=EXPIRY, underlying_price=100.0)
    for (option_type, strike), mid in marks.items():
        bucket = chain.calls if option_type == "call" else chain.puts
        bucket.append(OptionContract(
            contract_symbol=f"RICHCO-{option_type}-{strike}",
            option_type=option_type, strike=strike, expiry=EXPIRY,
            bid=mid, ask=mid,
        ))
    return chain


CHEAP_CLOSE = {
    ("put", 95.0): 0.05, ("put", 90.0): 0.01,
    ("call", 105.0): 0.05, ("call", 110.0): 0.01,
}


# --------------------------------------------------------------------------- #
# Persistence
# --------------------------------------------------------------------------- #
def test_load_missing_starts_fresh(tmp_path):
    data = load_ledger(str(tmp_path / "nope.json"), 25000.0)
    assert data["version"] == 2
    assert data["balance"] == 25000.0 and data["positions"] == []


def test_round_trip_and_prune(tmp_path):
    path = str(tmp_path / "state" / "trader.json")
    data = _fresh()
    open_from_decisions(data, [_decision()], TODAY, NOW)
    old_closed = {**data["positions"][0], "ticker": "OLD", "status": "closed",
                  "event_date": (TODAY - dt.timedelta(days=400)).isoformat()}
    old_open = {**old_closed, "ticker": "OLDOPEN", "status": "open"}
    data["positions"] += [old_closed, old_open]
    save_ledger(path, data, TODAY)
    loaded = load_ledger(path, 0.0)
    tickers = [p["ticker"] for p in loaded["positions"]]
    assert "OLD" not in tickers
    assert "OLDOPEN" in tickers
    assert loaded["balance"] == 25000.0


def test_load_malformed_or_old_version_starts_fresh(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{broken", encoding="utf-8")
    assert load_ledger(str(bad), 25000.0)["positions"] == []
    bad.write_text('{"version": 1, "positions": []}', encoding="utf-8")
    assert load_ledger(str(bad), 25000.0)["balance"] == 25000.0


# --------------------------------------------------------------------------- #
# Opening
# --------------------------------------------------------------------------- #
def test_sizing_and_metadata():
    data = _fresh()
    opened = open_from_decisions(data, [_decision()], TODAY, NOW)
    assert len(opened) == 1
    pos = opened[0]
    assert pos["qty"] == 13                # floor(5000 / 380)
    assert pos["risk_dollars"] == 4940.0
    assert pos["opened_phase"] == "entry"
    assert pos["opened_at"] == "2026-08-17T19:35:00+00:00"
    assert pos["signals"]["verdict"] == "rich"
    assert pos["signals"]["ratio"] == 1.5


def test_oversized_contract_skipped():
    data = _fresh()
    big = _decision(strategy=_condor(max_loss=60.0))   # one contract $6,000
    assert open_from_decisions(data, [big], TODAY, NOW) == []


def test_skip_decisions_never_open():
    data = _fresh()
    d = _decision()
    d.action = "skip"
    assert open_from_decisions(data, [d], TODAY, NOW) == []


def test_dedupe_across_runs_and_closed():
    data = _fresh()
    assert len(open_from_decisions(data, [_decision()], TODAY, NOW)) == 1
    assert open_from_decisions(data, [_decision()], TODAY, NOW) == []
    data["positions"][0]["status"] = "closed"
    assert open_from_decisions(data, [_decision()], TODAY, NOW) == []


def test_capital_gate_and_mispricing_priority():
    data = _fresh(balance=6000.0)
    small_edge = _decision("MILD", ratio=1.3)
    big_edge = _decision("WILD", ratio=2.0)
    # Only one fits the 6k balance; the bigger mispricing must win even
    # though it comes later in the list.
    opened = open_from_decisions(data, [small_edge, big_edge], TODAY, NOW)
    assert [p["ticker"] for p in opened] == ["WILD"]


# --------------------------------------------------------------------------- #
# Closing
# --------------------------------------------------------------------------- #
def _opened(strategy=None, timing="after-market"):
    data = _fresh()
    open_from_decisions(
        data, [_decision(strategy=strategy, timing=timing)], TODAY, NOW
    )
    assert len(data["positions"]) == 1
    return data


def test_close_credit_profit():
    data = _opened()
    provider = InMemoryProvider()
    provider.add_option_chain(_chain(CHEAP_CLOSE))
    closed = close_due_positions(data, provider, EVENT + dt.timedelta(days=1), NOW)
    pos = closed[0]
    assert pos["close_value"] == -0.08
    assert pos["realized_pnl"] == round(1.12 * 100 * 13, 2)
    assert pos["close_method"] == "chain"
    assert data["balance"] == 25000.0 + pos["realized_pnl"]


def test_close_debit_profit_and_loss():
    for marks, per_share in (
        ({("call", 100.0): 4.0, ("put", 100.0): 2.5}, 2.5),
        ({("call", 100.0): 0.5, ("put", 100.0): 0.5}, -3.0),
    ):
        data = _opened(strategy=_straddle())
        qty = data["positions"][0]["qty"]
        provider = InMemoryProvider()
        provider.add_option_chain(_chain(marks))
        closed = close_due_positions(
            data, provider, EVENT + dt.timedelta(days=1), NOW
        )
        assert closed[0]["realized_pnl"] == round(per_share * 100 * qty, 2)


def test_close_timing_gates():
    provider = InMemoryProvider()
    provider.add_option_chain(_chain(CHEAP_CLOSE))
    data = _opened(timing="after-market")
    assert close_due_positions(data, provider, EVENT - dt.timedelta(days=1), NOW) == []
    assert close_due_positions(data, provider, EVENT, NOW) == []  # AMC tonight
    data["positions"][0]["timing"] = "pre-market"
    assert len(close_due_positions(data, provider, EVENT, NOW)) == 1


def test_intrinsic_settlement_after_expiry():
    data = _opened()
    provider = InMemoryProvider()
    provider.add("RICHCO", bars=[
        PriceBar(day=EXPIRY, open=92.0, high=92.0, low=92.0, close=92.0)
    ])
    closed = close_due_positions(
        data, provider, EXPIRY + dt.timedelta(days=1), NOW
    )
    pos = closed[0]
    assert pos["close_value"] == -3.0     # short 95p intrinsic at spot 92
    assert pos["close_method"] == "intrinsic"
    assert pos["realized_pnl"] == round(-1.8 * 100 * 13, 2)


def test_unvaluable_and_partial_positions_stay_open():
    data = _opened()
    provider = InMemoryProvider()   # no chain, no bars
    assert close_due_positions(
        data, provider, EXPIRY + dt.timedelta(days=1), NOW
    ) == []
    assert data["positions"][0]["status"] == "open"
    assert data["balance"] == 25000.0

    partial = InMemoryProvider()
    partial.add_option_chain(_chain({("put", 95.0): 0.05}))  # 3 legs missing
    assert close_due_positions(
        data, partial, EVENT + dt.timedelta(days=1), NOW
    ) == []
    assert data["positions"][0]["status"] == "open"


# --------------------------------------------------------------------------- #
# Summary
# --------------------------------------------------------------------------- #
def test_summarize_lifetime_vs_window():
    data = _fresh()
    open_from_decisions(
        data,
        [_decision("NEW"), _decision("OLD"), _decision("STILLOPEN")],
        TODAY, NOW,
    )
    recent, old, _ = data["positions"]
    for pos, close_day, pnl in (
        (recent, TODAY - dt.timedelta(days=5), 500.0),
        (old, TODAY - dt.timedelta(days=40), -200.0),
    ):
        pos["status"] = "closed"
        pos["close_date"] = close_day.isoformat()
        pos["realized_pnl"] = pnl
        data["balance"] += pnl

    s = summarize(data, TODAY)
    assert s.closed_total == 2 and s.wins == 1
    assert s.window_closed == 1                 # 40-day-old close outside window
    assert s.window_wins == 1
    assert s.window_pnl == 500.0
    assert s.balance == 25300.0
    assert len(s.open_positions) == 1
    assert s.open_risk == 4940.0
