"""The sweep: quote pass, then bars for the names that look interesting.

A full S&P 500 scan can't afford one history request per ticker — at
TradeStation's 120 requests/minute that is four-plus minutes of the rate
gate before any analysis starts. So the sweep runs in two stages:

1. **Quote pass.** ``/quotes`` takes 100 symbols per call, so the whole
   universe costs ~5 requests and yields today's volume, last price and day
   change for everything.
2. **Bar pass.** Only names whose quote volume is already running hot get a
   daily-history request, which is what the baseline, the z-score and both
   trend horizons need.

The stage-1 screen compares against the *prior session's* volume, which is a
single noisy sample — a quiet Monday makes an ordinary Tuesday look busy. It
is deliberately loose (``prefilter_multiple``, default 1.5x) because its only
job is to discard the obviously-normal majority; the real test is the median
baseline in stage 2. Portfolio holdings skip the screen entirely and always
get bars, since "nothing unusual in what you own" is itself worth saying.
"""

from __future__ import annotations

import concurrent.futures
import datetime as dt
import logging
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from earnings_analyzer.models import PriceBar
# The rate gate is generic request-pacing that already exists in the IV
# scanner; duplicating it here would mean two copies to keep honest.
from iv_scanner.scanner import RateGate

from .insights import Insight, classify
from .tradestation import QUOTE_BATCH_SIZE, Quote
from .trend import TrendView, compute_trend_view
from .volume import (
    DEFAULT_LOOKBACK,
    VolumeStats,
    compute_volume_stats,
    split_current_bar,
)

logger = logging.getLogger(__name__)

# Enough history for the 200-day average plus a full 52-week range.
BARS_BACK = 300

# TradeStation allows 120 market-data requests/minute; 0.55s between starts
# keeps a threaded sweep just under it.
RATE_INTERVAL = 0.55


@dataclass
class InsightRow:
    """One ticker's finished analysis, ready to render."""

    ticker: str
    stats: VolumeStats
    insight: Insight
    trend: Optional[TrendView] = None
    price: Optional[float] = None
    change_pct: Optional[float] = None
    held: bool = False

    @property
    def rvol(self) -> float:
        return self.stats.rvol


def batched(items: Sequence[str], size: int = QUOTE_BATCH_SIZE) -> List[List[str]]:
    return [list(items[i:i + size]) for i in range(0, len(items), size)]


def fetch_quotes(
    session, tickers: Sequence[str], gate: Optional[RateGate] = None
) -> Dict[str, Quote]:
    """Quote the whole universe in batches; a failed batch is not fatal."""
    quotes: Dict[str, Quote] = {}
    for batch in batched(tickers):
        if gate is not None:
            gate.wait()
        try:
            quotes.update(session.quotes(batch))
        except Exception as exc:  # noqa: BLE001
            logger.warning("quote batch of %d failed (%s)", len(batch), exc)
    return quotes


def prefilter(
    quotes: Dict[str, Quote],
    fraction: float,
    multiple: float,
    *,
    always: Optional[set] = None,
    min_price: float = 0.0,
    limit: int = 0,
) -> List[str]:
    """Names worth a history request: hot against yesterday, or held.

    ``fraction`` prorates the day so a mid-session scan compares like with
    like. When the prior session's volume is missing the name is kept — a
    missing denominator is not evidence of a quiet day.

    ``limit`` caps the bar pass, keeping the hottest names. On a broad
    risk-off day half the index can clear a 1.5x screen, and an uncapped
    stage 2 would spend ten minutes at the rate gate. Names in ``always``
    are exempt from both the screen and the cap.
    """
    always = always or set()
    forced = [t for t in sorted(always) if t in quotes]
    scored: List[Tuple[float, str]] = []
    for ticker, quote in quotes.items():
        if ticker in always:
            continue
        if min_price and (quote.last or 0.0) < min_price:
            continue
        if not quote.volume or quote.volume <= 0:
            continue
        projected = (
            quote.volume / max(fraction, 0.05) if fraction < 1.0 else quote.volume
        )
        previous = quote.previous_volume
        if not previous or previous <= 0:
            # Unknown baseline: keep it, but rank it below anything with
            # measured heat so the cap sheds these first.
            scored.append((multiple, ticker))
            continue
        ratio = projected / previous
        if ratio >= multiple:
            scored.append((ratio, ticker))

    scored.sort(key=lambda pair: (-pair[0], pair[1]))
    if limit and limit > 0:
        room = max(0, limit - len(forced))
        scored = scored[:room]
    return forced + [ticker for _, ticker in scored]


