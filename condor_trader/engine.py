"""Pure condor-martingale engine: ledger, strikes, sizing, settlement.

The strategy: martingale position sizing applied to weekly SPX iron
condors (the shape of the reference SPXW trade):

- One short iron condor at a time: sell a put spread below the market
  and a call spread above it, both ``wing`` points wide, short strikes
  ~``otm_pct`` percent out-of-the-money, nearest weekly expiry.
- Hold to expiration. SPXW is cash-settled: the position settles at
  intrinsic value against the expiry day's closing level. Win = keep
  the credit; loss = wing - credit per share.
- The martingale ladder sizes the CONTRACT COUNT:
      qty = base_qty * factor**step
  capped so the position's max loss never exceeds ``max_risk`` dollars
  or the current balance. A losing settlement moves the ladder one
  step up, a winning one resets it, a scratch holds it.
- The account busts at $0 and never trades again.

Ledger schema v1 (JSON, persisted via the workflow cache).
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from typing import List, Optional

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1
MAX_SETTLED_KEPT = 300
MAX_STEP = 20   # ladder bookkeeping bound; the risk cap binds far earlier


@dataclass
class CondorSummary:
    balance: float
    start_balance: float
    busted: bool
    step: int                        # ladder position for the NEXT condor
    open_positions: List[dict] = field(default_factory=list)
    settled_total: int = 0
    wins: int = 0
    current_loss_streak: int = 0
    max_loss_streak: int = 0
    max_drawdown: float = 0.0
    recent: List[dict] = field(default_factory=list)   # newest first

    @property
    def total_pnl(self) -> float:
        return round(self.balance - self.start_balance, 2)

    @property
    def losses(self) -> int:
        return self.settled_total - self.wins

    @property
    def win_rate(self) -> Optional[float]:
        return self.wins / self.settled_total if self.settled_total else None

    @property
    def open_risk(self) -> float:
        return sum(p["risk_dollars"] for p in self.open_positions)


def load_ledger(path: str, start_balance: float) -> dict:
    """Load the ledger. Missing/malformed -> a fresh one."""
    if path and os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict) and data.get("version") == SCHEMA_VERSION:
                return data
            logger.warning("Ledger %s has unexpected format; starting fresh", path)
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Could not read ledger %s (%s); starting fresh", path, exc)
    return {
        "version": SCHEMA_VERSION,
        "start_balance": start_balance,
        "balance": start_balance,
        "step": 0,
        "busted": False,
        "positions": [],
    }


def save_ledger(path: str, data: dict) -> None:
    """Write the ledger, pruning old settled positions (open ones never)."""
    settled = [p for p in data["positions"] if p["status"] == "settled"]
    if len(settled) > MAX_SETTLED_KEPT:
        drop = set(id(p) for p in settled[:-MAX_SETTLED_KEPT])
        data["positions"] = [p for p in data["positions"] if id(p) not in drop]
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)


# --------------------------------------------------------------------- #
# Strike selection (pure)
# --------------------------------------------------------------------- #
def _mid_at(contracts, strike: float) -> Optional[float]:
    for c in contracts:
        if abs(c.strike - strike) < 1e-3:
            return c.mid
    return None


def pick_condor(chain, otm_pct: float, wing: float) -> Optional[dict]:
    """Choose strikes and price the condor from chain mids.

    Short put: the highest put strike at/below spot*(1 - otm_pct/100).
    Short call: the lowest call strike at/above spot*(1 + otm_pct/100).
    Wings sit ``wing`` points beyond the shorts. Returns None unless
    all four legs quote with usable mids and the net credit is positive.
    """
    spot = chain.underlying_price
    if not spot or spot <= 0:
        return None
    put_strikes = sorted({c.strike for c in chain.puts})
    call_strikes = sorted({c.strike for c in chain.calls})
    puts_below = [s for s in put_strikes if s <= spot * (1 - otm_pct / 100.0)]
    calls_above = [s for s in call_strikes if s >= spot * (1 + otm_pct / 100.0)]
    if not puts_below or not calls_above:
        return None
    short_put, short_call = puts_below[-1], calls_above[0]
    long_put, long_call = short_put - wing, short_call + wing
    mids = {
        "short_put": _mid_at(chain.puts, short_put),
        "long_put": _mid_at(chain.puts, long_put),
        "short_call": _mid_at(chain.calls, short_call),
        "long_call": _mid_at(chain.calls, long_call),
    }
    if any(v is None for v in mids.values()):
        return None
    credit = round(
        mids["short_put"] - mids["long_put"]
        + mids["short_call"] - mids["long_call"], 4,
    )
    if credit <= 0:
        return None
    return {
        "short_put": short_put, "long_put": long_put,
        "short_call": short_call, "long_call": long_call,
        "credit": credit, "spot": spot,
    }


# --------------------------------------------------------------------- #
# Martingale sizing
# --------------------------------------------------------------------- #
def qty_for_step(
    step: int, base_qty: int, factor: float,
    risk_per_contract: float, max_risk: float, balance: float,
) -> int:
    """Ladder quantity, capped by the dollar risk limit AND the balance."""
    if risk_per_contract <= 0:
        return 0
    want = int(base_qty * factor ** min(step, MAX_STEP))
    cap = int(min(max_risk, balance) // risk_per_contract)
    return max(0, min(want, cap))


# --------------------------------------------------------------------- #
# Ledger operations
# --------------------------------------------------------------------- #
def open_condor(data: dict, expiry, picked: dict, qty: int, today) -> dict:
    wing = picked["short_put"] - picked["long_put"]
    pos = {
        "expiry": expiry.isoformat(),
        "short_put": picked["short_put"], "long_put": picked["long_put"],
        "short_call": picked["short_call"], "long_call": picked["long_call"],
        "credit": picked["credit"],
        "qty": qty,
        "step": data["step"],
        "risk_dollars": round((wing - picked["credit"]) * 100.0 * qty, 2),
        "max_profit": round(picked["credit"] * 100.0 * qty, 2),
        "open_date": today.isoformat(),
        "open_spot": picked["spot"],
        "status": "open",
        "settle_price": None,
        "settle_date": None,
        "realized_pnl": None,
    }
    data["positions"].append(pos)
    logger.info(
        "Condor: opened %s %g/%g P + %g/%g C x%d for %.2f credit "
        "(step %d, risk $%.0f)",
        pos["expiry"], pos["long_put"], pos["short_put"],
        pos["short_call"], pos["long_call"], qty, picked["credit"],
        pos["step"], pos["risk_dollars"],
    )
    return pos


def settlement_loss(pos: dict, settle: float) -> float:
    """Per-share intrinsic payout owed at settlement (>= 0)."""
    put_loss = (max(0.0, pos["short_put"] - settle)
                - max(0.0, pos["long_put"] - settle))
    call_loss = (max(0.0, settle - pos["short_call"])
                 - max(0.0, settle - pos["long_call"]))
    return round(put_loss + call_loss, 4)


def settle_position(data: dict, pos: dict, settle: float, day) -> dict:
    """Cash-settle at the expiry close and advance the ladder."""
    loss = settlement_loss(pos, settle)
    realized = round((pos["credit"] - loss) * 100.0 * pos["qty"], 2)
    pos["status"] = "settled"
    pos["settle_price"] = settle
    pos["settle_date"] = day.isoformat()
    pos["realized_pnl"] = realized
    data["balance"] = round(data["balance"] + realized, 2)
    if realized < 0:
        data["step"] = min(data["step"] + 1, MAX_STEP)
    elif realized > 0:
        data["step"] = 0
    data["busted"] = data["balance"] <= 0
    logger.info(
        "Condor: settled %s at %.2f for %+.2f (balance $%.2f%s)",
        pos["expiry"], settle, realized, data["balance"],
        " — BUSTED" if data["busted"] else "",
    )
    return pos


def summarize(data: dict, *, recent_n: int = 10) -> CondorSummary:
    positions = data.get("positions", [])
    open_pos = [p for p in positions if p["status"] == "open"]
    done = [p for p in positions if p["status"] == "settled"]
    done.sort(key=lambda p: p["expiry"])
    equity = data.get("start_balance", 0.0)
    peak, max_dd = equity, 0.0
    streak = max_streak = 0
    for p in done:
        pnl = p["realized_pnl"] or 0.0
        equity += pnl
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
        if pnl < 0:
            streak += 1
            max_streak = max(max_streak, streak)
        elif pnl > 0:
            streak = 0
    current = 0
    for p in reversed(done):
        if (p["realized_pnl"] or 0.0) >= 0:
            break
        current += 1
    return CondorSummary(
        balance=data.get("balance", 0.0),
        start_balance=data.get("start_balance", 0.0),
        busted=data.get("busted", False),
        step=data.get("step", 0),
        open_positions=open_pos,
        settled_total=len(done),
        wins=sum(1 for p in done if (p["realized_pnl"] or 0) > 0),
        current_loss_streak=current,
        max_loss_streak=max_streak,
        max_drawdown=round(max_dd, 2),
        recent=done[-recent_n:][::-1],
    )
