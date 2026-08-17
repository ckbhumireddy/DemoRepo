"""Batch quotes: Schwab market data first, Yahoo fallback per ticker."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

from earnings_analyzer.schwab import schwab_symbol

logger = logging.getLogger(__name__)

BENCHMARK = "SPY"


@dataclass(frozen=True)
class Quote:
    ticker: str
    last: float
    prev_close: Optional[float] = None
    day_change_pct: Optional[float] = None


def _as_float(value) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return None if f != f else f


def quotes_from_payload(payload: dict) -> Dict[str, Quote]:
    """Parse a Schwab ``/quotes`` response (symbol -> {"quote": {...}})."""
    out: Dict[str, Quote] = {}
    for symbol, entry in (payload or {}).items():
        quote = (entry or {}).get("quote") or {}
        last = _as_float(quote.get("lastPrice"))
        if last is None or last <= 0:
            continue
        prev = _as_float(quote.get("closePrice"))
        pct = _as_float(quote.get("netPercentChange"))
        if pct is None and prev and prev > 0:
            pct = (last - prev) / prev * 100.0
        ticker = str(symbol).replace("/", "-")
        out[ticker] = Quote(
            ticker=ticker, last=last, prev_close=prev, day_change_pct=pct
        )
    return out


def _yahoo_quote(ticker: str) -> Optional[Quote]:
    import yfinance as yf

    try:
        fast = yf.Ticker(ticker).fast_info
        last = _as_float(getattr(fast, "last_price", None))
        prev = _as_float(getattr(fast, "previous_close", None))
    except Exception:  # noqa: BLE001
        return None
    if last is None or last <= 0:
        return None
    pct = (last - prev) / prev * 100.0 if prev and prev > 0 else None
    return Quote(ticker=ticker, last=last, prev_close=prev, day_change_pct=pct)


def fetch_quotes(
    session,
    tickers: List[str],
    fallback: Optional[Callable[[str], Optional[Quote]]] = None,
) -> Dict[str, Quote]:
    """Quotes for ``tickers`` + the SPY benchmark.

    One batch Schwab call when a session exists; any ticker it misses (or
    everything, when Schwab is unavailable) goes through the per-ticker
    fallback (Yahoo by default).
    """
    fallback = fallback or _yahoo_quote
    wanted = list(dict.fromkeys(list(tickers) + [BENCHMARK]))
    quotes: Dict[str, Quote] = {}
    if session is not None:
        try:
            payload = session.get(
                "/quotes",
                {"symbols": ",".join(schwab_symbol(t) for t in wanted)},
            )
            quotes = quotes_from_payload(payload)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Schwab quotes failed (%s); using fallback", type(exc).__name__
            )
    for ticker in wanted:
        if ticker not in quotes:
            quote = fallback(ticker)
            if quote is not None:
                quotes[ticker] = quote
    missing = len(wanted) - len(quotes)
    if missing:
        logger.info("%d ticker(s) have no quote this run", missing)
    return quotes
