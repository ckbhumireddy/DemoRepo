"""The scan itself: per-ticker IV rank, paced for API limits.

The Schwab path uses two requests per ticker (price history + one chains
call covering 7-45 DTE) through a shared rate gate that keeps the whole
sweep near 100 requests/minute — a full S&P 500 scan takes ~9 minutes.
Yahoo is used only for small degraded universes; a 500-ticker Yahoo sweep
would be rate-limited into uselessness.
"""

from __future__ import annotations

import concurrent.futures
import datetime as dt
import logging
import threading
import time
from dataclasses import dataclass
from typing import Callable, List, Optional, Tuple

from earnings_analyzer.models import IVRank
from earnings_analyzer.schwab import (
    bars_from_candles,
    chains_from_payload,
    schwab_symbol,
)
from earnings_analyzer.volatility import compute_iv_rank

logger = logging.getLogger(__name__)

MIN_DTE = 7          # skip the nearest expiry — its IV sits at a structural low
TARGET_DTE = 30      # the IV30 convention
MAX_DTE = 45
MIN_ATM_OI = 50      # below this the chain barely trades; its IV is fiction
# Cash-like instruments (T-bill ETFs, money-market proxies) have a realized
# vol near zero, so ANY option IV ranks 100 — the rank is meaningless there.
MIN_HV_HIGH = 0.05


@dataclass
class ScanRow:
    rank: IVRank
    price: Optional[float] = None
    expiry: Optional[dt.date] = None
    held: bool = False
    earnings_date: Optional[dt.date] = None
    earnings_timing: Optional[str] = None

    @property
    def ticker(self) -> str:
        return self.rank.ticker


class RateGate:
    """Serializes request starts so the sweep stays under the API limit."""

    def __init__(self, min_interval: float = 0.55) -> None:
        self._lock = threading.Lock()
        self._next = 0.0
        self.min_interval = min_interval

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            delay = self._next - now
            self._next = max(now, self._next) + self.min_interval
        if delay > 0:
            time.sleep(delay)


def pick_expiry(expiries: List[dt.date], today: dt.date) -> Optional[dt.date]:
    usable = [e for e in expiries if (e - today).days >= MIN_DTE]
    if not usable:
        return None
    return min(usable, key=lambda e: abs((e - today).days - TARGET_DTE))


def row_from_chain_and_bars(
    ticker: str, chains: dict, bars, today: dt.date
) -> Optional[ScanRow]:
    """Pure assembly: chain map + price bars -> a ScanRow (or None)."""
    expiry = pick_expiry(sorted(chains), today)
    if expiry is None:
        return None
    chain = chains[expiry]
    atm = [c for c in (chain.atm_call(), chain.atm_put()) if c is not None]
    oi = [c.open_interest for c in atm if c.open_interest]
    if not oi or max(oi) < MIN_ATM_OI:
        return None
    rank = compute_iv_rank(ticker, chain.atm_iv(), bars)
    if rank is None or rank.hv_high < MIN_HV_HIGH:
        return None
    price = chain.underlying_price or (bars[-1].close if bars else None)
    return ScanRow(rank=rank, price=price, expiry=expiry)


def make_schwab_fetch(session, gate: RateGate, today: dt.date):
    def fetch(ticker: str) -> Optional[ScanRow]:
        gate.wait()
        history = session.get(
            "/pricehistory",
            {
                "symbol": schwab_symbol(ticker),
                "periodType": "year",
                "period": 2,
                "frequencyType": "daily",
                "frequency": 1,
            },
        )
        bars = bars_from_candles(history)
        gate.wait()
        payload = session.get(
            "/chains",
            {
                "symbol": schwab_symbol(ticker),
                "contractType": "ALL",
                "strategy": "SINGLE",
                "fromDate": (today + dt.timedelta(days=MIN_DTE)).isoformat(),
                "toDate": (today + dt.timedelta(days=MAX_DTE)).isoformat(),
            },
        )
        chains = chains_from_payload(payload, ticker)
        return row_from_chain_and_bars(ticker, chains, bars, today)

    return fetch


def make_yahoo_fetch(provider, today: dt.date):
    def fetch(ticker: str) -> Optional[ScanRow]:
        bars = provider.price_history(ticker, days=380)
        expiry = pick_expiry(provider.option_expiries(ticker), today)
        if expiry is None:
            return None
        chain = provider.option_chain(ticker, expiry)
        if chain is None:
            return None
        return row_from_chain_and_bars(ticker, {expiry: chain}, bars, today)

    return fetch


def scan(
    tickers: List[str],
    fetch: Callable[[str], Optional[ScanRow]],
    max_workers: int = 4,
) -> Tuple[List[ScanRow], int]:
    """Run ``fetch`` across the universe; one bad ticker never kills a scan."""
    rows: List[ScanRow] = []
    failures = 0

    def _one(ticker: str) -> Optional[ScanRow]:
        try:
            return fetch(ticker)
        except Exception as exc:  # noqa: BLE001
            logger.debug("scan failed for %s (%s)", ticker, type(exc).__name__)
            return None

    if not tickers:
        return rows, failures
    workers = max(1, min(max_workers, len(tickers)))
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        for result in pool.map(_one, tickers):
            if result is None:
                failures += 1
            else:
                rows.append(result)
    logger.info("Scanned %d ticker(s): %d ranked, %d skipped/failed",
                len(tickers), len(rows), failures)
    return rows, failures


def select_top(rows: List[ScanRow], n: int) -> List[ScanRow]:
    return sorted(rows, key=lambda r: -r.rank.iv_rank)[:n]


def annotate(
    rows: List[ScanRow],
    held_tickers: set,
    window: Optional[dict],
) -> List[ScanRow]:
    """Mark portfolio holdings and attach earnings dates from the window."""
    events = {}
    if window:
        for e in window.get("events", []):
            events[e["ticker"]] = (e["date"], e.get("timing"))
    for r in rows:
        r.held = r.ticker in held_tickers
        if r.ticker in events:
            r.earnings_date, r.earnings_timing = events[r.ticker]
    return rows
