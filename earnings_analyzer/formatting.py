"""Render the trade sheet as email subject + plain-text + HTML bodies.

Pure functions only — no IO — matching the calendar digest's mobile-friendly
stacked-card style.
"""

from __future__ import annotations

import datetime as dt
import html
from typing import List, Optional, Tuple
from urllib.parse import quote

from .models import PricedStrategy, TickerAnalysis

GREEN = "#1a7f37"
RED = "#c62828"
AMBER = "#b26a00"

_GRADE_COLORS = {
    "A+": GREEN, "A": GREEN, "B": "#2b6cb0", "C": AMBER, "D": RED, "F": RED,
}

_COMPONENT_LABELS = {
    "earnings_quality": "quality",
    "vol_edge": "vol",
    "liquidity": "liq",
    "reaction_consistency": "react",
    "trend_alignment": "trend",
}


def _quote_url(ticker: str) -> str:
    return f"https://finance.yahoo.com/quote/{quote(ticker)}"


def _weekday(d: dt.date) -> str:
    return d.strftime("%a %b %d, %Y")


def _fmt_price(value: Optional[float]) -> str:
    return "—" if value is None else f"${value:,.2f}"


def _fmt_market_cap(value: Optional[float]) -> str:
    if value is None:
        return "—"
    for threshold, suffix in ((1e12, "T"), (1e9, "B"), (1e6, "M")):
        if value >= threshold:
            return f"${value / threshold:,.2f}{suffix}"
    return f"${value:,.0f}"


def _fmt_signed(value: Optional[float], digits: int = 1) -> str:
    return "—" if value is None else f"{value:+.{digits}f}%"


def _a_or_better(analyses: List[TickerAnalysis]) -> int:
    return sum(
        1 for a in analyses if a.rating and a.rating.grade in {"A+", "A"}
    )


def render_subject(analyses: List[TickerAnalysis], today: dt.date) -> str:
    n = len(analyses)
    if n == 0:
        return "Earnings trade sheet: no setups today"
    noun = "setup" if n == 1 else "setups"
    return f"Earnings trade sheet: {n} {noun}, {_a_or_better(analyses)} rated A or better"


# --------------------------------------------------------------------------- #
# Shared line builders
# --------------------------------------------------------------------------- #
def _history_line(a: TickerAnalysis) -> Optional[str]:
    h = a.history
    if h is None:
        return None
    beats = round((h.beat_rate or 0) * h.quarters)
    streak = f"{h.streak:+d}" if h.streak else "0"
    return (
        f"History: beat {beats}/{h.quarters} ({(h.beat_rate or 0) * 100:.0f}%)"
        f" · avg surprise {_fmt_signed(h.avg_surprise_pct)} · streak {streak}"
    )


def _reaction_line(a: TickerAnalysis) -> Optional[str]:
    r = a.reactions
    if r is None:
        return None
    return (
        f"Reactions ({r.samples}q): avg ±{r.avg_abs_move_pct:.1f}%"
        f" · up {(r.up_rate or 0) * 100:.0f}%"
        f" · best {_fmt_signed(r.best_pct)} / worst {_fmt_signed(r.worst_pct)}"
    )


def _vol_line(a: TickerAnalysis) -> Optional[str]:
    m = a.implied
    if m is None:
        return None
    parts = [f"Implied ±{m.implied_move_pct:.1f}% (exp {m.expiry.isoformat()})"]
    if m.historical_avg_abs_move_pct:
        parts.append(f"vs hist ±{m.historical_avg_abs_move_pct:.1f}%")
    if m.ratio is not None:
        parts.append(f"→ {m.ratio:.2f}x {m.verdict.upper()}")
    elif m.diluted:
        parts.append("(expiry too far past event to judge)")
    if a.iv_rank is not None:
        parts.append(f"· IV rank {a.iv_rank.rank_pct:.0f} (proxy)")
    return "Vol: " + " ".join(parts)


