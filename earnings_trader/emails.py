"""The trader's three email types (pure renderers).

All subjects carry the ``[Paper]`` prefix so inbox filters can group them.
Every renderer returns ``(subject, text_body, html_body)``.
"""

from __future__ import annotations

import datetime as dt
import html
from typing import List, Optional, Tuple

from .candidates import SkippedCandidate
from .ledger import TraderSummary
from .pipeline import TradeDecision

GREEN = "#1a7f37"
RED = "#c62828"

_WRAP = (
    "<div style=\"font-family:Arial,Helvetica,sans-serif;color:#222;"
    "font-size:14px;max-width:640px\">"
)


def _weekday(d: dt.date) -> str:
    return d.strftime("%a %b %d, %Y")


def _money(v: float) -> str:
    return f"${v:,.2f}"


def _signed(v: float) -> str:
    return f"{v:+,.2f}"


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


def _rationale(d: TradeDecision) -> str:
    parts = []
    if d.implied is not None:
        hist = d.implied.historical_avg_abs_move_pct
        parts.append(
            f"implied ±{d.implied.implied_move_pct:.1f}%"
            + (f" vs ±{hist:.1f}% hist" if hist else "")
            + (
                f" (ratio {d.implied.ratio:.2f}, {d.implied.verdict})"
                if d.implied.ratio is not None
                else f" ({d.implied.verdict})"
            )
        )
    if d.trend is not None:
        parts.append(f"trend {d.trend.bias}")
    return " · ".join(parts)


def _pos_rationale(pos: dict) -> str:
    s = pos.get("signals") or {}
    parts = []
    if s.get("implied_move_pct") is not None:
        line = f"implied ±{s['implied_move_pct']:.1f}%"
        if s.get("ratio") is not None:
            line += f" (ratio {s['ratio']:.2f}, {s.get('verdict')})"
        parts.append(line)
    if s.get("bias"):
        parts.append(f"trend {s['bias']}")
    return " · ".join(parts)


