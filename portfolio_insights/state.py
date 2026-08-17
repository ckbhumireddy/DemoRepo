"""Send-dedupe state for the insights service (tiny JSON file).

Keeps holdings out of state: only dates, tickers of already-sent alerts,
and the last portfolio value for continuity.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import os
from typing import List

logger = logging.getLogger(__name__)

ALERT_RETENTION_DAYS = 7


def load_state(path: str) -> dict:
    if path and os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict):
                return data
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Could not read state %s (%s); starting fresh",
                           path, exc)
    return {}


def save_state(path: str, state: dict, today: dt.date) -> None:
    cutoff = (today - dt.timedelta(days=ALERT_RETENTION_DAYS)).isoformat()
    state["alerts_sent"] = [
        a for a in state.get("alerts_sent", []) if a.get("date", "") >= cutoff
    ]
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=2)


def alert_key(date: dt.date, ticker: str, kind: str) -> dict:
    return {"date": date.isoformat(), "ticker": ticker, "trigger": kind}


def already_alerted(state: dict, key: dict) -> bool:
    return key in state.get("alerts_sent", [])


def record_alerts(state: dict, keys: List[dict]) -> None:
    state.setdefault("alerts_sent", []).extend(keys)
