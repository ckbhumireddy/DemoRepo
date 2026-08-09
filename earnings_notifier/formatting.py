"""Render the earnings digest as email subject + plain-text + HTML bodies.

Pure functions only — no IO — so the output is easy to snapshot in tests.
"""

from __future__ import annotations

import datetime as dt
import html
from typing import List, Tuple

from .earnings import EarningsEvent


def _weekday(d: dt.date) -> str:
    return d.strftime("%a %b %d, %Y")


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
        lines.append(f"- {e.ticker:<6} {_weekday(e.date)}  (in {days} day(s)){flag}")

    lines.append("")
    lines.append(f"{len(events)} company(ies) total.")
    lines.append("")
    lines.append("Dates are sourced from Yahoo Finance and may shift; verify before trading.")
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
        rows.append(
            "<tr>"
            f"<td style='padding:6px 12px;font-weight:bold'>{html.escape(e.ticker)}</td>"
            f"<td style='padding:6px 12px'>{html.escape(_weekday(e.date))}</td>"
            f"<td style='padding:6px 12px;text-align:right'>{days}</td>"
            f"<td style='padding:6px 12px'>{flag}</td>"
            "</tr>"
        )

    table = (
        "<table style='border-collapse:collapse;font-size:14px'>"
        "<thead><tr style='background:#f2f2f2;text-align:left'>"
        "<th style='padding:6px 12px'>Ticker</th>"
        "<th style='padding:6px 12px'>Earnings date</th>"
        "<th style='padding:6px 12px;text-align:right'>Days</th>"
        "<th style='padding:6px 12px'>Status</th>"
        "</tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table>"
    )

    footer = (
        "<p style='margin-top:16px;color:#777;font-size:12px'>"
        f"{len(events)} company(ies). Dates are sourced from Yahoo Finance and "
        "may shift; verify before trading.</p>"
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
