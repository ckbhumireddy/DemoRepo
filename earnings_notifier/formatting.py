"""Render the earnings digest as email subject + plain-text + HTML bodies.

Pure functions only — no IO — so the output is easy to snapshot in tests.
"""

from __future__ import annotations

import datetime as dt
import html
from typing import List, Tuple
from urllib.parse import quote

from .earnings import EarningsEvent


def _quote_url(ticker: str) -> str:
    """Yahoo Finance quote page for a ticker (already Yahoo-normalized)."""
    return f"https://finance.yahoo.com/quote/{quote(ticker)}"


def _weekday(d: dt.date) -> str:
    return d.strftime("%a %b %d, %Y")


def _fmt_price(value: float | None) -> str:
    return "—" if value is None else f"${value:,.2f}"


def _fmt_range(low: float | None, high: float | None) -> str:
    if low is None or high is None:
        return "—"
    return f"${low:,.2f} – ${high:,.2f}"


def _fmt_market_cap(value: float | None) -> str:
    if value is None:
        return "—"
    for threshold, suffix in ((1e12, "T"), (1e9, "B"), (1e6, "M")):
        if value >= threshold:
            return f"${value / threshold:,.2f}{suffix}"
    return f"${value:,.0f}"


def _surprise_pct(e: EarningsEvent) -> float | None:
    """Last quarter's earnings surprise, computed from EPS if Yahoo omits it."""
    if e.last_surprise_pct is not None:
        return e.last_surprise_pct
    if e.last_eps_actual is not None and e.last_eps_estimate:
        return (e.last_eps_actual - e.last_eps_estimate) / abs(e.last_eps_estimate) * 100
    return None


def _reaction_text(pct: float | None) -> str | None:
    """Plain-English description of the post-earnings price move."""
    if pct is None:
        return None
    if pct >= 10:
        verb = "soared"
    elif pct >= 3:
        verb = "jumped"
    elif pct >= 0:
        verb = "rose"
    elif pct > -3:
        verb = "dipped"
    elif pct > -10:
        verb = "dropped"
    else:
        verb = "crashed"
    return f"stock {verb} {abs(pct):.1f}%"


def _last_earnings_text(e: EarningsEvent) -> str | None:
    """One-line summary of the previous report, or None if we have nothing."""
    if e.last_eps_actual is None:
        return None
    when = f" ({e.last_earnings_date.strftime('%b %d')})" if e.last_earnings_date else ""
    line = f"Last earnings{when}: EPS ${e.last_eps_actual:,.2f}"
    if e.last_eps_estimate is not None:
        line += f" vs ${e.last_eps_estimate:,.2f} est"
    surprise = _surprise_pct(e)
    if surprise is not None:
        verdict = "beat" if surprise >= 0 else "missed"
        line += f" ({verdict} by {abs(surprise):.1f}%)"
    return line


def render_subject(events: List[EarningsEvent], today: dt.date, lead_days: int) -> str:
    n = len(events)
    if n == 0:
        return f"S&P 500 earnings: nothing new within {lead_days} days"
    noun = "company" if n == 1 else "companies"
    return f"S&P 500 earnings within {lead_days} days: {n} {noun}"


def render_text(events: List[EarningsEvent], today: dt.date, lead_days: int) -> str:
    lines = [
        f"S&P 500 earnings within the next {lead_days} day(s)",
        f"(generated {_weekday(today)})",
        "",
    ]
    if not events:
        lines.append("No new S&P 500 earnings within the window.")
        return "\n".join(lines)

    for e in events:
        days = e.days_until(today)
        flag = " (estimated)" if e.is_estimate else ""
        name = f"  {e.company}" if e.company else ""
        lines.append(f"- {e.ticker:<6}{name}")
        lines.append(f"    Reports {_weekday(e.date)}  (in {days} day(s)){flag}")
        lines.append(
            f"    Price {_fmt_price(e.price)}"
            f" | 52wk {_fmt_range(e.fifty_two_week_low, e.fifty_two_week_high)}"
            f" | Mkt cap {_fmt_market_cap(e.market_cap)}"
        )
        last = _last_earnings_text(e)
        reaction = _reaction_text(e.last_reaction_pct)
        if last and reaction:
            lines.append(f"    {last}; {reaction}")
        elif last:
            lines.append(f"    {last}")
        lines.append(f"    {_quote_url(e.ticker)}")
        lines.append("")

    lines.append(f"{len(events)} company(ies) total.")
    lines.append("")
    lines.append("Dates and quotes are sourced from Yahoo Finance and may shift; verify before trading.")
    return "\n".join(lines)


