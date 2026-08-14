"""The trader's position ledger (JSON, persisted via the workflow cache).

Sign convention (all money per share; x100 x qty for dollars): the value of
a set of legs is ``sum(+mid for buys, -mid for sells)`` — the net amount
received to liquidate. At open this equals ``-net_premium``, so realized
P&L per share is ``close_value + net_premium``.

Schema v2 (vs the retired in-sheet paper trader's v1): drops the rating
grade, adds ``opened_at``/``opened_phase``/``closed_at`` timestamps and a
``signals`` block recording why the trade was taken. A version bump starts
a fresh ledger (the balance resets) — acceptable for a paper account.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from earnings_analyzer.models import OptionChain
from earnings_analyzer.provider import MarketDataProvider

from .pipeline import TradeDecision

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 2
RETENTION_DAYS = 365


@dataclass
class TraderSummary:
    balance: float
    start_balance: float
    open_positions: List[dict] = field(default_factory=list)
    closed_this_run: List[dict] = field(default_factory=list)
    window_days: int = 30
    window_closed: int = 0
    window_wins: int = 0
    window_pnl: float = 0.0
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
    def window_win_rate(self) -> Optional[float]:
        return self.window_wins / self.window_closed if self.window_closed else None

    @property
    def open_risk(self) -> float:
        return sum(p["risk_dollars"] for p in self.open_positions)


def _position_key(ticker: str, event_date: str, strategy: str) -> str:
    return f"{ticker}:{event_date}:{strategy}"


def load_ledger(path: str, start_balance: float) -> dict:
    """Load the ledger. Missing/malformed -> a fresh one at start_balance."""
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
        "positions": [],
    }


def save_ledger(path: str, data: dict, today: dt.date) -> None:
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
def _event_passed(
    event_date: dt.date, timing: Optional[str], today: dt.date
) -> bool:
    """Closeable: the report happened."""
    if event_date < today:
        return True
    return event_date == today and timing == "pre-market"


# --------------------------------------------------------------------------- #
# Opening
# --------------------------------------------------------------------------- #
def open_from_decisions(
    data: dict,
    decisions: List[TradeDecision],
    today: dt.date,
    now_utc: dt.datetime,
    *,
    max_risk: float = 5000.0,
) -> List[dict]:
    """Open positions from "open" decisions. Returns the positions opened.

    When capital is scarce the biggest mispricing gets it first: decisions
    are taken in order of |implied/historical ratio - 1|, descending.
    """
    known = {
        _position_key(p["ticker"], p["event_date"], p["strategy"])
        for p in data["positions"]
    }
    open_risk = sum(
        p["risk_dollars"] for p in data["positions"] if p["status"] == "open"
    )
    openable = [d for d in decisions if d.action == "open" and d.strategy]
    openable.sort(
        key=lambda d: abs((d.implied.ratio or 1.0) - 1.0) if d.implied else 0.0,
        reverse=True,
    )
    opened: List[dict] = []
    for d in openable:
        s = d.strategy
        if s.max_loss is None or s.max_loss <= 0:
            continue
        per_contract_risk = s.max_loss * 100.0
        qty = int(max_risk // per_contract_risk)
        if qty < 1:
            continue  # one contract already risks more than the cap
        key = _position_key(
            d.candidate.ticker, d.candidate.event_date.isoformat(), s.strategy
        )
        if key in known:
            continue
        risk_dollars = round(qty * per_contract_risk, 2)
        if open_risk + risk_dollars > data["balance"]:
            continue  # worst case must never exceed the balance
        known.add(key)
        open_risk += risk_dollars
        pos = {
            "ticker": d.candidate.ticker,
            "event_date": d.candidate.event_date.isoformat(),
            "timing": d.candidate.timing,
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
            "open_spot": d.spot,
            "opened_at": now_utc.isoformat(timespec="seconds"),
            "opened_phase": "entry",
            "signals": {
                "verdict": d.implied.verdict if d.implied else None,
                "bias": d.trend.bias if d.trend else "neutral",
                "implied_move_pct": d.implied.implied_move_pct if d.implied else None,
                "ratio": d.implied.ratio if d.implied else None,
                "atm_spread_pct": d.liquidity.atm_spread_pct if d.liquidity else None,
            },
            "status": "open",
            "close_date": None,
            "closed_at": None,
            "close_value": None,
            "close_method": None,
            "realized_pnl": None,
        }
        data["positions"].append(pos)
        opened.append(pos)
        logger.info(
            "Trader: opened %s %s x%d (risk $%.0f)",
            pos["ticker"], pos["strategy"], qty, risk_dollars,
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
    data: dict,
    provider: MarketDataProvider,
    today: dt.date,
    now_utc: dt.datetime,
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
            logger.debug("Trader: could not value %s (%s)", pos["ticker"], exc)
            continue
        if valued is None:
            continue
        close_value, method = valued
        realized = round(
            (close_value + pos["net_premium"]) * 100.0 * pos["qty"], 2
        )
        pos["status"] = "closed"
        pos["close_date"] = today.isoformat()
        pos["closed_at"] = now_utc.isoformat(timespec="seconds")
        pos["close_value"] = close_value
        pos["close_method"] = method
        pos["realized_pnl"] = realized
        data["balance"] = round(data["balance"] + realized, 2)
        closed.append(pos)
        logger.info(
            "Trader: closed %s %s x%d for %+.2f (%s)",
            pos["ticker"], pos["strategy"], pos["qty"], realized, method,
        )
    return closed


def summarize(
    data: dict, today: dt.date, *, window_days: int = 30, recent_n: int = 5
) -> TraderSummary:
    positions = data.get("positions", [])
    open_pos = [p for p in positions if p["status"] == "open"]
    done = [p for p in positions if p["status"] == "closed"]
    done.sort(key=lambda p: (p["close_date"] or "", p["event_date"]))
    window_start = (today - dt.timedelta(days=window_days)).isoformat()
    in_window = [p for p in done if (p["close_date"] or "") >= window_start]
    return TraderSummary(
        balance=data.get("balance", 0.0),
        start_balance=data.get("start_balance", 0.0),
        open_positions=open_pos,
        window_days=window_days,
        window_closed=len(in_window),
        window_wins=sum(1 for p in in_window if (p["realized_pnl"] or 0) > 0),
        window_pnl=round(sum(p["realized_pnl"] or 0 for p in in_window), 2),
        closed_total=len(done),
        wins=sum(1 for p in done if (p["realized_pnl"] or 0) > 0),
        recent=done[-recent_n:][::-1],
    )