def analyze(
    ticker: str,
    bars: Sequence[PriceBar],
    *,
    partial: bool,
    fraction: float,
    quote: Optional[Quote] = None,
    lookback: int = DEFAULT_LOOKBACK,
) -> Optional[InsightRow]:
    """Pure assembly: bars (+ optional quote) -> a finished :class:`InsightRow`.

    The quote wins over the last bar for price, volume and day change: on a
    live session it is fresher than the forming bar.
    """
    history, current = split_current_bar(bars, partial)
    if current is None:
        return None

    volume = current.volume
    price = current.close
    change_pct = None
    if quote is not None:
        volume = quote.volume or volume
        price = quote.last or price
        change_pct = quote.change_pct
    if change_pct is None and history:
        previous_close = history[-1].close
        if previous_close > 0 and price:
            change_pct = (price - previous_close) / previous_close

    stats = compute_volume_stats(
        ticker,
        history,
        volume,
        price,
        fraction=fraction,
        partial=partial,
        lookback=lookback,
    )
    if stats is None:
        return None

    # The trend reads the completed sessions plus wherever the stock is
    # trading right now, so a big move today is visible to the short horizon.
    trend_bars = list(history)
    if price and price > 0:
        trend_bars.append(
            PriceBar(
                day=current.day,
                open=current.open or price,
                high=max(current.high or price, price),
                low=min(current.low or price, price),
                close=price,
                volume=volume or 0.0,
            )
        )
    trend = compute_trend_view(trend_bars)

    return InsightRow(
        ticker=ticker,
        stats=stats,
        insight=classify(stats, trend, change_pct),
        trend=trend,
        price=price,
        change_pct=change_pct,
    )


def make_tradestation_fetch(session, gate: RateGate, fraction: float, quotes: Dict[str, Quote]):
    def fetch(ticker: str) -> Optional[InsightRow]:
        gate.wait()
        bars, partial = session.bars(ticker, BARS_BACK)
        return analyze(
            ticker,
            bars,
            partial=partial,
            fraction=fraction,
            quote=quotes.get(ticker),
        )

    return fetch


def make_yahoo_fetch(provider, today: dt.date, fraction: float):
    """Degraded path: Yahoo history, with today's bar treated as the live one."""

    def fetch(ticker: str) -> Optional[InsightRow]:
        bars = provider.price_history(ticker, days=BARS_BACK + 100)
        if not bars:
            return None
        partial = fraction < 1.0 and max(b.day for b in bars) >= today
        return analyze(ticker, bars, partial=partial, fraction=fraction)

    return fetch


def sweep(
    tickers: Sequence[str],
    fetch: Callable[[str], Optional[InsightRow]],
    max_workers: int = 4,
) -> Tuple[List[InsightRow], int]:
    """Run ``fetch`` across the candidates; one bad ticker never kills a run."""
    rows: List[InsightRow] = []
    failures = 0

    def _one(ticker: str) -> Optional[InsightRow]:
        try:
            return fetch(ticker)
        except Exception as exc:  # noqa: BLE001
            logger.debug("analysis failed for %s (%s)", ticker, type(exc).__name__)
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
    logger.info(
        "Analyzed %d candidate(s): %d usable, %d skipped/failed",
        len(tickers), len(rows), failures,
    )
    return rows, failures


def select_unusual(
    rows: Sequence[InsightRow],
    *,
    min_rvol: float,
    min_dollar_volume: float,
    min_price: float,
    top_n: int,
) -> List[InsightRow]:
    """The unusual names, most unusual first, after liquidity screening.

    The dollar-volume and price floors matter more than they look: a $2
    stock printing 8x its usual 40k shares is not a market insight, and
    without a floor those names crowd out everything else.
    """
    keep = [
        r for r in rows
        if r.rvol >= min_rvol
        and (r.price or 0.0) >= min_price
        and r.stats.dollar_volume >= min_dollar_volume
    ]
    return sorted(keep, key=lambda r: -r.rvol)[:top_n]


def annotate(rows: Sequence[InsightRow], held: set) -> List[InsightRow]:
    for row in rows:
        row.held = row.ticker in held
    return list(rows)