def render_html(events: List[EarningsEvent], today: dt.date, lead_days: int) -> str:
    header = (
        f"<h2 style='margin:0 0 4px'>S&amp;P 500 earnings within {lead_days} day(s)</h2>"
        f"<p style='margin:0 0 16px;color:#555'>Generated {html.escape(_weekday(today))}</p>"
    )
    if not events:
        return (
            "<div style=\"font-family:Arial,Helvetica,sans-serif\">"
            + header
            + "<p>No new S&amp;P 500 earnings within the window.</p>"
            + "</div>"
        )

    rows = []
    for e in events:
        days = e.days_until(today)
        flag = (
            "<span style='color:#b26a00'>estimated</span>"
            if e.is_estimate
            else "<span style='color:#1a7f37'>confirmed</span>"
        )
        company = (
            f"<div style='color:#555;font-size:12px'>{html.escape(e.company)}</div>"
            if e.company
            else ""
        )
        last = _last_earnings_text(e)
        if last:
            surprise = _surprise_pct(e)
            color = "#555" if surprise is None else ("#1a7f37" if surprise >= 0 else "#c62828")
            # Strip the "Last earnings" prefix — the column header already says it.
            last_cell = (
                f"<span style='color:{color}'>"
                + html.escape(last.removeprefix("Last earnings").lstrip())
                + "</span>"
            )
            reaction = _reaction_text(e.last_reaction_pct)
            if reaction:
                rcolor = "#1a7f37" if e.last_reaction_pct >= 0 else "#c62828"
                last_cell += (
                    f"<div style='color:{rcolor};font-size:12px'>{html.escape(reaction)}</div>"
                )
        else:
            last_cell = "—"
        rows.append(
            "<tr>"
            f"<td style='padding:6px 12px'>"
            f"<a href='{html.escape(_quote_url(e.ticker))}' "
            f"style='color:#0b57d0;text-decoration:none'>"
            f"<b>{html.escape(e.ticker)}</b></a>{company}</td>"
            f"<td style='padding:6px 12px;white-space:nowrap'>{html.escape(_weekday(e.date))}</td>"
            f"<td style='padding:6px 12px;text-align:right'>{days}</td>"
            f"<td style='padding:6px 12px'>{flag}</td>"
            f"<td style='padding:6px 12px;text-align:right'>{html.escape(_fmt_price(e.price))}</td>"
            f"<td style='padding:6px 12px;white-space:nowrap'>"
            f"{html.escape(_fmt_range(e.fifty_two_week_low, e.fifty_two_week_high))}</td>"
            f"<td style='padding:6px 12px;text-align:right'>{html.escape(_fmt_market_cap(e.market_cap))}</td>"
            f"<td style='padding:6px 12px'>{last_cell}</td>"
            "</tr>"
        )

    table = (
        "<table style='border-collapse:collapse;font-size:14px'>"
        "<thead><tr style='background:#f2f2f2;text-align:left'>"
        "<th style='padding:6px 12px'>Company</th>"
        "<th style='padding:6px 12px'>Earnings date</th>"
        "<th style='padding:6px 12px;text-align:right'>Days</th>"
        "<th style='padding:6px 12px'>Status</th>"
        "<th style='padding:6px 12px;text-align:right'>Price</th>"
        "<th style='padding:6px 12px'>52-week range</th>"
        "<th style='padding:6px 12px;text-align:right'>Mkt cap</th>"
        "<th style='padding:6px 12px'>Last earnings</th>"
        "</tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table>"
    )

    footer = (
        "<p style='margin-top:16px;color:#777;font-size:12px'>"
        f"{len(events)} company(ies). Dates and quotes are sourced from Yahoo "
        "Finance and may shift; verify before trading.</p>"
    )
    return (
        "<div style=\"font-family:Arial,Helvetica,sans-serif;color:#222\">"
        + header
        + table
        + footer
        + "</div>"
    )


def render_email(
    events: List[EarningsEvent], today: dt.date, lead_days: int
) -> Tuple[str, str, str]:
    """Return ``(subject, text_body, html_body)``."""
    return (
        render_subject(events, today, lead_days),
        render_text(events, today, lead_days),
        render_html(events, today, lead_days),
    )
