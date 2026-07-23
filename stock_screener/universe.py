"""A default universe of liquid, large-cap tickers to scan.

This is a starting watchlist, not a recommendation. Pass your own list via the
CLI (``--tickers`` or ``--tickers-file``) to scan anything you like.
"""

from __future__ import annotations

DEFAULT_UNIVERSE = [
    # Mega-cap tech
    "AAPL", "MSFT", "GOOGL", "AMZN", "META", "NVDA", "AVGO", "ADBE", "CRM",
    "ORCL", "AMD", "INTC", "QCOM", "TXN", "CSCO",
    # Consumer
    "COST", "WMT", "HD", "NKE", "SBUX", "MCD", "PEP", "KO", "PG", "DIS",
    # Financials
    "JPM", "V", "MA", "BAC", "GS", "AXP",
    # Healthcare
    "UNH", "JNJ", "LLY", "ABBV", "MRK", "PFE",
    # Industrials / other
    "CAT", "DE", "HON", "UPS", "GE",
]


def load_tickers_from_file(path: str) -> list[str]:
    """Read tickers from a file: one per line, or comma-separated. '#' comments ok."""
    tickers: list[str] = []
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.split("#", 1)[0].strip()
            if not line:
                continue
            for tok in line.replace(",", " ").split():
                tickers.append(tok.strip().upper())
    # De-dupe, preserve order.
    seen: set[str] = set()
    out: list[str] = []
    for t in tickers:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out
