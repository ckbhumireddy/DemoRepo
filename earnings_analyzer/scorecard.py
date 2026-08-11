"""Suggestion log + outcome grading: the feedback loop for the trade sheet.

Every real run appends its strategy suggestions to a small JSON file. When a
suggestion's earnings report is in the past, the next run grades it against
the actual post-earnings move, with the same win test the backtest uses.
The email then reports the running totals, so the rating system is measured
against reality instead of trusted on faith.

The file lives under ``state/`` so the existing workflow cache persists it.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .backtest import is_win
from .history import reactions_from_bars
from .models import OptionLeg, PricedStrategy, TickerAnalysis
from .provider import MarketDataProvider

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1
RETENTION_DAYS = 365
# Grade a suggestion when at least one full day has passed after the report,
# so a close after the event exists.
GRADE_LAG_DAYS = 2


@dataclass
class ScorecardSummary:
    graded: int
    wins: int
    pending: int
    recent: List[dict] = field(default_factory=list)   # last graded entries

    @property
    def losses(self) -> int:
        return self.graded - self.wins

    @property
    def win_rate(self) -> Optional[float]:
        return self.wins / self.graded if self.graded else None


def _entry_key(ticker: str, event_date: str, strategy: str) -> str:
    return f"{ticker}:{event_date}:{strategy}"


def load_scorecard(path: str) -> dict:
    """Load the scorecard file. Missing/malformed -> a fresh empty one."""
    if path and os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict) and data.get("version") == SCHEMA_VERSION:
                return data
            logger.warning("Scorecard %s has unexpected format; starting fresh", path)
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Could not read scorecard %s (%s); starting fresh", path, exc)
    return {"version": SCHEMA_VERSION, "entries": []}


def save_scorecard(path: str, data: dict, today: dt.date) -> None:
    """Write the scorecard, pruning entries older than the retention window."""
    cutoff = (today - dt.timedelta(days=RETENTION_DAYS)).isoformat()
    data["entries"] = [e for e in data["entries"] if e["event_date"] >= cutoff]
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)


def record_suggestions(
    data: dict, analyses: List[TickerAnalysis], today: dt.date
) -> int:
    """Append today's strategy suggestions; skip ones already recorded."""
    known = {
        _entry_key(e["ticker"], e["event_date"], e["strategy"])
        for e in data["entries"]
    }
    added = 0
    for a in analyses:
        for s in a.strategies:
            if a.price is None:
                continue
            key = _entry_key(a.ticker, a.event_date.isoformat(), s.strategy)
            if key in known:
                continue
            known.add(key)
            data["entries"].append(
                {
                    "ticker": a.ticker,
                    "event_date": a.event_date.isoformat(),
                    "suggested_on": today.isoformat(),
                    "strategy": s.strategy,
                    "outlook": s.outlook,
                    "spot": a.price,
                    "net_premium": s.net_premium,
                    "breakevens": s.breakevens,
                    "leg_strikes": [l.strike for l in s.legs],
                    "grade": a.rating.grade if a.rating else None,
                    "outcome": None,
                    "actual_move_pct": None,
                    "graded_on": None,
                }
            )
            added += 1
    if added:
        logger.info("Scorecard: recorded %d new suggestion(s)", added)
    return added


def _reconstruct(entry: dict) -> PricedStrategy:
    """A minimal PricedStrategy carrying what the win test needs."""
    expiry = dt.date.fromisoformat(entry["event_date"])
    return PricedStrategy(
        strategy=entry["strategy"],
        outlook=entry.get("outlook", ""),
        legs=[
            OptionLeg(action="", option_type="", strike=k, expiry=expiry, price=0.0)
            for k in entry.get("leg_strikes", [])
        ],
        net_premium=entry.get("net_premium", 0.0),
        breakevens=entry.get("breakevens", []),
    )


def grade_pending(data: dict, provider: MarketDataProvider, today: dt.date) -> int:
    """Grade suggestions whose report is far enough in the past."""
    cutoff = today - dt.timedelta(days=GRADE_LAG_DAYS)
    pending: Dict[str, List[dict]] = {}
    for e in data["entries"]:
        if e["outcome"] is None and dt.date.fromisoformat(e["event_date"]) <= cutoff:
            pending.setdefault(e["ticker"], []).append(e)
    if not pending:
        return 0

    graded = 0
    for ticker, entries in pending.items():
        try:
            bars = provider.price_history(ticker, days=60)
        except Exception as exc:  # noqa: BLE001
            logger.debug("Scorecard: no price history for %s (%s)", ticker, exc)
            continue
        event_dates = {dt.date.fromisoformat(e["event_date"]) for e in entries}
        moves = reactions_from_bars(bars, sorted(event_dates))
        for e in entries:
            move = moves.get(dt.date.fromisoformat(e["event_date"]))
            if move is None:
                continue
            won = is_win(_reconstruct(e), e["spot"], move)
            if won is None:
                continue
            e["outcome"] = "win" if won else "loss"
            e["actual_move_pct"] = round(move, 2)
            e["graded_on"] = today.isoformat()
            graded += 1
    if graded:
        logger.info("Scorecard: graded %d suggestion(s)", graded)
    return graded


def summarize(data: dict, recent_n: int = 5) -> ScorecardSummary:
    entries = data.get("entries", [])
    done = [e for e in entries if e["outcome"] is not None]
    done.sort(key=lambda e: (e["graded_on"] or "", e["event_date"]))
    return ScorecardSummary(
        graded=len(done),
        wins=sum(1 for e in done if e["outcome"] == "win"),
        pending=sum(1 for e in entries if e["outcome"] is None),
        recent=done[-recent_n:][::-1],
    )
