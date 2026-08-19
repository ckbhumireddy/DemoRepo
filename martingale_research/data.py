"""SPX daily closes since 1990, cached to a CSV under state/."""

from __future__ import annotations

import csv
import datetime as dt
import os
from typing import List, Tuple

CACHE_FILE = "state/spx_closes.csv"
START = "1990-01-01"


def closes(cache_file: str = CACHE_FILE) -> List[Tuple[dt.date, float]]:
    """All SPX daily closes since 1990 (Yahoo; cached after first fetch)."""
    if cache_file and os.path.exists(cache_file):
        with open(cache_file, newline="") as fh:
            return [(dt.date.fromisoformat(d), float(c))
                    for d, c in csv.reader(fh)]
    import yfinance as yf

    frame = yf.Ticker("^GSPC").history(start=START, interval="1d",
                                       auto_adjust=True)
    data = [(d.date(), float(c)) for d, c in frame["Close"].items() if c > 0]
    if not data:
        raise RuntimeError("Yahoo returned no SPX history")
    if cache_file:
        directory = os.path.dirname(cache_file)
        if directory:
            os.makedirs(directory, exist_ok=True)
        with open(cache_file, "w", newline="") as fh:
            csv.writer(fh).writerows((d.isoformat(), c) for d, c in data)
    return data