def _liquidity_line(a: TickerAnalysis) -> Optional[str]:
    liq = a.liquidity
    if liq is None:
        return "Liquidity: options data unavailable"
    if not liq.tradeable:
        return "NO TRADE — " + "; ".join(liq.reasons)
    return (
        f"Liquidity: OI {liq.atm_open_interest:.0f}"
        f" · vol {liq.atm_volume:.0f}"
        f" · spread {liq.atm_spread_pct:.1f}%"
    )


def _legs_text(s: PricedStrategy) -> str:
    multi_expiry = len({l.expiry for l in s.legs}) > 1
    parts = []
    for l in s.legs:
        piece = f"{l.action.title()} {l.strike:g}{'C' if l.option_type == 'call' else 'P'}"
        if multi_expiry:
            piece += f" ({l.expiry.isoformat()})"
        parts.append(piece)
    text = " / ".join(parts)
    if not multi_expiry and s.legs:
        text += f" exp {s.legs[0].expiry.isoformat()}"
    return text


def _strategy_lines(s: PricedStrategy) -> List[str]:
    premium = (
        f"credit ${s.net_premium:.2f}"
        if s.is_credit
        else f"debit ${-s.net_premium:.2f}"
    )
    figures = [premium]
    if s.max_profit is not None:
        figures.append(f"max gain ${s.max_profit:.2f}")
    if s.max_loss is not None:
        figures.append(f"max loss ${s.max_loss:.2f}")
    if s.breakevens:
        figures.append("BE " + "/".join(f"{b:g}" for b in s.breakevens))
    return [
        f"{s.strategy} ({s.outlook}): {_legs_text(s)}",
        " · ".join(figures),
    ]


def _rating_breakdown(a: TickerAnalysis) -> Optional[str]:
    r = a.rating
    if r is None or not r.components:
        return None
    parts = [
        f"{_COMPONENT_LABELS.get(k, k)} {v:.0f}"
        for k, v in r.components.items()
    ]
    return f"Rating mix: {' · '.join(parts)} (coverage {r.coverage * 100:.0f}%)"


# --------------------------------------------------------------------------- #
# Plain text
# --------------------------------------------------------------------------- #
def render_text(analyses: List[TickerAnalysis], today: dt.date) -> str:
    lines = [
        "Earnings trade sheet",
        f"(generated {_weekday(today)})",
        "",
    ]
    if not analyses:
        lines.append("No upcoming earnings to analyze.")
        return "\n".join(lines)

    for a in analyses:
        grade = a.rating.grade if a.rating else "?"
        score = f"{a.rating.score:.0f}" if a.rating else "—"
        name = f"  {a.snapshot.company}" if a.snapshot and a.snapshot.company else ""
        days = (a.event_date - today).days
        lines.append(f"- [{grade} {score}] {a.ticker}{name}")
        lines.append(f"    Reports {_weekday(a.event_date)}  (in {days} day(s))")
        snap = a.snapshot
        lines.append(
            f"    Price {_fmt_price(a.price)}"
            f" | Mkt cap {_fmt_market_cap(snap.market_cap if snap else None)}"
            + (
                f" | Fwd P/E {snap.forward_pe:.1f}"
                if snap and snap.forward_pe is not None
                else ""
            )
        )
        for line in (
            _history_line(a), _reaction_line(a), _vol_line(a), _liquidity_line(a)
        ):
            if line:
                lines.append(f"    {line}")
        for s in a.strategies:
            first, second = _strategy_lines(s)
            lines.append(f"    ▶ {first}")
            lines.append(f"      {second}")
        breakdown = _rating_breakdown(a)
        if breakdown:
            lines.append(f"    {breakdown}")
        lines.append(f"    {_quote_url(a.ticker)}")
        lines.append("")

    lines.append(f"{len(analyses)} setup(s) analyzed.")
    lines.append("")
    lines.append(
        "Educational analysis only — not investment advice. Quotes are Yahoo "
        "Finance mids and may be delayed or stale; options involve substantial "
        "risk; verify everything before trading."
    )
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# HTML (stacked cards, matching the calendar digest style)
# --------------------------------------------------------------------------- #
def render_html(analyses: List[TickerAnalysis], today: dt.date) -> str:
    header = (
        "<h2 style='margin:0 0 4px'>Earnings trade sheet</h2>"
        f"<p style='margin:0 0 16px;color:#555'>Generated {html.escape(_weekday(today))}</p>"
    )
    if not analyses:
        body = "<p>No upcoming earnings to analyze.</p>"
    else:
        body = "".join(_card(a, today) for a in analyses)

    footer = (
        "<p style='margin-top:16px;color:#777;font-size:12px'>"
        f"{len(analyses)} setup(s). Educational analysis only — not investment "
        "advice. Quotes are Yahoo Finance mids and may be delayed or stale; "
        "options involve substantial risk; verify everything before trading.</p>"
    )
    return (
        "<div style=\"font-family:Arial,Helvetica,sans-serif;color:#222;"
        "font-size:14px;max-width:640px\">"
        + header + body + footer + "</div>"
    )


