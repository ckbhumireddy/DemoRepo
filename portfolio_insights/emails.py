"""The insights emails (pure renderers): EOD summary and midday alert.

Subjects carry the ``[Portfolio]`` prefix. Every renderer returns
``(subject, text_body, html_body)``.
"""

from __future__ import annotations

import datetime as dt
import html
from typing import List, Optional, Tuple

from .signals import AlertTrigger, Insight, PortfolioView, PositionView

GREEN = "#1a7f37"
RED = "#c62828"

_WRAP = (
    "<div style=\"font-family:Arial,Helvetica,sans-serif;color:#222;"
    "font-size:14px;max-width:640px\">"
)

DISCLAIMER = (
    "Automated, rule-based observations from your own portfolio data. "
    "Not investment advice."
)


def _weekday(d: dt.date) -> str:
    return d.strftime("%a %b %d, %Y")


def _money(v: Optional[float]) -> str:
    return "—" if v is None else f"${v:,.2f}"


def _pct(v: Optional[float]) -> str:
    return "—" if v is None else f"{v:+.2f}%"


def _card(title: str, rows: List[str]) -> str:
    return (
        "<div style='border:1px solid #d0d0d0;border-radius:8px;"
        "padding:10px 14px;margin:0 0 10px'>"
        f"<div style='font-size:15px'><b>{html.escape(title)}</b></div>"
        + "".join(rows)
        + "</div>"
    )


def _row(text: str, color: str = "#444") -> str:
    return f"<div style='margin-top:4px;color:{color}'>{text}</div>"


def _move_color(v: Optional[float]) -> str:
    if v is None:
        return "#444"
    return GREEN if v >= 0 else RED


# --------------------------------------------------------------------------- #
# EOD
# --------------------------------------------------------------------------- #
def render_eod_email(
    views: List[PositionView],
    portfolio: PortfolioView,
    insights: List[Insight],
    movers,
    today: dt.date,
    *,
    fetch_error: Optional[str] = None,
    other_note: Optional[str] = None,
) -> Tuple[str, str, str]:
    if fetch_error:
        subject = f"[Portfolio] EOD — {today.strftime('%a %b %d')}: could not load the portfolio"
        text = "\n".join([
            f"Portfolio EOD insights ({_weekday(today)})",
            "",
            f"Could not load the portfolio: {fetch_error}",
            "",
            "Fix the portfolio source and the next run will recover.",
            "",
            DISCLAIMER,
        ])
        body = (
            _card("Could not load the portfolio", [_row(html.escape(fetch_error), RED)])
            + f"<p style='color:#777;font-size:12px'>{html.escape(DISCLAIMER)}</p>"
        )
        return subject, text, _WRAP + body + "</div>"

    if not views:
        subject = f"[Portfolio] EOD — {today.strftime('%a %b %d')}: no positions"
        text = (
            f"Portfolio EOD insights ({_weekday(today)})\n\n"
            "The portfolio has no equity positions.\n\n" + DISCLAIMER
        )
        return subject, text, (
            _WRAP + "<p>The portfolio has no equity positions.</p>"
            + f"<p style='color:#777;font-size:12px'>{html.escape(DISCLAIMER)}</p></div>"
        )

    day = portfolio.day_change_pct
    subject = (
        f"[Portfolio] EOD — {today.strftime('%a %b %d')}: "
        f"day {_pct(day)} ({portfolio.day_pnl:+,.0f})"
        if portfolio.day_pnl is not None
        else f"[Portfolio] EOD — {today.strftime('%a %b %d')}"
    )
    subject += f" · {len(insights)} watch item(s)"

    # ---- text ----
    lines = [
        f"Portfolio EOD insights ({_weekday(today)})",
        "",
        f"Total value {_money(portfolio.total_value)}"
        + (f" (cash {_money(portfolio.cash)})" if portfolio.cash else ""),
        f"Day P&L {_money(portfolio.day_pnl)} ({_pct(day)})"
        + (
            f" | SPY {_pct(portfolio.spy_day_pct)}"
            if portfolio.spy_day_pct is not None
            else ""
        ),
        f"Total P&L vs cost {_money(portfolio.total_pnl)}",
    ]
    if portfolio.unquoted:
        lines.append(
            f"{portfolio.unquoted} position(s) had no quote and are excluded "
            "from the numbers above."
        )
    lines += ["", "Positions:"]
    for v in views:
        w = f"{v.weight_pct:.1f}%" if v.weight_pct is not None else "—"
        lines.append(
            f"  {v.ticker:<6} {v.position.quantity:>10,.2f}  "
            f"value {_money(v.market_value):>12}  w {w:>6}  "
            f"day {_pct(v.day_change_pct):>8}"
            + (
                f"  vs SPY {_pct(v.vs_spy_pct):>8}"
                if v.vs_spy_pct is not None
                else ""
            )
            + (
                f"  total {_pct(v.total_pnl_pct)}"
                if v.total_pnl_pct is not None
                else ""
            )
        )
    gainers, losers = movers
    if gainers or losers:
        lines += ["", "Biggest movers:"]
        for v in gainers:
            lines.append(f"  UP   {v.ticker} {_pct(v.day_change_pct)}")
        for v in losers:
            lines.append(f"  DOWN {v.ticker} {_pct(v.day_change_pct)}")
    if insights:
        lines += ["", "Watch items & suggestions:"]
        for i in insights:
            lines.append(f"  • {i.fact} — {i.suggestion}")
    if other_note:
        lines += ["", other_note]
    lines += ["", DISCLAIMER]
    text = "\n".join(lines)

    # ---- html ----
    summary_rows = [
        _row(
            f"Total value <b>{_money(portfolio.total_value)}</b>"
            + (f" &middot; cash {_money(portfolio.cash)}" if portfolio.cash else "")
        ),
        _row(
            f"Day P&L <b style='color:{_move_color(portfolio.day_pnl)}'>"
            f"{_money(portfolio.day_pnl)} ({_pct(day)})</b>"
            + (
                f" &middot; SPY {_pct(portfolio.spy_day_pct)}"
                if portfolio.spy_day_pct is not None
                else ""
            )
        ),
        _row(f"Total P&L vs cost <b>{_money(portfolio.total_pnl)}</b>"),
    ]
    if portfolio.unquoted:
        summary_rows.append(_row(
            f"{portfolio.unquoted} position(s) had no quote and are excluded.",
            "#b26a00",
        ))
    cards = [_card("Portfolio", summary_rows)]

    pos_rows = []
    for v in views:
        w = f"{v.weight_pct:.1f}%" if v.weight_pct is not None else "—"
        pos_rows.append(_row(
            f"<b>{html.escape(v.ticker)}</b> &middot; {v.position.quantity:,.2f} sh"
            f" &middot; {_money(v.market_value)} &middot; {w}"
            f" &middot; day <span style='color:{_move_color(v.day_change_pct)}'>"
            f"{_pct(v.day_change_pct)}</span>"
            + (
                f" &middot; vs SPY {_pct(v.vs_spy_pct)}"
                if v.vs_spy_pct is not None
                else ""
            )
            + (
                f" &middot; total {_pct(v.total_pnl_pct)}"
                if v.total_pnl_pct is not None
                else ""
            )
        ))
    cards.append(_card("Positions", pos_rows))

    if gainers or losers:
        cards.append(_card("Biggest movers", [
            _row(f"<b>{html.escape(v.ticker)}</b> {_pct(v.day_change_pct)}",
                 GREEN)
            for v in gainers
        ] + [
            _row(f"<b>{html.escape(v.ticker)}</b> {_pct(v.day_change_pct)}",
                 RED)
            for v in losers
        ]))
    if insights:
        cards.append(_card("Watch items & suggestions", [
            _row(
                f"<b>{html.escape(i.fact)}</b> — {html.escape(i.suggestion)}"
            )
            for i in insights
        ]))
    if other_note:
        cards.append(_row(html.escape(other_note), "#777"))
    html_body = (
        _WRAP
        + "<h2 style='margin:0 0 4px'>Portfolio EOD insights</h2>"
        + f"<p style='margin:0 0 16px;color:#555'>{html.escape(_weekday(today))}</p>"
        + "".join(cards)
        + f"<p style='margin-top:16px;color:#777;font-size:12px'>{html.escape(DISCLAIMER)}</p>"
        + "</div>"
    )
    return subject, text, html_body


