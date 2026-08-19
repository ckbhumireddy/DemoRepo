import datetime as dt

from earnings_analyzer.models import OptionChain, OptionContract
from condor_trader.engine import (
    load_ledger,
    open_condor,
    pick_condor,
    qty_for_step,
    save_ledger,
    settle_position,
    settlement_loss,
    summarize,
)

EXPIRY = dt.date(2026, 3, 9)
TODAY = dt.date(2026, 3, 2)


def _chain(spot=6730.0, mids=None, drop=()):
    """Four-leg chain: puts 6640/6645, calls 6815/6820."""
    mids = mids or {"6645P": 2.0, "6640P": 1.4, "6815C": 1.5, "6820C": 1.0}
    chain = OptionChain(ticker="$SPX", expiry=EXPIRY, underlying_price=spot)
    for label, mid in mids.items():
        strike, kind = float(label[:-1]), label[-1]
        if label in drop:
            continue
        bucket = chain.calls if kind == "C" else chain.puts
        bucket.append(OptionContract(
            contract_symbol=label, option_type="call" if kind == "C" else "put",
            strike=strike, expiry=EXPIRY, bid=mid, ask=mid,
        ))
    return chain


def _fresh(balance=25000.0):
    return load_ledger("", balance)


def _open(data, picked=None, qty=1):
    picked = picked or pick_condor(_chain(), 1.25, 5.0)
    return open_condor(data, EXPIRY, picked, qty, TODAY)


# ------------------------------------------------------------- selection
def test_pick_condor_strikes_and_credit():
    p = pick_condor(_chain(), 1.25, 5.0)
    # 1.25% OTM of 6730: puts <= 6645.9 -> 6645; calls >= 6814.1 -> 6815.
    assert (p["short_put"], p["long_put"]) == (6645.0, 6640.0)
    assert (p["short_call"], p["long_call"]) == (6815.0, 6820.0)
    assert round(p["credit"], 4) == 1.1     # (2.0-1.4)+(1.5-1.0)


def test_pick_condor_requires_all_legs():
    assert pick_condor(_chain(drop=("6640P",)), 1.25, 5.0) is None
    assert pick_condor(_chain(spot=0.0), 1.25, 5.0) is None


# ------------------------------------------------------------ settlement
def test_settlement_regions():
    data = _fresh()
    pos = _open(data)
    # Between the shorts: keep the whole credit.
    assert settlement_loss(pos, 6730.0) == 0.0
    # Below the put wing: full wing loss.
    assert settlement_loss(pos, 6000.0) == 5.0
    # Between long and short put: partial.
    assert settlement_loss(pos, 6642.0) == 3.0
    # Above the call wing: full wing loss.
    assert settlement_loss(pos, 7000.0) == 5.0


def test_settle_win_resets_ladder():
    data = _fresh()
    pos = _open(data)
    data["step"] = 3
    settle_position(data, pos, 6730.0, EXPIRY)
    assert pos["realized_pnl"] == 110.0      # 1.1 credit x100
    assert data["balance"] == 25110.0
    assert data["step"] == 0


def test_settle_loss_steps_up():
    data = _fresh()
    pos = _open(data)
    settle_position(data, pos, 6600.0, EXPIRY)
    assert pos["realized_pnl"] == -390.0     # (1.1 - 5.0) x100
    assert data["balance"] == 24610.0
    assert data["step"] == 1


def test_bust_at_or_below_zero():
    data = _fresh(balance=300.0)
    pos = _open(data)
    settle_position(data, pos, 6000.0, EXPIRY)
    assert data["balance"] == -90.0
    assert data["busted"] is True


# ----------------------------------------------------------------- sizing
def test_qty_ladder_and_caps():
    # risk/contract $390: ladder 1,2,4,8 ... capped by dollars and balance.
    assert qty_for_step(0, 1, 2.0, 390.0, 10000.0, 25000.0) == 1
    assert qty_for_step(2, 1, 2.0, 390.0, 10000.0, 25000.0) == 4
    assert qty_for_step(6, 1, 2.0, 390.0, 10000.0, 25000.0) == 25   # $ cap
    assert qty_for_step(6, 1, 2.0, 390.0, 10000.0, 1000.0) == 2     # balance
    assert qty_for_step(0, 1, 2.0, 390.0, 100.0, 25000.0) == 0
    assert qty_for_step(0, 1, 2.0, 0.0, 10000.0, 25000.0) == 0


# ----------------------------------------------------------------- ledger
def test_ledger_roundtrip(tmp_path):
    path = str(tmp_path / "condor.json")
    data = _fresh()
    pos = _open(data)
    settle_position(data, pos, 6730.0, EXPIRY)
    save_ledger(path, data)
    assert load_ledger(path, 1.0) == data


def test_summarize_streaks_and_drawdown():
    data = _fresh()
    for settle, exp_day in ((6600.0, 9), (6600.0, 16), (6730.0, 23)):
        pos = open_condor(
            data, dt.date(2026, 3, exp_day),
            pick_condor(_chain(), 1.25, 5.0), 1, TODAY,
        )
        settle_position(data, pos, settle, dt.date(2026, 3, exp_day))
    s = summarize(data)
    assert (s.settled_total, s.wins, s.losses) == (3, 1, 2)
    assert s.max_loss_streak == 2
    assert s.current_loss_streak == 0
    assert s.max_drawdown == 780.0
    assert s.recent[0]["expiry"] == "2026-03-23"
