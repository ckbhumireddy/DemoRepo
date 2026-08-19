import datetime as dt
import json

from martingale_trader.engine import (
    load_state,
    notional_for_step,
    open_round,
    save_state,
    settle_open_round,
    summarize,
)

D1 = dt.date(2026, 8, 17)
D2 = dt.date(2026, 8, 18)

# A plain doubling ladder with a high cap reproduces the classic behavior.
BASE, FACTOR, CAP = 5000.0, 2.0, 320000.0


def _fresh():
    return load_state("", 25000.0)


def _run_round(state, entry, exit_, *, base=BASE, factor=FACTOR, cap=CAP):
    notional = notional_for_step(state["step"], base, factor, cap)
    open_round(state, D1, entry, notional)
    return settle_open_round(state, D2, exit_)


# ---------------------------------------------------------------- ladder
def test_notional_ladder_multiplies_and_caps():
    assert notional_for_step(0, 5000.0, 2.0, 320000.0) == 5000.0
    assert notional_for_step(3, 5000.0, 2.0, 320000.0) == 40000.0
    assert notional_for_step(6, 5000.0, 2.0, 320000.0) == 320000.0
    # Past the table limit the stake stays at the cap.
    assert notional_for_step(9, 5000.0, 2.0, 320000.0) == 320000.0
    # The champion shape: 30% of a $25k balance, factor 3, $160k cap.
    assert notional_for_step(0, 7500.0, 3.0, 160000.0) == 7500.0
    assert notional_for_step(1, 7500.0, 3.0, 160000.0) == 22500.0
    assert notional_for_step(2, 7500.0, 3.0, 160000.0) == 67500.0
    assert notional_for_step(3, 7500.0, 3.0, 160000.0) == 160000.0


def test_loss_steps_up_win_resets_push_holds():
    state = _fresh()
    r = _run_round(state, 100.0, 99.0)          # -1% on $5,000 = -$50
    assert r["pnl"] == -50.0
    assert state["step"] == 1
    assert state["balance"] == 24950.0

    r = _run_round(state, 100.0, 100.0)         # flat: ladder holds
    assert r["pnl"] == 0.0
    assert state["step"] == 1

    r = _run_round(state, 100.0, 102.0)         # +2% on $10,000 = +$200
    assert r["pnl"] == 200.0
    assert state["step"] == 0
    assert state["balance"] == 25150.0


def test_notional_never_exceeds_the_cap():
    state = _fresh()
    for _ in range(10):
        _run_round(state, 100.0, 99.0, cap=25000.0)
    assert notional_for_step(state["step"], BASE, FACTOR, 25000.0) == 25000.0


def test_bust_at_or_below_zero():
    state = load_state("", 100.0)
    _run_round(state, 100.0, 98.0)              # -2% on $5,000 = -$100
    assert state["balance"] == 0.0
    assert state["busted"] is True


# ---------------------------------------------------------------- state io
def test_state_roundtrip(tmp_path):
    path = str(tmp_path / "state" / "martingale.json")
    state = _fresh()
    _run_round(state, 100.0, 101.0)
    save_state(path, state)
    loaded = load_state(path, 1.0)
    assert loaded == state


def test_bad_state_starts_fresh(tmp_path):
    path = str(tmp_path / "martingale.json")
    path_obj = tmp_path / "martingale.json"
    path_obj.write_text("not json", encoding="utf-8")
    assert load_state(path, 25000.0)["balance"] == 25000.0
    path_obj.write_text(json.dumps({"version": 99}), encoding="utf-8")
    assert load_state(path, 25000.0)["balance"] == 25000.0


# ---------------------------------------------------------------- summary
def test_summarize_streaks_and_drawdown():
    state = _fresh()
    _run_round(state, 100.0, 99.0)      # -50   -> 24950
    _run_round(state, 100.0, 99.0)      # -100  -> 24850
    _run_round(state, 100.0, 101.0)     # +200  -> 25050
    _run_round(state, 100.0, 99.0)      # -50   -> 25000
    s = summarize(state, BASE, FACTOR, CAP)
    assert (s.rounds_total, s.wins, s.losses, s.pushes) == (4, 1, 3, 0)
    assert s.current_loss_streak == 1
    assert s.max_loss_streak == 2
    assert s.max_drawdown == 150.0      # 25000 peak -> 24850 trough
    assert s.total_pnl == 0.0
    assert s.step == 1
    assert s.next_notional == 10000.0
    assert s.recent[0]["balance_after"] == 25000.0   # newest first


def test_summarize_win_rate_ignores_pushes():
    state = _fresh()
    _run_round(state, 100.0, 101.0)
    _run_round(state, 100.0, 100.0)
    s = summarize(state, BASE, FACTOR, CAP)
    assert s.win_rate == 1.0
    assert summarize(_fresh(), BASE, FACTOR, CAP).win_rate is None
