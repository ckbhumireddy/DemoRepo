"""Retrieve the current list of S&P 500 constituents.

Primary source is the Wikipedia "List of S&P 500 companies" table, which is
kept current by the community. If the network fetch fails (offline, rate
limited, page structure change) we fall back to a bundled snapshot shipped in
``data/sp500_fallback.txt`` so the service still runs, just with a possibly
slightly stale roster.
"""

from __future__ import annotations

import logging
import os
from typing import List

logger = logging.getLogger(__name__)

WIKI_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"

_FALLBACK_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data",
    "sp500_fallback.txt",
)


def normalize_ticker(symbol: str) -> str:
    """Convert a symbol to the form Yahoo Finance expects.

    Yahoo uses a hyphen where the exchange uses a dot for share classes, e.g.
    ``BRK.B`` -> ``BRK-B`` and ``BF.B`` -> ``BF-B``.
    """
    return symbol.strip().upper().replace(".", "-")


def _fetch_from_wikipedia(timeout: int = 20) -> List[str]:
    import io

    import pandas as pd  # imported lazily so tests don't require pandas
    import requests

    # Wikipedia returns 403 to the default urllib user-agent that pandas uses,
    # so fetch the HTML ourselves with a browser-like UA and parse the text.
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (compatible; sp500-earnings-notifier/1.0; "
            "+https://github.com/)"
        )
    }
    resp = requests.get(WIKI_URL, headers=headers, timeout=timeout)
    resp.raise_for_status()

    tables = pd.read_html(io.StringIO(resp.text))
    # The first table on the page is the constituents list with a "Symbol"
    # column. Guard against layout changes by searching for the column.
    for table in tables:
        cols = {str(c).strip().lower(): c for c in table.columns}
        if "symbol" in cols:
            symbols = table[cols["symbol"]].astype(str).tolist()
            return [normalize_ticker(s) for s in symbols if s and s != "nan"]
    raise ValueError("No table with a 'Symbol' column found on the Wikipedia page")


def load_fallback(path: str = _FALLBACK_PATH) -> List[str]:
    """Load the bundled constituent snapshot (one ticker per line)."""
    with open(path, "r", encoding="utf-8") as fh:
        tickers = []
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            tickers.append(normalize_ticker(line))
    return tickers


def get_sp500_tickers(use_fallback_on_error: bool = True) -> List[str]:
    """Return the list of S&P 500 tickers, normalized for Yahoo Finance.

    Tries Wikipedia first; on any failure falls back to the bundled snapshot
    (unless ``use_fallback_on_error`` is False, in which case the error
    propagates).
    """
    try:
        tickers = _fetch_from_wikipedia()
        if tickers:
            logger.info("Loaded %d S&P 500 tickers from Wikipedia", len(tickers))
            return _dedupe(tickers)
        raise ValueError("Wikipedia returned an empty constituents list")
    except Exception as exc:  # noqa: BLE001 - broad on purpose, fall back gracefully
        if not use_fallback_on_error:
            raise
        logger.warning(
            "Falling back to bundled S&P 500 snapshot (Wikipedia fetch failed: %s)",
            exc,
        )
        tickers = load_fallback()
        logger.info("Loaded %d S&P 500 tickers from fallback snapshot", len(tickers))
        return _dedupe(tickers)


def _dedupe(tickers: List[str]) -> List[str]:
    """Preserve order while removing duplicates."""
    seen = set()
    out = []
    for t in tickers:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out