# --------------------------------------------------------------------------- #
# Daily plan + performance
# --------------------------------------------------------------------------- #
def render_plan_email(
    summary: TraderSummary,
    decisions: List[TradeDecision],
    skipped: List[SkippedCandidate],
    today: dt.date,
    *,
    window_note: Optional[str] = None,
) -> Tuple[str, str, str]:
    planned = [d for d in decisions if d.action == "open"]
    subject = (
        f"[Paper] Daily plan {today.strftime('%a %b %d')} — "
        f"{len(planned)} planned trade(s) · balance {_money(summary.balance)}"
    )

    # ---- text ----
    lines = [
        f"Paper trader daily plan ({_weekday(today)})",
        "",
        f"Performance, last {summary.window_days} days:",
        f"  Realized P&L {_signed(summary.window_pnl)}"
        + (
            f" · {summary.window_wins}/{summary.window_closed} wins"
            f" ({summary.window_win_rate * 100:.0f}%)"
            if summary.window_closed
            else " · no closed trades"
        ),
        f"  Balance {_money(summary.balance)}"
        f" ({_signed(summary.total_pnl)} since {_money(summary.start_balance)} start)",
    ]
    if summary.closed_this_run:
        lines += ["", "Closed this morning:"]
        for p in summary.closed_this_run:
            lines.append(
                f"  {p['ticker']} {p['strategy']} x{p['qty']}: "
                f"{_signed(p['realized_pnl'])}"
            )
    if summary.open_positions:
        lines += ["", f"Open positions (risk {_money(summary.open_risk)}):"]
        for p in summary.open_positions:
            lines.append(
                f"  {p['ticker']} {p['strategy']} x{p['qty']}"
                f" (reports {p['event_date']}, risk ${p['risk_dollars']:,.0f})"
            )
    lines += ["", "Today's plan (final decision at the ~15:35 ET entry run):"]
    if planned:
        for d in planned:
            s = d.strategy
            lines.append(
                f"  {d.candidate.ticker} — {s.strategy}"
                f" ({d.candidate.session}, {_rationale(d)})"
            )
    else:
        lines.append("  No trades planned.")
    skips = [d for d in decisions if d.action == "skip"]
    if skips or skipped:
        lines += ["", "Skipped:"]
        for d in skips:
            lines.append(f"  {d.candidate.ticker}: {'; '.join(d.reasons)}")
        for s in skipped:
            lines.append(f"  {s.ticker}: {s.reason}")
    if window_note:
        lines += ["", window_note]
    lines += ["", "Paper trading only — no real orders are placed."]
    text = "\n".join(lines)

    # ---- html ----
    perf_rows = [
        _row(
            f"Realized P&L <b>{_signed(summary.window_pnl)}</b>"
            + (
                f" &middot; {summary.window_wins}/{summary.window_closed} wins"
                f" ({summary.window_win_rate * 100:.0f}%)"
                if summary.window_closed
                else " &middot; no closed trades"
            )
        ),
        _row(
            f"Balance <b>{_money(summary.balance)}</b>"
            f" ({_signed(summary.total_pnl)} since {_money(summary.start_balance)})"
        ),
    ]
    cards = [_card(f"Performance, last {summary.window_days} days", perf_rows)]
    if summary.closed_this_run:
        cards.append(_card("Closed this morning", [
            _row(
                f"{html.escape(p['ticker'])} {html.escape(p['strategy'])}"
                f" x{p['qty']}: <b>{_signed(p['realized_pnl'])}</b>",
                GREEN if (p["realized_pnl"] or 0) > 0 else RED,
            )
            for p in summary.closed_this_run
        ]))
    if summary.open_positions:
        cards.append(_card(f"Open positions (risk {_money(summary.open_risk)})", [
            _row(
                f"{html.escape(p['ticker'])} {html.escape(p['strategy'])}"
                f" x{p['qty']} (reports {html.escape(p['event_date'])},"
                f" risk ${p['risk_dollars']:,.0f})"
            )
            for p in summary.open_positions
        ]))
    plan_rows = (
        [
            _row(
                f"<b>{html.escape(d.candidate.ticker)}</b> — "
                f"{html.escape(d.strategy.strategy)}"
                f" ({html.escape(d.candidate.session)}, {html.escape(_rationale(d))})"
            )
            for d in planned
        ]
        if planned
        else [_row("No trades planned.")]
    )
    plan_rows.append(_row(
        "Final decision at the ~15:35 ET entry run.", "#777"
    ))
    cards.append(_card("Today's plan", plan_rows))
    if skips or skipped:
        cards.append(_card("Skipped", [
            _row(f"{html.escape(d.candidate.ticker)}: "
                 f"{html.escape('; '.join(d.reasons))}")
            for d in skips
        ] + [
            _row(f"{html.escape(s.ticker)}: {html.escape(s.reason)}")
            for s in skipped
        ]))
    note = (
        f"<p style='color:#b26a00;font-size:12px'>{html.escape(window_note)}</p>"
        if window_note
        else ""
    )
    html_body = (
        _WRAP
        + f"<h2 style='margin:0 0 4px'>Paper trader daily plan</h2>"
        + f"<p style='margin:0 0 16px;color:#555'>{html.escape(_weekday(today))}</p>"
        + "".join(cards)
        + note
        + "<p style='margin-top:16px;color:#777;font-size:12px'>"
        "Paper trading only — no real orders are placed.</p></div>"
    )
    return subject, text, html_body


