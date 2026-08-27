"""The swing-signals email (pure renderer).

Each row is a complete trade plan — entry, stop, target, R/R — because a
signal without its exits is a temptation, not a plan. The subject and body
always name the strategy and its backtest standing, so the reader never has
to wonder whether what they're looking at earned its way into the inbox.
"""

from __future__ import annotations

import datetime as dt
import html
from typing import List, Optional, Tuple

from .strategies.base import Signal

GREEN = "#1a7f37"
RED = "#b3261e"
AMBER = "#b26a00"
GREY = "#777"

_WRAP = (
    "<div style=\"font-family:Arial,Helvetica,sans-serif;color:#222;"
    "font-size:14px;max-width:720px\">"
)

DISCLAIMER = (
    "Signals are mechanical: levels come from clustered swing pivots and "
    "every plan is entry/stop/target with no discretion. Backtest approval "
    "measures the past, not the future. Educational analysis only — not "
    "investment advice."
)

_TH = (
    "padding:4px 8px;border-bottom:1px solid #bbb;text-align:right;"
    "font-size:12px;color:#555"
)
_TD = "padding:3px 8px;border-bottom:1px solid #eee;text-align:right"


def _price(value: Optional[float]) -> str:
    if value is None:
        return "—"
    return f"${value:,.2f}" if value < 1000 else f"${value:,.0f}"


def render_signals_email(
    signals: List[Signal],
    today: dt.date,
    *,
    strategy_name: str,
    approval_note: str,
    universe_note: str = "",
) -> Tuple[str, str, str]:
    date_label = today.strftime("%a %b %d")
    if signals:
        subject = f"[Swing] {len(signals)} setup(s) · {strategy_name} — {date_label}"
    else:
        subject = f"[Swing] No setups · {strategy_name} — {date_label}"

    # ---- text ----
    lines = [
        f"Swing setups — {strategy_name} ({today.strftime('%a %b %d, %Y')})",
        f"Backtest standing: {approval_note}"
        + (f" — {universe_note}" if universe_note else ""),
        "",
    ]
    if signals:
        lines.append(
            f"  {'':<7}{'PRICE':>9} {'ENTRY':>9} {'STOP':>9} {'TARGET':>9} "
            f"{'R/R':>5}  NOTES"
        )
        for s in signals:
            lines.append(
                f"  {s.ticker:<6} {_price(s.price):>9} {_price(s.entry):>9} "
                f"{_price(s.stop):>9} {_price(s.target):>9} {s.rr:>4.1f}  {s.note}"
            )
    else:
        lines.append("  No candidate cleared the setup conditions today.")
    lines += ["", DISCLAIMER]
    text = "\n".join(lines)

    # ---- html ----
    head = (
        "<tr>"
        f"<th style='{_TH};text-align:left'>Ticker</th>"
        f"<th style='{_TH}'>Price</th>"
        f"<th style='{_TH}'>Entry</th>"
        f"<th style='{_TH}'>Stop</th>"
        f"<th style='{_TH}'>Target</th>"
        f"<th style='{_TH}'>R/R</th>"
        f"<th style='{_TH};text-align:left'>Notes</th>"
        "</tr>"
    )
    rows = []
    for s in signals:
        rows.append(
            "<tr>"
            f"<td style='{_TD};text-align:left'><b>{html.escape(s.ticker)}</b></td>"
            f"<td style='{_TD}'>{_price(s.price)}</td>"
            f"<td style='{_TD}'>{_price(s.entry)}</td>"
            f"<td style='{_TD};color:{RED}'>{_price(s.stop)}</td>"
            f"<td style='{_TD};color:{GREEN}'>{_price(s.target)}</td>"
            f"<td style='{_TD}'><b>{s.rr:.1f}</b></td>"
            f"<td style='{_TD};text-align:left;color:#444'>"
            f"{html.escape(s.note)}</td>"
            "</tr>"
        )
    table = (
        "<div style='overflow-x:auto'>"
        "<table style='border-collapse:collapse;width:100%;font-size:13px'>"
        + head + "".join(rows) + "</table></div>"
        if signals
        else "<p style='color:#555'>No candidate cleared the setup "
             "conditions today.</p>"
    )
    html_body = (
        _WRAP
        + "<h2 style='margin:0 0 4px'>Swing setups &middot; "
        + html.escape(strategy_name) + "</h2>"
        + "<p style='margin:0 0 16px;color:#555'>"
        + html.escape(today.strftime("%a %b %d, %Y"))
        + f" &middot; {html.escape(approval_note)}"
        + (f" &middot; {html.escape(universe_note)}" if universe_note else "")
        + "</p>"
        + table
        + "<p style='margin-top:16px;color:#777;font-size:12px'>"
        + html.escape(DISCLAIMER)
        + "</p></div>"
    )
    return subject, text, html_body
