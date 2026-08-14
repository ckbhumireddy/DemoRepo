"""Paper trader: opens trade-sheet strategies before earnings, closes after.

Each real run first closes open positions whose earnings report has passed
(valued at live chain mids, or intrinsic value for expired legs), then opens
new positions from today's sheet: best-rated setups first, one strategy per
analysis, sized so no position risks more than ``max_risk`` dollars and the
worst-case total loss never exceeds the current balance.

Sign convention (all money per share; x100 x qty for dollars): the value of
a set of legs is ``sum(+mid for buys, -mid for sells)``, i.e. the net amount
received to liquidate. At open this equals ``-net_premium``, so realized
P&L per share is simply ``close_value + net_premium``.

The ledger lives under ``state/`` so the existing workflow cache persists
it. A schema-version bump starts a fresh ledger (the balance resets) —
acceptable for a paper account.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .models import OptionChain, TickerAnalysis
from .provider import MarketDataProvider

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1
RETENTION_DAYS = 365
GRADE_ORDER = {"A+": 0, "A": 1, "B": 2, "C": 3, "D": 4, "F": 5}


@dataclass
class PaperSummary:
    balance: float
    start_balance: float
    open_positions: List[dict] = field(default_factory=list)
    closed_this_run: List[dict] = field(default_factory=list)
    closed_total: int = 0
    wins: int = 0
    recent: List[dict] = field(default_factory=list)   # last closed, newest first

    @property
    def total_pnl(self) -> float:
        return self.balance - self.start_balance

    @property
    def losses(self) -> int:
        return self.closed_total - self.wins

    @property
    def win_rate(self) -> Optional[float]:
        return self.wins / self.closed_total if self.closed_total else None

    @property
    def open_risk(self) -> float:
        return sum(p["risk_dollars"] for p in self.open_positions)


def _position_key(ticker: str, event_date: str, strategy: str) -> str:
    return f"{ticker}:{event_date}:{strategy}"


def load_paper(path: str, start_balance: float) -> dict:
    """Load the ledger. Missing/malformed -> a fresh one at start_balance."""
    if path and os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict) and data.get("version") == SCHEMA_VERSION:
                return data
            logger.warning("Paper ledger %s has unexpected format; starting fresh", path)
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Could not read paper ledger %s (%s); starting fresh", path, exc)
    return {
        "version": SCHEMA_VERSION,
        "start_balance": start_balance,
        "balance": start_balance,
        "positions": [],
    }


def save_paper(path: str, data: dict, today: dt.date) -> None:
    """Write the ledger, pruning old *closed* positions (open ones never)."""
    cutoff = (today - dt.timedelta(days=RETENTION_DAYS)).isoformat()
    data["positions"] = [
        p for p in data["positions"]
        if p["status"] == "open" or p["event_date"] >= cutoff
    ]
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)


# --------------------------------------------------------------------------- #
# Event-timing gates
# --------------------------------------------------------------------------- #
def _event_pending(
    event_date: dt.date, timing: Optional[str], today: dt.date
) -> bool:
    """Openable: the report is still ahead. On event day only an explicit
    "after-market" qualifies — with unknown timing the report may be out."""
    if event_date > today:
        return True
    return event_date == today and timing == "after-market"


def _event_passed(
    event_date: dt.date, timing: Optional[str], today: dt.date
) -> bool:
    """Closeable: the report happened. Not the negation of _event_pending —
    event day with "after-market" or unknown timing is neither."""
    if event_date < today:
        return True
    return event_date == today and timing == "pre-market"


def _grade_ok(grade: Optional[str], min_grade: str) -> bool:
    if grade not in GRADE_ORDER:
        return False
    floor = GRADE_ORDER.get(min_grade, GRADE_ORDER["B"])
    return GRADE_ORDER[grade] <= floor


# --------------------------------------------------------------------------- #
# Opening
# --------------------------------------------------------------------------- #
def open_positions(
    data: dict,
    analyses: List[TickerAnalysis],
    today: dt.date,
    *,
    max_risk: float = 5000.0,
    min_grade: str = "B",
) -> int:
    """Open new positions from today's sheet. Returns the number opened.

    ``analyses`` arrive sorted best rating first, so capital goes to the
    strongest setups; a candidate that does not fit the remaining risk
    budget is skipped and scanning continues with cheaper ones.
    """
    known = {
        _position_key(p["ticker"], p["event_date"], p["strategy"])
        for p in data["positions"]
    }
    open_risk = sum(
        p["risk_dollars"] for p in data["positions"] if p["status"] == "open"
    )
    opened = 0
    for a in analyses:
        if a.price is None or not a.strategies:
            continue
        if a.liquidity is None or not a.liquidity.tradeable:
            continue
        if a.rating is None or not _grade_ok(a.rating.grade, min_grade):
            continue
        if not _event_pending(a.event_date, a.timing, today):
            continue
        s = a.strategies[0]
        if s.max_loss is None or s.max_loss <= 0:
            continue
        per_contract_risk = s.max_loss * 100.0
        qty = int(max_risk // per_contract_risk)
        if qty < 1:
            continue  # one contract already risks more than the cap
        key = _position_key(a.ticker, a.event_date.isoformat(), s.strategy)
        if key in known:
            continue
        risk_dollars = round(qty * per_contract_risk, 2)
        if open_risk + risk_dollars > data["balance"]:
            continue  # worst case must never exceed the balance
        known.add(key)
        open_risk += risk_dollars
        data["positions"].append(
            {
                "ticker": a.ticker,
                "event_date": a.event_date.isoformat(),
                "timing": a.timing,
                "strategy": s.strategy,
                "outlook": s.outlook,
                "qty": qty,
                "legs": [
                    {
                        "action": leg.action,
                        "option_type": leg.option_type,
                        "strike": leg.strike,
                        "expiry": leg.expiry.isoformat(),
                        "open_price": leg.price,
                    }
                    for leg in s.legs
                ],
                "net_premium": s.net_premium,
                "max_loss": s.max_loss,
                "risk_dollars": risk_dollars,
                "open_date": today.isoformat(),
                "open_spot": a.price,
                "grade": a.rating.grade,
                "status": "open",
                "close_date": None,
                "close_value": None,
                "close_method": None,
                "realized_pnl": None,
            }
        )
        opened += 1
        logger.info(
            "Paper: opened %s %s x%d (risk $%.0f)",
            a.ticker, s.strategy, qty, risk_dollars,
        )
    return opened


# --------------------------------------------------------------------------- #
# Closing
# --------------------------------------------------------------------------- #
def _intrinsic(option_type: str, strike: float, spot: float) -> float:
    if option_type == "call":
        return max(0.0, spot - strike)
    return max(0.0, strike - spot)


def _contract_mid(
    chain: Optional[OptionChain], option_type: str, strike: float
) -> Optional[float]:
    if chain is None:
        return None
    contracts = chain.calls if option_type == "call" else chain.puts
    for c in contracts:
        if abs(c.strike - strike) < 1e-3:
            return c.mid
    return None


def _spot_on_or_before(
    provider: MarketDataProvider, ticker: str, day: dt.date
) -> Optional[float]:
    bars = provider.price_history(ticker, days=30)
    closes = [b.close for b in bars if b.day <= day]
    return closes[-1] if closes else None


def _close_value(
    pos: dict, provider: MarketDataProvider, today: dt.date
) -> Optional[Tuple[float, str]]:
    """Net liquidation value per share, or None while any leg is unvaluable.

    Never mixes a real quote with a guess: either every leg gets a chain mid
    or an intrinsic settlement, or the whole position stays open.
    """
    chains: Dict[dt.date, Optional[OptionChain]] = {}
    value = 0.0
    method = "chain"
    for leg in pos["legs"]:
        expiry = dt.date.fromisoformat(leg["expiry"])
        if expiry not in chains:
            chains[expiry] = provider.option_chain(pos["ticker"], expiry)
        mark = _contract_mid(chains[expiry], leg["option_type"], leg["strike"])
        if mark is None:
            if expiry >= today:
                return None  # live contract without a quote — retry next run
            spot = _spot_on_or_before(provider, pos["ticker"], expiry)
            if spot is None:
                return None
            mark = _intrinsic(leg["option_type"], leg["strike"], spot)
            method = "intrinsic"
        value += mark if leg["action"] == "buy" else -mark
    return round(value, 4), method


def close_due_positions(
    data: dict, provider: MarketDataProvider, today: dt.date
) -> List[dict]:
    """Close open positions whose report has passed. Returns those closed."""
    closed: List[dict] = []
    for pos in data["positions"]:
        if pos["status"] != "open":
            continue
        event_date = dt.date.fromisoformat(pos["event_date"])
        if not _event_passed(event_date, pos.get("timing"), today):
            continue
        try:
            valued = _close_value(pos, provider, today)
        except Exception as exc:  # noqa: BLE001 - one ticker never kills the run
            logger.debug("Paper: could not value %s (%s)", pos["ticker"], exc)
            continue
        if valued is None:
            continue
        close_value, method = valued
        realized = round(
            (close_value + pos["net_premium"]) * 100.0 * pos["qty"], 2
        )
        pos["status"] = "closed"
        pos["close_date"] = today.isoformat()
        pos["close_value"] = close_value
        pos["close_method"] = method
        pos["realized_pnl"] = realized
        data["balance"] = round(data["balance"] + realized, 2)
        closed.append(pos)
        logger.info(
            "Paper: closed %s %s x%d for %+.2f (%s)",
            pos["ticker"], pos["strategy"], pos["qty"], realized, method,
        )
    return closed


def summarize_paper(data: dict, recent_n: int = 5) -> PaperSummary:
    positions = data.get("positions", [])
    open_pos = [p for p in positions if p["status"] == "open"]
    done = [p for p in positions if p["status"] == "closed"]
    done.sort(key=lambda p: (p["close_date"] or "", p["event_date"]))
    return PaperSummary(
        balance=data.get("balance", 0.0),
        start_balance=data.get("start_balance", 0.0),
        open_positions=open_pos,
        closed_total=len(done),
        wins=sum(1 for p in done if (p["realized_pnl"] or 0) > 0),
        recent=done[-recent_n:][::-1],
    )
