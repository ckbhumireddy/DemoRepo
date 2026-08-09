"""Persisted "already notified" state so each earnings is emailed only once.

The state is a small JSON file listing ``TICKER:YYYY-MM-DD`` keys that have
already been sent. Because an earnings date stays inside the look-ahead window
for several days, this file is what prevents a daily job from re-emailing the
same event every day. Old keys are pruned so the file doesn't grow forever.

In GitHub Actions the file is persisted between runs via ``actions/cache``
(see the workflow); locally it just lives on disk.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import os
from typing import Iterable, Set

from .earnings import EarningsEvent

logger = logging.getLogger(__name__)


def event_key(event: EarningsEvent) -> str:
    """Stable identity for an earnings event: ticker + ISO date."""
    return f"{event.ticker}:{event.date.isoformat()}"


def load_state(path: str) -> Set[str]:
    """Load the set of already-notified keys. Missing/unreadable file -> empty."""
    if not path or not os.path.exists(path):
        return set()
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return set(data.get("notified", []))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Could not read state file %s (%s); starting empty", path, exc)
        return set()


def _key_date(key: str) -> dt.date:
    """Parse the date out of a ``TICKER:YYYY-MM-DD`` key (raises on garbage)."""
    return dt.date.fromisoformat(key.rsplit(":", 1)[1])


def prune(keys: Iterable[str], today: dt.date, retention_days: int) -> Set[str]:
    """Drop keys whose earnings date is older than ``retention_days`` ago."""
    cutoff = today - dt.timedelta(days=retention_days)
    kept = set()
    for key in keys:
        try:
            if _key_date(key) >= cutoff:
                kept.add(key)
        except (ValueError, IndexError):
            # Malformed key — drop it rather than let it break the run.
            continue
    return kept


def save_state(
    path: str,
    keys: Iterable[str],
    today: dt.date,
    retention_days: int = 120,
) -> None:
    """Write the (pruned) set of notified keys to ``path`` (creating dirs)."""
    kept = sorted(prune(keys, today, retention_days))
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"notified": kept, "updated": today.isoformat()}, fh, indent=2)
    logger.info("Saved %d notified key(s) to %s", len(kept), path)