# --------------------------------------------------------------------------- #
# Midday alert
# --------------------------------------------------------------------------- #
def render_midday_email(
    triggers: List[AlertTrigger],
    views: List[PositionView],
    portfolio: PortfolioView,
    today: dt.date,
) -> Tuple[str, str, str]:
    details = " · ".join(t.detail for t in triggers[:3])
    subject = f"[Portfolio] Midday alert — {details}"

    by_ticker = {v.ticker: v for v in views}
    lines = [f"Portfolio midday alert ({_weekday(today)})", ""]
    rows = []
    for t in triggers:
        lines.append(f"  {t.detail}")
        row = f"<b>{html.escape(t.detail)}</b>"
        v = by_ticker.get(t.ticker)
        if v is not None and v.market_value is not None:
            context = (
                f" — value {_money(v.market_value)}, "
                f"{v.weight_pct:.1f}% of portfolio"
                if v.weight_pct is not None
                else f" — value {_money(v.market_value)}"
            )
            lines[-1] += context
            row += html.escape(context)
        rows.append(_row(row, RED))
    lines += [
        "",
        f"Portfolio day {_pct(portfolio.day_change_pct)}"
        f" ({_money(portfolio.day_pnl)})"
        + (
            f" | SPY {_pct(portfolio.spy_day_pct)}"
            if portfolio.spy_day_pct is not None
            else ""
        ),
        "",
        DISCLAIMER,
    ]
    text = "\n".join(lines)
    html_body = (
        _WRAP
        + "<h2 style='margin:0 0 8px'>Portfolio midday alert</h2>"
        + _card("Triggers", rows)
        + _row(
            f"Portfolio day {_pct(portfolio.day_change_pct)}"
            f" ({_money(portfolio.day_pnl)})"
        )
        + f"<p style='margin-top:16px;color:#777;font-size:12px'>{html.escape(DISCLAIMER)}</p>"
        + "</div>"
    )
    return subject, text, html_body
