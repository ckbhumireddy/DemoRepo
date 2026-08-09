import datetime as dt

from earnings_analyzer.implied_move import compute_implied_move, select_event_expiry
from earnings_analyzer.models import OptionChain, OptionContract

EVENT = dt.date(2026, 8, 13)
EXPIRY = dt.date(2026, 8, 14)


def _c(kind, strike, bid, ask, expiry=EXPIRY, oi=1500, vol=500, iv=0.5):
    return OptionContract(
        contract_symbol=f"X{strike}{kind}",
        option_type=kind, strike=strike, expiry=expiry,
        bid=bid, ask=ask, last=(bid + ask) / 2,
        implied_volatility=iv, volume=vol, open_interest=oi,
    )


def _chain(spot=100.0, call_mid=3.0, put_mid=3.0, expiry=EXPIRY):
    return OptionChain(
        ticker="X", expiry=expiry, underlying_price=spot,
        calls=[_c("call", spot, call_mid - 0.05, call_mid + 0.05, expiry)],
        puts=[_c("put", spot, put_mid - 0.05, put_mid + 0.05, expiry)],
    )


def test_select_event_expiry_first_on_or_after():
    expiries = [dt.date(2026, 8, 7), dt.date(2026, 8, 14), dt.date(2026, 8, 21)]
    assert select_event_expiry(expiries, EVENT) == dt.date(2026, 8, 14)
    assert select_event_expiry([dt.date(2026, 8, 1)], EVENT) is None


def test_implied_move_math_and_verdicts():
    # Straddle $6 on $100 spot = 6% implied.
    move = compute_implied_move(_chain(), 100.0, EVENT, hist_avg_abs_move=4.0)
    assert move.implied_move_pct == 6.0
    assert move.straddle_debit == 6.0
    assert move.ratio == 1.5
    assert move.verdict == "rich"

    cheap = compute_implied_move(_chain(), 100.0, EVENT, hist_avg_abs_move=10.0)
    assert cheap.verdict == "cheap"          # ratio 0.6

    fair = compute_implied_move(_chain(), 100.0, EVENT, hist_avg_abs_move=6.0)
    assert fair.verdict == "fair"            # ratio 1.0


def test_implied_move_threshold_edges():
    # ratio exactly 1.25 -> rich; exactly 0.80 -> cheap (inclusive bounds).
    assert compute_implied_move(_chain(), 100.0, EVENT, 4.8).verdict == "rich"
    assert compute_implied_move(_chain(), 100.0, EVENT, 7.5).verdict == "cheap"


def test_implied_move_diluted_expiry():
    far = _chain(expiry=EVENT + dt.timedelta(days=30))
    move = compute_implied_move(far, 100.0, EVENT, hist_avg_abs_move=4.0)
    assert move.diluted is True
    assert move.verdict == "unknown"
    assert move.ratio is None


def test_implied_move_unknown_without_baseline():
    move = compute_implied_move(_chain(), 100.0, EVENT, hist_avg_abs_move=None)
    assert move.verdict == "unknown"


def test_implied_move_none_on_empty_chain():
    empty = OptionChain(ticker="X", expiry=EXPIRY, underlying_price=100.0)
    assert compute_implied_move(empty, 100.0, EVENT, 4.0) is None
    assert compute_implied_move(_chain(), 0.0, EVENT, 4.0) is None
