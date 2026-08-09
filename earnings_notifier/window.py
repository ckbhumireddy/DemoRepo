"""Hand-off file between the calendar notifier and downstream services.

The notifier writes the full notification window (pre-deduplication) to a
small JSON file each run; the earnings analyzer consumes it so it doesn't
have to re-scan 500+ tickers to learn who reports this week.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import os
from typing import List, Optional

from .earnings import EarningsEvent

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1


def write_window_file(
    path: str,
    events: List[EarningsEvent],
    today: dt.date,
    lead_days: int,
) -> None:
    """Write the in-window events to ``path`` (creating directories)."""
    payload = {
        "version": SCHEMA_VERSION,
        "today": today.isoformat(),
        "lead_days": lead_days,
        "events": [
            {
                "ticker": e.ticker,
                "date": e.date.isoformat(),
                "is_estimate": e.is_estimate,
            }
            for e in events
        ],
    }
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)
    logger.info("Wrote %d window event(s) to %s", len(events), path)


def read_window_file(path: str) -> Optional[dict]:
    """Load a window file. Missing/malformed/unknown-version -> ``None``."""
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Could not read window file %s (%s)", path, exc)
        return None
    if not isinstance(data, dict) or data.get("version") != SCHEMA_VERSION:
        logger.warning("Window file %s has unexpected format; ignoring", path)
        return None
    try:
        events = [
            {
                "ticker": str(e["ticker"]),
                "date": dt.date.fromisoformat(e["date"]),
                "is_estimate": bool(e.get("is_estimate", True)),
            }
            for e in data.get("events", [])
        ]
    except (KeyError, TypeError, ValueError) as exc:
        logger.warning("Window file %s has malformed events (%s)", path, exc)
        return None
    return {
        "today": dt.date.fromisoformat(data["today"]),
        "lead_days": int(data["lead_days"]),
        "events": events,
    }