# --------------------------------------------------------------------------- #
# Per-trade notifications
# --------------------------------------------------------------------------- #
def render_open_email(pos: dict) -> Tuple[str, str, str]:
    subject = (
        f"[Paper] Opened {pos['ticker']} {pos['strategy']}"
        f" x{pos['qty']} (risk ${pos['risk_dollars']:,.0f})"
    )
    premium = pos["net_premium"]
    prem_line = (
        f"Net credit ${premium:.2f}/share"
        if premium >= 0
        else f"Net debit ${-premium:.2f}/share"
    )
    lines = [
        f"{pos['ticker']} reports {pos['event_date']} ({pos['timing']}).",
        "",
        "Legs:",
    ]
    for leg in pos["legs"]:
        lines.append(
            f"  {leg['action']:<4} {leg['option_type']:<4}"
            f" {leg['strike']:g}  exp {leg['expiry']}"
            f"  @ ${leg['open_price']:.2f}"
        )
    lines += [
        "",
        f"{prem_line} · max loss ${pos['max_loss']:.2f}/share"
        f" · {pos['qty']} contract(s) · risk ${pos['risk_dollars']:,.0f}",
        f"Spot at entry {_money(pos['open_spot'])}",
        f"Why: {_pos_rationale(pos)}",
        "",
        "Paper trading only — no real orders are placed.",
    ]
    text = "\n".join(lines)

    leg_rows = [
        _row(
            f"{html.escape(leg['action'])} {html.escape(leg['option_type'])} "
            f"<b>{leg['strike']:g}</b> exp {html.escape(leg['expiry'])}"
            f" @ ${leg['open_price']:.2f}"
        )
        for leg in pos["legs"]
    ]
    html_body = (
        _WRAP
        + f"<h2 style='margin:0 0 8px'>Opened {html.escape(pos['ticker'])} "
        f"{html.escape(pos['strategy'])} x{pos['qty']}</h2>"
        + _row(
            f"Reports {html.escape(pos['event_date'])}"
            f" ({html.escape(pos['timing'] or 'unknown')})"
        )
        + _card("Legs", leg_rows)
        + _row(
            f"{html.escape(prem_line)} &middot; max loss "
            f"${pos['max_loss']:.2f}/share &middot; risk "
            f"<b>${pos['risk_dollars']:,.0f}</b>"
        )
        + _row(f"Spot at entry {_money(pos['open_spot'])}")
        + _row(f"Why: {html.escape(_pos_rationale(pos))}")
        + "<p style='margin-top:16px;color:#777;font-size:12px'>"
        "Paper trading only — no real orders are placed.</p></div>"
    )
    return subject, text, html_body


def render_close_email(pos: dict, summary: TraderSummary) -> Tuple[str, str, str]:
    pnl = pos["realized_pnl"] or 0.0
    pct = pnl / summary.balance * 100.0 if summary.balance else 0.0
    subject = (
        f"[Paper] Closed {pos['ticker']} {pos['strategy']}"
        f" x{pos['qty']}: {_signed(pnl)} ({pct:+.1f}% of account)"
    )
    record = (
        f"{summary.wins}-{summary.losses}"
        + (
            f" ({summary.win_rate * 100:.0f}% win rate)"
            if summary.closed_total
            else ""
        )
    )
    lines = [
        f"{pos['ticker']} {pos['strategy']} x{pos['qty']}"
        f" (event {pos['event_date']}, opened {pos['open_date']}).",
        "",
        f"Realized P&L: {_signed(pnl)}",
        f"Close value {pos['close_value']:+.2f}/share"
        f" vs net premium {pos['net_premium']:+.2f}/share"
        f" · valued via {pos['close_method']}",
        f"New balance: {_money(summary.balance)}"
        f" ({_signed(summary.total_pnl)} total)",
        f"Record: {record}",
        "",
        "Paper trading only — no real orders are placed.",
    ]
    text = "\n".join(lines)

    color = GREEN if pnl > 0 else RED
    html_body = (
        _WRAP
        + f"<h2 style='margin:0 0 8px'>Closed {html.escape(pos['ticker'])} "
        f"{html.escape(pos['strategy'])} x{pos['qty']}</h2>"
        + _row(
            f"Realized P&L <b style='color:{color}'>{_signed(pnl)}</b>"
            f" ({pct:+.1f}% of account)"
        )
        + _row(
            f"Close value {pos['close_value']:+.2f}/share vs net premium "
            f"{pos['net_premium']:+.2f}/share &middot; valued via "
            f"{html.escape(pos['close_method'] or '')}"
        )
        + _row(
            f"New balance <b>{_money(summary.balance)}</b>"
            f" ({_signed(summary.total_pnl)} total) &middot; record {record}"
        )
        + "<p style='margin-top:16px;color:#777;font-size:12px'>"
        "Paper trading only — no real orders are placed.</p></div>"
    )
    return subject, text, html_body
