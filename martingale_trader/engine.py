"""Pure martingale engine: state, stake ladder, settlement, summary.

The strategy (a deliberate demonstration of martingale sizing — see the
README warning):

- One long SPX round per trading day, close to close. A round opened at
  today's close settles at the next available close; if a run is missed
  the round simply spans more days.
- The stake is a notional dollar exposure:
  ``min(base * factor**step, cap)`` — base is a fraction of the balance
  (or fixed dollars), so exposure compounds with the account while the
  dollar cap bounds any single crash week.
- A losing round moves the ladder one step up (multiply the stake by
  ``factor``), a winning round resets it to step 0, and a flat round
  leaves it alone. Past the point where the cap binds, the stake stays
  at the cap until a win resets it.
- The notional may exceed the balance (paper leverage) — that is exactly
  the failure mode martingale hides. The account busts when the balance
  reaches 0 or below; a busted account never trades again.

State schema v1 (JSON, persisted via the workflow cache). A version bump
starts a fresh account at the start balance — acceptable for paper money.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from typing import List, Optional

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1
MAX_ROUNDS_KEPT = 500   # oldest settled rounds are pruned beyond this


@dataclass
class MartingaleSummary:
    balance: float
    start_balance: float
    busted: bool
    step: int                       # ladder position for the NEXT round
    next_notional: float
    open_round: Optional[dict] = None
    rounds_total: int = 0
    wins: int = 0
    losses: int = 0
    pushes: int = 0
    current_loss_streak: int = 0
    max_loss_streak: int = 0
    max_drawdown: float = 0.0       # dollars, peak-to-trough
    recent: List[dict] = field(default_factory=list)   # newest first

    @property
    def total_pnl(self) -> float:
        return round(self.balance - self.start_balance, 2)

    @property
    def win_rate(self) -> Optional[float]:
        decided = self.wins + self.losses
        return self.wins / decided if decided else None

    @property
    def leverage(self) -> Optional[float]:
        if self.busted or self.balance <= 0:
            return None
        return self.next_notional / self.balance


def load_state(path: str, start_balance: float) -> dict:
    """Load the state. Missing/malformed -> a fresh account."""
    if path and os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict) and data.get("version") == SCHEMA_VERSION:
                return data
            logger.warning("State %s has unexpected format; starting fresh", path)
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Could not read state %s (%s); starting fresh", path, exc)
    return {
        "version": SCHEMA_VERSION,
        "start_balance": start_balance,
        "balance": start_balance,
        "step": 0,
        "busted": False,
        "open_round": None,
        "rounds": [],
    }


def save_state(path: str, state: dict) -> None:
    """Write the state, pruning settled rounds beyond MAX_ROUNDS_KEPT."""
    state["rounds"] = state["rounds"][-MAX_ROUNDS_KEPT:]
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=2)


MAX_STEP = 30   # ladder bookkeeping bound; the dollar cap binds far earlier


def notional_for_step(
    step: int, base: float, factor: float, cap: float
) -> float:
    """The stake for a ladder position, capped at the table limit."""
    return round(min(base * factor ** min(step, MAX_STEP), cap), 2)


def open_round(state: dict, day, price: float, notional: float) -> dict:
    """Open the next round at this close. The caller checks preconditions."""
    rnd = {
        "entry_date": day.isoformat(),
        "entry_price": price,
        "notional": notional,
        "step": state["step"],
    }
    state["open_round"] = rnd
    logger.info(
        "Martingale: opened round at %.2f, notional $%.0f (step %d)",
        price, notional, state["step"],
    )
    return rnd


def settle_open_round(state: dict, day, price: float) -> dict:
    """Settle the open round at this close and advance the ladder."""
    rnd = state["open_round"]
    pnl = round(rnd["notional"] * (price / rnd["entry_price"] - 1.0), 2)
    state["balance"] = round(state["balance"] + pnl, 2)
    if pnl < 0:
        state["step"] = min(state["step"] + 1, MAX_STEP)
    elif pnl > 0:
        state["step"] = 0
    state["busted"] = state["balance"] <= 0
    record = {
        **rnd,
        "exit_date": day.isoformat(),
        "exit_price": price,
        "pnl": pnl,
        "return_pct": round((price / rnd["entry_price"] - 1.0) * 100.0, 3),
        "balance_after": state["balance"],
    }
    state["rounds"].append(record)
    state["open_round"] = None
    logger.info(
        "Martingale: settled round %+.2f at %.2f; balance $%.2f%s",
        pnl, price, state["balance"], " — BUSTED" if state["busted"] else "",
    )
    return record


def summarize(
    state: dict, base: float, factor: float, cap: float, *, recent_n: int = 10
) -> MartingaleSummary:
    rounds = state.get("rounds", [])
    wins = sum(1 for r in rounds if r["pnl"] > 0)
    losses = sum(1 for r in rounds if r["pnl"] < 0)

    current_streak = 0
    for r in reversed(rounds):
        if r["pnl"] >= 0:
            break
        current_streak += 1

    max_streak = streak = 0
    equity = state.get("start_balance", 0.0)
    peak = equity
    max_dd = 0.0
    for r in rounds:
        if r["pnl"] < 0:
            streak += 1
            max_streak = max(max_streak, streak)
        elif r["pnl"] > 0:
            streak = 0
        equity = r["balance_after"]
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)

    return MartingaleSummary(
        balance=state.get("balance", 0.0),
        start_balance=state.get("start_balance", 0.0),
        busted=state.get("busted", False),
        step=state.get("step", 0),
        next_notional=notional_for_step(
            state.get("step", 0), base, factor, cap
        ),
        open_round=state.get("open_round"),
        rounds_total=len(rounds),
        wins=wins,
        losses=losses,
        pushes=len(rounds) - wins - losses,
        current_loss_streak=current_streak,
        max_loss_streak=max_streak,
        max_drawdown=round(max_dd, 2),
        recent=rounds[-recent_n:][::-1],
    )
