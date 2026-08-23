"""The market-insights email (pure renderer).

Ordered by how unusual the volume is, because that is the question the scan
answers. Each row carries its own interpretation — the pattern name and one
line of why — so the email is readable without opening a chart.
"""

from __future__ import annotations

import datetime as dt
import html
from typing import List, Optional, Sequence, Tuple

from .insights import BEARISH, BULLISH
from .scanner import InsightRow
from .trend import DOWN, FLAT, UP

GREEN = "#1a7f37"
RED = "#b3261e"
AMBER = "#b26a00"
GREY = "#777"

_WRAP = (
    "<div style=\"font-family:Arial,Helvetica,sans-serif;color:#222;"
    "font-size:14px;max-width:720px\">"
)

DISCLAIMER = (
    "Relative volume compares a projected full session against the median of "
    "recent sessions; intraday runs project from a typical volume curve, so "
    "early-session figures are estimates. Pattern labels describe what the "
    "tape is doing, not what it will do. Educational analysis only — not "
    "investment advice."
)

_TH = (
    "padding:4px 8px;border-bottom:1px solid #bbb;text-align:right;"
    "font-size:12px;color:#555"
)
_TD = "padding:3px 8px;border-bottom:1px solid #eee;text-align:right"

_ARROWS = {UP: "up", DOWN: "down", FLAT: "flat"}
_TREND_COLORS = {UP: GREEN, DOWN: RED, FLAT: GREY}


def _pct(value: Optional[float], digits: int = 1) -> str:
    return "—" if value is None else f"{value * 100:+.{digits}f}%"


def _price(value: Optional[float]) -> str:
    if not value:
        return "—"
    return f"${value:,.2f}" if value < 100 else f"${value:,.0f}"


def _trend_cell(row: InsightRow) -> Tuple[str, str]:
    if row.trend is None:
        return "—", "—"
    return _ARROWS[row.trend.short_term], _ARROWS[row.trend.long_term]


def _direction_color(row: InsightRow) -> str:
    if row.insight.direction == BULLISH:
        return GREEN
    if row.insight.direction == BEARISH:
        return RED
    return GREY


def _session_label(rows: Sequence[InsightRow]) -> str:
    """Whether the numbers are a finished session or a live projection."""
    live = next((r for r in rows if r.stats.partial), None)
    if live is None:
        return "last completed session"
    return f"live session, ~{live.stats.fraction * 100:.0f}% elapsed (projected)"


