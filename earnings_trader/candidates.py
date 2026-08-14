"""Candidate discovery: window file -> what could be traded at today's close.

The entry run happens shortly before the market close, so the tradeable
events are tonight's after-market reports and tomorrow's pre-market
reports. Events with an unknown report time are surfaced in the plan email
but never traded — entering blind into an already-released report is worse
than missing a trade.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import List, Optional, Tuple


@dataclass(frozen=True)
class Candidate:
    ticker: str
    event_date: dt.date
    timing: str            # "pre-market" | "after-market"
    session: str           # "today-amc" | "tomorrow-bmo"


@dataclass(frozen=True)
class SkippedCandidate:
    ticker: str
    event_date: dt.date
    timing: Optional[str]
    reason: str


def next_trading_day(day: dt.date) -> dt.date:
    """The next weekday after ``day``. Market holidays are out of scope."""
    nxt = day + dt.timedelta(days=1)
    while nxt.weekday() >= 5:  # 5 = Saturday, 6 = Sunday
        nxt += dt.timedelta(days=1)
    return nxt


def find_candidates(
    window: Optional[dict], today: dt.date
) -> Tuple[List[Candidate], List[SkippedCandidate]]:
    """Split the window's events into tradeable candidates and skips.

    Tradeable: reports after today's close ("today-amc") or before the next
    trading day's open ("tomorrow-bmo"). Events at those dates without a
    known report time become skips; everything else is out of scope for
    this run and silently ignored.
    """
    if window is None:
        return [], []
    tomorrow = next_trading_day(today)
    candidates: List[Candidate] = []
    skipped: List[SkippedCandidate] = []
    for e in window.get("events", []):
        ticker, date, timing = e["ticker"], e["date"], e.get("timing")
        if date not in (today, tomorrow):
            continue
        if date == today and timing == "after-market":
            candidates.append(Candidate(ticker, date, timing, "today-amc"))
        elif date == tomorrow and timing == "pre-market":
            candidates.append(Candidate(ticker, date, timing, "tomorrow-bmo"))
        elif timing is None:
            skipped.append(
                SkippedCandidate(ticker, date, timing, "unknown report time")
            )
        # today-BMO already reported; tomorrow-AMC is tomorrow's entry run.
    candidates.sort(key=lambda c: (c.event_date, c.ticker))
    skipped.sort(key=lambda s: (s.event_date, s.ticker))
    return candidates, skipped
