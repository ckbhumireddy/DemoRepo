"""The daily IV-rank email (pure renderer)."""

from __future__ import annotations

import datetime as dt
import html
from typing import List, Optional, Tuple

from .plays import PlayIdea
from .scanner import ScanRow

GREEN = "#1a7f37"
AMBER = "#b26a00"

_WRAP = (
    "<div style=\"font-family:Arial,Helvetica,sans-serif;color:#222;"
    "font-size:14px;max-width:640px\">"
)

DISCLAIMER = (
    "IV rank is a proxy (current ~30-day ATM implied volatility vs the "
    "stock's own 1-year realized-volatility range). Quotes are from the "
    "prior close. Educational analysis only — not investment advice."
)

_TH = (
    "padding:4px 8px;border-bottom:1px solid #bbb;text-align:right;"
    "font-size:12px;color:#555"
)
_TD = "padding:3px 8px;border-bottom:1px solid #eee;text-align:right"


def _earnings_label(r: ScanRow) -> str:
    if r.earnings_date is None:
        return ""
    label = r.earnings_date.strftime("%a %b %d")
    if r.earnings_timing:
        label += f" ({r.earnings_timing})"
    return label


def render_scan_email(
    top: List[ScanRow],
    ideas: List[PlayIdea],
    today: dt.date,
    *,
    universe_note: str = "",
    scan_note: Optional[str] = None,
) -> Tuple[str, str, str]:
    held_count = sum(1 for r in top if r.held)
    subject = (
        f"[IV] Top {len(top)} IV ranks — {today.strftime('%a %b %d')}"
        + (f" · {held_count} in your portfolio" if held_count else "")
    )

    # ---- text ----
    lines = [
        f"Daily IV-rank scan ({today.strftime('%a %b %d, %Y')})"
        + (f" — {universe_note}" if universe_note else ""),
        "",
        f"  {'#':>3} {'':<7}{'IVR':>4}  {'IV30':>6}  {'PRICE':>9}  EARNINGS",
    ]
    for i, r in enumerate(top, 1):
        held = "*" if r.held else " "
        price = f"${r.price:,.0f}" if r.price else "—"
        lines.append(
            f"  {i:>3} {r.ticker:<6}{held}{r.rank.iv_rank * 100:>4.0f}  "
            f"{r.rank.current_iv * 100:>5.0f}%  {price:>9}  {_earnings_label(r)}"
        )
    if held_count:
        lines.append("  * = in your portfolio")
    if ideas:
        lines += ["", "Potential options plays:"]
        for p in ideas:
            prefix = f"{p.ticker}: " if p.ticker else ""
            lines.append(f"  {prefix}{p.idea} — {p.why}")
    if scan_note:
        lines += ["", scan_note]
    lines += ["", DISCLAIMER]
    text = "\n".join(lines)

    # ---- html ----
    head = (
        "<tr>"
        f"<th style='{_TH}'>#</th>"
        f"<th style='{_TH};text-align:left'>Ticker</th>"
        f"<th style='{_TH}'>IV rank</th>"
        f"<th style='{_TH}'>IV30</th>"
        f"<th style='{_TH}'>Price</th>"
        f"<th style='{_TH};text-align:left'>Earnings</th>"
        "</tr>"
    )
    rows = []
    for i, r in enumerate(top, 1):
        ticker_cell = f"<b>{html.escape(r.ticker)}</b>"
        row_style = _TD
        if r.held:
            ticker_cell += (
                f" <span style='background:{GREEN};color:#fff;border-radius:4px;"
                "padding:0 5px;font-size:11px;font-weight:bold'>HELD</span>"
            )
        earn = _earnings_label(r)
        earn_cell = (
            f"<span style='color:{AMBER}'>{html.escape(earn)}</span>"
            if earn
            else ""
        )
        rows.append(
            "<tr>"
            f"<td style='{row_style};color:#999'>{i}</td>"
            f"<td style='{row_style};text-align:left'>{ticker_cell}</td>"
            f"<td style='{row_style}'><b>{r.rank.iv_rank * 100:.0f}</b></td>"
            f"<td style='{row_style}'>{r.rank.current_iv * 100:.0f}%</td>"
            f"<td style='{row_style}'>{f'${r.price:,.0f}' if r.price else '—'}</td>"
            f"<td style='{row_style};text-align:left'>{earn_cell}</td>"
            "</tr>"
        )
    table = (
        "<div style='overflow-x:auto'>"
        "<table style='border-collapse:collapse;width:100%;font-size:13px'>"
        + head + "".join(rows) + "</table></div>"
    )
    ideas_html = ""
    if ideas:
        ideas_html = (
            "<div style='border:1px solid #d0d0d0;border-radius:8px;"
            "padding:10px 14px;margin:10px 0'>"
            "<div style='font-size:15px'><b>Potential options plays</b></div>"
            + "".join(
                f"<div style='margin-top:4px;color:#444'>"
                + (f"<b>{html.escape(p.ticker)}</b>: " if p.ticker else "")
                + f"{html.escape(p.idea)} — {html.escape(p.why)}</div>"
                for p in ideas
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
        + f"<h2 style='margin:0 0 4px'>Daily IV-rank scan</h2>"
        + "<p style='margin:0 0 16px;color:#555'>"
        + html.escape(today.strftime("%a %b %d, %Y"))
        + (f" &middot; {html.escape(universe_note)}" if universe_note else "")
        + "</p>"
        + table
        + ideas_html
        + note_html
        + f"<p style='margin-top:16px;color:#777;font-size:12px'>{html.escape(DISCLAIMER)}</p>"
        + "</div>"
    )
    return subject, text, html_body
