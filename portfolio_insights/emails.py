"""The insights emails (pure renderers): EOD summary and midday alert.

Subjects carry the ``[Portfolio]`` prefix. Every renderer returns
``(subject, text_body, html_body)``.
"""

from __future__ import annotations

import datetime as dt
import html
from typing import List, Optional, Tuple

from .signals import (
    MIN_INSIGHT_VALUE,
    AlertTrigger,
    Insight,
    PortfolioView,
    PositionView,
    biggest_dollar_movers,
    biggest_movers,
)

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


def _money0(v: Optional[float]) -> str:
    """Whole dollars — cents are noise at position scale."""
    return "—" if v is None else f"${v:,.0f}"


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


def _split_dust(views: List[PositionView]):
    """Positions below the insight noise floor collapse into one line."""
    major = [
        v for v in views
        if v.market_value is None or abs(v.market_value) >= MIN_INSIGHT_VALUE
    ]
    dust = [
        v for v in views
        if v.market_value is not None and abs(v.market_value) < MIN_INSIGHT_VALUE
    ]
    return major, dust


def _dust_summary(dust: List[PositionView]) -> Optional[str]:
    if not dust:
        return None
    total = sum(v.market_value for v in dust)
    day = sum(v.day_pnl for v in dust if v.day_pnl is not None)
    tickers = ", ".join(v.ticker for v in dust[:8])
    if len(dust) > 8:
        tickers += ", …"
    return (
        f"+ {len(dust)} small positions ({tickers}) worth "
        f"{_money0(total)} total, day {day:+,.0f}"
    )


_TH = (
    "padding:4px 8px;border-bottom:1px solid #bbb;text-align:right;"
    "font-size:12px;color:#555"
)
_TD = "padding:3px 8px;border-bottom:1px solid #eee;text-align:right"


def _positions_table(views: List[PositionView]) -> str:
    """A real table: aligned columns beat 77 crowded prose rows."""
    major, dust = _split_dust(views)
    head = (
        "<tr>"
        f"<th style='{_TH};text-align:left'>Ticker</th>"
        f"<th style='{_TH}'>Value</th>"
        f"<th style='{_TH}'>Weight</th>"
        f"<th style='{_TH}'>Day</th>"
        f"<th style='{_TH}'>vs SPY</th>"
        f"<th style='{_TH}'>Total P&L</th>"
        "</tr>"
    )
    rows = []
    for v in major:
        w = f"{v.weight_pct:.1f}%" if v.weight_pct is not None else "—"
        rows.append(
            "<tr>"
            f"<td style='{_TD};text-align:left'><b>{html.escape(v.ticker)}</b></td>"
            f"<td style='{_TD}'>{_money0(v.market_value)}</td>"
            f"<td style='{_TD}'>{w}</td>"
            f"<td style='{_TD};color:{_move_color(v.day_change_pct)}'>"
            f"{_pct(v.day_change_pct)}</td>"
            f"<td style='{_TD}'>{_pct(v.vs_spy_pct)}</td>"
            f"<td style='{_TD};color:{_move_color(v.total_pnl_pct)}'>"
            f"{_pct(v.total_pnl_pct)}</td>"
            "</tr>"
        )
    dust_line = _dust_summary(dust)
    dust_html = (
        f"<div style='margin-top:6px;color:#777;font-size:12px'>"
        f"{html.escape(dust_line)}</div>"
        if dust_line
        else ""
    )
    return (
        "<div style='border:1px solid #d0d0d0;border-radius:8px;"
        "padding:10px 14px;margin:0 0 10px'>"
        "<div style='font-size:15px;margin-bottom:6px'><b>Positions</b>"
        " <span style='color:#777;font-weight:normal'>(by value)</span></div>"
        "<div style='overflow-x:auto'>"
        "<table style='border-collapse:collapse;width:100%;font-size:13px'>"
        + head + "".join(rows) + "</table></div>"
        + dust_html
        + "</div>"
    )


# --------------------------------------------------------------------------- #
# EOD
# --------------------------------------------------------------------------- #
def _iv_note(rank: float) -> str:
    if rank >= 0.7:
        return "premium rich — covered calls and credit structures pay well"
    if rank <= 0.3:
        return "premium cheap — protective puts and collars are inexpensive"
    return "premium mid-range"


def render_eod_email(
    views: List[PositionView],
    portfolio: PortfolioView,
    insights: List[Insight],
    today: dt.date,
    *,
    fetch_error: Optional[str] = None,
    other_note: Optional[str] = None,
    iv_context=None,
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
    major, dust = _split_dust(views)
    lines += ["", "Positions (by value):"]
    lines.append(f"  {'':<7}{'VALUE':>10}  {'WT':>6}  {'DAY':>8}  {'TOTAL':>8}")
    for v in major:
        w = f"{v.weight_pct:.1f}%" if v.weight_pct is not None else "—"
        lines.append(
            f"  {v.ticker:<7}{_money0(v.market_value):>10}  {w:>6}  "
            f"{_pct(v.day_change_pct):>8}  "
            f"{_pct(v.total_pnl_pct) if v.total_pnl_pct is not None else '—':>8}"
        )
    dust_line = _dust_summary(dust)
    if dust_line:
        lines.append(f"  {dust_line}")

    d_gain, d_lose = biggest_dollar_movers(views)
    p_gain, p_lose = biggest_movers(views)
    if d_gain or d_lose:
        lines += ["", "Biggest movers by day P&L:"]
        for v in d_gain + d_lose:
            lines.append(
                f"  {v.ticker:<7}{v.day_pnl:>+10,.0f}  ({_pct(v.day_change_pct)})"
            )
    if p_gain or p_lose:
        lines += ["", "Biggest movers by day %:"]
        for v in p_gain + p_lose:
            pnl = f"  ({v.day_pnl:+,.0f})" if v.day_pnl is not None else ""
            lines.append(f"  {v.ticker:<7}{_pct(v.day_change_pct):>10}{pnl}")
    if iv_context:
        lines += ["", "Options premium, top positions (IV rank, proxy):"]
        for r in iv_context:
            lines.append(
                f"  {r.ticker:<7}{r.iv_rank * 100:>4.0f}  — {_iv_note(r.iv_rank)}"
            )
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

    cards.append(_positions_table(views))

    if d_gain or d_lose or p_gain or p_lose:
        mover_rows = []
        if d_gain or d_lose:
            mover_rows.append(_row("<b>By day P&L</b>", "#555"))
            mover_rows += [
                _row(
                    f"<b>{html.escape(v.ticker)}</b> {v.day_pnl:+,.0f}"
                    f" ({_pct(v.day_change_pct)})",
                    GREEN if v.day_pnl > 0 else RED,
                )
                for v in d_gain + d_lose
            ]
        if p_gain or p_lose:
            mover_rows.append(_row("<b>By day %</b>", "#555"))
            mover_rows += [
                _row(
                    f"<b>{html.escape(v.ticker)}</b> {_pct(v.day_change_pct)}"
                    + (
                        f" ({v.day_pnl:+,.0f})"
                        if v.day_pnl is not None
                        else ""
                    ),
                    GREEN if v.day_change_pct > 0 else RED,
                )
                for v in p_gain + p_lose
            ]
        cards.append(_card("Biggest movers", mover_rows))
    if iv_context:
        cards.append(_card("Options premium, top positions (IV rank, proxy)", [
            _row(
                f"<b>{html.escape(r.ticker)}</b> {r.iv_rank * 100:.0f}"
                f" &middot; {html.escape(_iv_note(r.iv_rank))}"
            )
            for r in iv_context
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