def _card(a: TickerAnalysis, today: dt.date) -> str:
    grade = a.rating.grade if a.rating else "?"
    score = f"{a.rating.score:.0f}" if a.rating else "—"
    color = _GRADE_COLORS.get(grade, "#555")
    days = (a.event_date - today).days
    name = (
        f" <span style='color:#555;font-weight:normal'>&middot; "
        f"{html.escape(a.snapshot.company)}</span>"
        if a.snapshot and a.snapshot.company
        else ""
    )
    chip = (
        f"<span style='background:{color};color:#fff;border-radius:4px;"
        f"padding:1px 7px;font-weight:bold'>{html.escape(grade)} {score}</span>"
    )
    title = (
        f"<div style='font-size:15px'>{chip} "
        f"<a href='{html.escape(_quote_url(a.ticker))}' "
        f"style='color:#0b57d0;text-decoration:none'>"
        f"<b>{html.escape(a.ticker)}</b></a>{name}</div>"
    )
    snap = a.snapshot
    fwd_pe = (
        f" &nbsp;&middot;&nbsp; Fwd P/E {snap.forward_pe:.1f}"
        if snap and snap.forward_pe is not None
        else ""
    )
    rows = [
        f"<div style='margin-top:2px'>Reports <b>{html.escape(_weekday(a.event_date))}</b>"
        f" &middot; in {days} day(s)</div>",
        f"<div style='margin-top:6px'>Price <b>{html.escape(_fmt_price(a.price))}</b>"
        f" &nbsp;&middot;&nbsp; Mkt cap "
        f"<b>{html.escape(_fmt_market_cap(snap.market_cap if snap else None))}</b>"
        f"{fwd_pe}</div>",
    ]
    for line in (_history_line(a), _reaction_line(a), _vol_line(a)):
        if line:
            rows.append(
                f"<div style='margin-top:4px;color:#444'>{html.escape(line)}</div>"
            )
    liq_line = _liquidity_line(a)
    if liq_line:
        liq_color = RED if liq_line.startswith("NO TRADE") else "#444"
        weight = "bold" if liq_line.startswith("NO TRADE") else "normal"
        rows.append(
            f"<div style='margin-top:4px;color:{liq_color};font-weight:{weight}'>"
            f"{html.escape(liq_line)}</div>"
        )
    for s in a.strategies:
        first, second = _strategy_lines(s)
        rows.append(
            "<div style='margin-top:8px;padding:6px 10px;background:#f6f8fa;"
            "border-radius:6px'>"
            f"<div><b>{html.escape(first)}</b></div>"
            f"<div style='color:#444'>{html.escape(second)}</div>"
            "</div>"
        )
    breakdown = _rating_breakdown(a)
    if breakdown:
        rows.append(
            f"<div style='margin-top:6px;color:#777;font-size:12px'>"
            f"{html.escape(breakdown)}</div>"
        )
    return (
        "<div style='border:1px solid #d0d0d0;border-radius:8px;"
        "padding:10px 14px;margin:0 0 10px'>"
        + title + "".join(rows) + "</div>"
    )


def render_email(
    analyses: List[TickerAnalysis], today: dt.date
) -> Tuple[str, str, str]:
    """Return ``(subject, text_body, html_body)``."""
    return (
        render_subject(analyses, today),
        render_text(analyses, today),
        render_html(analyses, today),
    )
