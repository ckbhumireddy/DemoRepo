"""A one-command check that the TradeStation feed matches our parsers.

The payload parsers in :mod:`market_insights.tradestation` are written to
TradeStation's documented shapes and covered by unit tests, but no test can
prove the live feed agrees — field names, stringly-typed numbers and the
BarStatus flag are all things a vendor can change. This runs the two calls
the scan depends on against one symbol and prints what came back, so a
mismatch shows up as a readable report instead of an empty email.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class FeedCheck:
    """What the two market-data calls returned for one symbol."""

    symbol: str
    quote_ok: bool = False
    bars_ok: bool = False
    last: Optional[float] = None
    volume: Optional[float] = None
    previous_volume: Optional[float] = None
    change_pct: Optional[float] = None
    bar_count: int = 0
    first_day: Optional[dt.date] = None
    last_day: Optional[dt.date] = None
    last_close: Optional[float] = None
    last_volume: Optional[float] = None
    session_open: bool = False
    errors: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.quote_ok and self.bars_ok and not self.errors


def check_feed(session, symbol: str, barsback: int = 30) -> FeedCheck:
    """Probe quotes and barcharts for ``symbol``; never raises."""
    check = FeedCheck(symbol=symbol)

    try:
        quotes = session.quotes([symbol])
        quote = quotes.get(symbol)
        if quote is None:
            check.errors.append(
                f"quotes returned no row for {symbol} "
                f"(got: {sorted(quotes) or 'nothing'})"
            )
        else:
            check.quote_ok = True
            check.last = quote.last
            check.volume = quote.volume
            check.previous_volume = quote.previous_volume
            check.change_pct = quote.change_pct
            if quote.last is None:
                check.errors.append("quote parsed but Last was empty")
            if quote.volume is None:
                check.errors.append("quote parsed but Volume was empty")
    except Exception as exc:  # noqa: BLE001
        check.errors.append(f"quotes call failed: {type(exc).__name__}: {exc}")

    try:
        bars, partial = session.bars(symbol, barsback)
        check.bar_count = len(bars)
        check.session_open = partial
        if not bars:
            check.errors.append("barcharts returned no usable bars")
        else:
            check.bars_ok = True
            check.first_day = bars[0].day
            check.last_day = bars[-1].day
            check.last_close = bars[-1].close
            check.last_volume = bars[-1].volume
            if len(bars) < barsback / 2:
                check.errors.append(
                    f"expected ~{barsback} bars, parsed only {len(bars)} — "
                    "some rows were dropped as unusable"
                )
    except Exception as exc:  # noqa: BLE001
        check.errors.append(f"barcharts call failed: {type(exc).__name__}: {exc}")

    return check


def format_check(check: FeedCheck) -> str:
    """Render a :class:`FeedCheck` as a human-readable report."""
    lines = [f"TradeStation feed check — {check.symbol}", ""]

    if check.quote_ok:
        lines.append("  quotes      OK")
        lines.append(f"    last            {check.last}")
        lines.append(f"    volume today    {check.volume:,.0f}"
                     if check.volume else "    volume today    (empty)")
        lines.append(
            f"    prior volume    {check.previous_volume:,.0f}"
            if check.previous_volume else "    prior volume    (empty)"
        )
        lines.append(
            f"    day change      {check.change_pct * 100:+.2f}%"
            if check.change_pct is not None else "    day change      (empty)"
        )
    else:
        lines.append("  quotes      FAILED")

    lines.append("")
    if check.bars_ok:
        lines.append("  barcharts   OK")
        lines.append(f"    bars parsed     {check.bar_count}")
        lines.append(f"    date range      {check.first_day} .. {check.last_day}")
        lines.append(f"    last close      {check.last_close}")
        lines.append(f"    last volume     {check.last_volume:,.0f}"
                     if check.last_volume else "    last volume     (empty)")
        lines.append(
            "    session         still open (volume will be projected)"
            if check.session_open else
            "    session         closed (no projection needed)"
        )
    else:
        lines.append("  barcharts   FAILED")

    if check.errors:
        lines += ["", "  Problems:"]
        lines += [f"    - {e}" for e in check.errors]
    else:
        lines += ["", "  All good — the feed matches what the scanner expects."]
    return "\n".join(lines)