def render_insights_email(
    rows: List[InsightRow],
    today: dt.date,
    *,
    universe_note: str = "",
    scan_note: Optional[str] = None,
) -> Tuple[str, str, str]:
    held_count = sum(1 for r in rows if r.held)
    date_label = today.strftime("%a %b %d")
    if rows:
        subject = (
            f"[Volume] {len(rows)} unusual — {date_label}"
            + (f" · {held_count} in your portfolio" if held_count else "")
        )
    else:
        subject = f"[Volume] Nothing unusual — {date_label}"

    session_label = _session_label(rows)

    # ---- text ----
    lines = [
        f"Unusual volume & trend ({today.strftime('%a %b %d, %Y')})",
        f"Basis: {session_label}" + (f" — {universe_note}" if universe_note else ""),
        "",
    ]
    if rows:
        lines.append(
            f"  {'':<7}{'RVOL':>6} {'Z':>5} {'DAY':>8} {'PRICE':>9}  "
            f"{'SHORT':<6}{'LONG':<6} PATTERN"
        )
        for row in rows:
            short, long = _trend_cell(row)
            zscore = f"{row.stats.zscore:.1f}" if row.stats.zscore is not None else "—"
            held = "*" if row.held else " "
            lines.append(
                f"  {row.ticker:<6}{held}{row.stats.rvol:>5.1f}x {zscore:>5} "
                f"{_pct(row.change_pct):>8} {_price(row.price):>9}  "
                f"{short:<6}{long:<6} {row.insight.label}"
            )
        if held_count:
            lines.append("  * = in your portfolio")
        lines += ["", "What the volume is saying:"]
        for row in rows:
            lines.append(f"  {row.ticker}: {row.insight.label} — {row.insight.note}")
    else:
        lines.append("  No name cleared the unusual-volume threshold today.")
    if scan_note:
        lines += ["", scan_note]
    lines += ["", DISCLAIMER]
    text = "\n".join(lines)

    # ---- html ----
    head = (
        "<tr>"
        f"<th style='{_TH};text-align:left'>Ticker</th>"
        f"<th style='{_TH}'>RVOL</th>"
        f"<th style='{_TH}'>Z</th>"
        f"<th style='{_TH}'>Day</th>"
        f"<th style='{_TH}'>Price</th>"
        f"<th style='{_TH}'>Short</th>"
        f"<th style='{_TH}'>Long</th>"
        f"<th style='{_TH};text-align:left'>Pattern</th>"
        "</tr>"
    )
    body_rows = []
    for row in rows:
        ticker_cell = f"<b>{html.escape(row.ticker)}</b>"
        if row.held:
            ticker_cell += (
                f" <span style='background:{GREEN};color:#fff;border-radius:4px;"
                "padding:0 5px;font-size:11px;font-weight:bold'>HELD</span>"
            )
        change_color = GREEN if (row.change_pct or 0) >= 0 else RED
        zscore = (
            f"{row.stats.zscore:.1f}" if row.stats.zscore is not None else "—"
        )
        short_label, long_label = _trend_cell(row)
        short_color = (
            _TREND_COLORS[row.trend.short_term] if row.trend else GREY
        )
        long_color = _TREND_COLORS[row.trend.long_term] if row.trend else GREY
        body_rows.append(
            "<tr>"
            f"<td style='{_TD};text-align:left'>{ticker_cell}</td>"
            f"<td style='{_TD}'><b>{row.stats.rvol:.1f}x</b></td>"
            f"<td style='{_TD};color:{GREY}'>{zscore}</td>"
            f"<td style='{_TD};color:{change_color}'>{_pct(row.change_pct)}</td>"
            f"<td style='{_TD}'>{_price(row.price)}</td>"
            f"<td style='{_TD};color:{short_color}'>{short_label}</td>"
            f"<td style='{_TD};color:{long_color}'>{long_label}</td>"
            f"<td style='{_TD};text-align:left;color:{_direction_color(row)}'>"
            f"{html.escape(row.insight.label)}</td>"
            "</tr>"
        )
    table = (
        "<div style='overflow-x:auto'>"
        "<table style='border-collapse:collapse;width:100%;font-size:13px'>"
        + head + "".join(body_rows) + "</table></div>"
        if rows
        else "<p style='color:#555'>No name cleared the unusual-volume "
             "threshold today.</p>"
    )

    reads = ""
    if rows:
        reads = (
            "<div style='border:1px solid #d0d0d0;border-radius:8px;"
            "padding:10px 14px;margin:14px 0'>"
            "<div style='font-size:15px'><b>What the volume is saying</b></div>"
            + "".join(
                "<div style='margin-top:6px;color:#444'>"
                f"<b>{html.escape(r.ticker)}</b> &middot; "
                f"<span style='color:{_direction_color(r)}'>"
                f"{html.escape(r.insight.label)}</span> — "
                f"{html.escape(r.insight.note)}</div>"
                for r in rows
            )
            + "</div>"
        )
    note_html = (
        f"<p style='color:{AMBER};font-size:12px'>{html.escape(scan_note)}</p>"
        if scan_note
        else ""
    )
    html_body = (
        _WRAP
        + "<h2 style='margin:0 0 4px'>Unusual volume &amp; trend</h2>"
        + "<p style='margin:0 0 16px;color:#555'>"
        + html.escape(today.strftime("%a %b %d, %Y"))
        + f" &middot; {html.escape(session_label)}"
        + (f" &middot; {html.escape(universe_note)}" if universe_note else "")
        + "</p>"
        + table
        + reads
        + note_html
        + "<p style='margin-top:16px;color:#777;font-size:12px'>"
        + html.escape(DISCLAIMER)
        + "</p></div>"
    )
    return subject, text, html_body
