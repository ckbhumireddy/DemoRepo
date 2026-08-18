"""The daily martingale report email (pure renderer)."""

from __future__ import annotations

import datetime as dt
import html
from typing import Optional, Tuple

from .engine import MartingaleSummary

GREEN = "#1a7f37"
RED = "#b02a2a"
AMBER = "#b26a00"

_WRAP = (
    "<div style=\"font-family:Arial,Helvetica,sans-serif;color:#222;"
    "font-size:14px;max-width:640px\">"
)

DISCLAIMER = (
    "Martingale sizing doubles the stake after every losing day, so one "
    "long losing streak can wipe out the account — this paper trader "
    "exists to demonstrate that failure mode. Paper money only. Closes "
    "are from the Schwab Trader API. Not investment advice."
)

_TH = (
    "padding:4px 8px;border-bottom:1px solid #bbb;text-align:right;"
    "font-size:12px;color:#555"
)
_TD = "padding:3px 8px;border-bottom:1px solid #eee;text-align:right"


def _money(x: float) -> str:
    return f"-${abs(x):,.2f}" if x < 0 else f"${x:,.2f}"


def _signed(x: float) -> str:
    return f"{'+' if x >= 0 else '-'}${abs(x):,.2f}"


def _pnl_html(x: float) -> str:
    color = GREEN if x >= 0 else RED
    return f"<span style='color:{color}'><b>{_signed(x)}</b></span>"


def render_daily_email(
    summary: MartingaleSummary,
    settled: Optional[dict],
    opened: Optional[dict],
    today: dt.date,
    *,
    symbol_label: str = "SPX",
) -> Tuple[str, str, str]:
    day_label = today.strftime("%a %b %d")

    if summary.busted:
        subject = (
            f"[MG] {symbol_label} martingale BUSTED — balance "
            f"{_money(summary.balance)} after {summary.rounds_total} rounds"
        )
    elif settled:
        subject = (
            f"[MG] {symbol_label} martingale — {day_label}: "
            f"{_signed(settled['pnl'])} · balance {_money(summary.balance)} "
            f"· step {summary.step}"
        )
    else:
        subject = (
            f"[MG] {symbol_label} martingale — {day_label}: first round "
            f"opened at {opened['entry_price']:,.2f}"
        )

    # ---- text ----
    lines = [f"{symbol_label} martingale paper trader ({day_label})", ""]
    if settled:
        lines += [
            "Settled round:",
            f"  {settled['entry_date']} -> {settled['exit_date']}  "
            f"{settled['entry_price']:,.2f} -> {settled['exit_price']:,.2f}  "
            f"({settled['return_pct']:+.2f}%)",
            f"  Notional {_money(settled['notional'])} (step {settled['step']})"
            f"  P&L {_signed(settled['pnl'])}",
            "",
        ]
    if summary.busted:
        lines += [
            "THE ACCOUNT IS BUSTED. The balance is at or below $0, so no",
            "new rounds open. This is how a martingale ends.",
            "",
        ]
    elif opened:
        lev = summary.leverage
        lines += [
            "Next round (open):",
            f"  Long {symbol_label} at {opened['entry_price']:,.2f}, notional "
            f"{_money(opened['notional'])} (step {opened['step']}"
            + (f", {lev:.1f}x the balance" if lev is not None else "")
            + ")",
            "",
        ]
    lines += [
        "Account:",
        f"  Balance {_money(summary.balance)}  (start "
        f"{_money(summary.start_balance)}, total {_signed(summary.total_pnl)})",
        f"  Rounds {summary.rounds_total}: {summary.wins} wins, "
        f"{summary.losses} losses, {summary.pushes} flat"
        + (
            f"  (win rate {summary.win_rate * 100:.0f}%)"
            if summary.win_rate is not None
            else ""
        ),
        f"  Loss streak: current {summary.current_loss_streak}, "
        f"max {summary.max_loss_streak}",
        f"  Max drawdown {_money(summary.max_drawdown)}",
    ]
    if summary.recent:
        lines += [
            "",
            "Recent rounds (newest first):",
            f"  {'EXIT':<11}{'NOTIONAL':>10} {'STEP':>4} {'RET':>7} "
            f"{'P&L':>11} {'BALANCE':>11}",
        ]
        for r in summary.recent:
            lines.append(
                f"  {r['exit_date']:<11}{r['notional']:>10,.0f} {r['step']:>4} "
                f"{r['return_pct']:>+6.2f}% {_signed(r['pnl']):>11} "
                f"{r['balance_after']:>11,.2f}"
            )
    lines += ["", DISCLAIMER]
    text = "\n".join(lines)

    # ---- html ----
    parts = [
        _WRAP,
        f"<h2 style='margin:0 0 4px'>{html.escape(symbol_label)} martingale "
        "paper trader</h2>",
        "<p style='margin:0 0 16px;color:#555'>"
        + html.escape(today.strftime("%a %b %d, %Y"))
        + "</p>",
    ]
    if settled:
        parts.append(
            "<div style='border:1px solid #d0d0d0;border-radius:8px;"
            "padding:10px 14px;margin:10px 0'>"
            "<div style='font-size:15px'><b>Settled round</b></div>"
            f"<div style='margin-top:4px'>{settled['entry_date']} &rarr; "
            f"{settled['exit_date']} &middot; {settled['entry_price']:,.2f} "
            f"&rarr; {settled['exit_price']:,.2f} "
            f"({settled['return_pct']:+.2f}%)</div>"
            f"<div>Notional {_money(settled['notional'])} (step "
            f"{settled['step']}) &middot; P&amp;L {_pnl_html(settled['pnl'])}"
            "</div></div>"
        )
    if summary.busted:
        parts.append(
            f"<div style='border:2px solid {RED};border-radius:8px;"
            "padding:10px 14px;margin:10px 0'>"
            f"<div style='font-size:15px;color:{RED}'><b>THE ACCOUNT IS "
            "BUSTED</b></div>"
            "<div style='margin-top:4px'>The balance is at or below $0, so "
            "no new rounds open. This is how a martingale ends.</div></div>"
        )
    elif opened:
        lev = summary.leverage
        parts.append(
            "<div style='border:1px solid #d0d0d0;border-radius:8px;"
            "padding:10px 14px;margin:10px 0'>"
            "<div style='font-size:15px'><b>Next round (open)</b></div>"
            f"<div style='margin-top:4px'>Long {html.escape(symbol_label)} at "
            f"{opened['entry_price']:,.2f} &middot; notional "
            f"<b>{_money(opened['notional'])}</b> (step {opened['step']}"
            + (f", <b>{lev:.1f}x</b> the balance" if lev is not None else "")
            + ")</div></div>"
        )
    win_rate = (
        f" &middot; win rate {summary.win_rate * 100:.0f}%"
        if summary.win_rate is not None
        else ""
    )
    parts.append(
        "<div style='border:1px solid #d0d0d0;border-radius:8px;"
        "padding:10px 14px;margin:10px 0'>"
        "<div style='font-size:15px'><b>Account</b></div>"
        f"<div style='margin-top:4px'>Balance <b>{_money(summary.balance)}</b> "
        f"(start {_money(summary.start_balance)}, total "
        f"{_pnl_html(summary.total_pnl)})</div>"
        f"<div>Rounds {summary.rounds_total}: {summary.wins} wins, "
        f"{summary.losses} losses, {summary.pushes} flat{win_rate}</div>"
        f"<div>Loss streak: current <b>{summary.current_loss_streak}</b>, "
        f"max {summary.max_loss_streak} &middot; max drawdown "
        f"{_money(summary.max_drawdown)}</div></div>"
    )
    if summary.recent:
        head = (
            "<tr>"
            f"<th style='{_TH};text-align:left'>Exit</th>"
            f"<th style='{_TH}'>Notional</th>"
            f"<th style='{_TH}'>Step</th>"
            f"<th style='{_TH}'>Return</th>"
            f"<th style='{_TH}'>P&amp;L</th>"
            f"<th style='{_TH}'>Balance</th>"
            "</tr>"
        )
        rows = "".join(
            "<tr>"
            f"<td style='{_TD};text-align:left'>{r['exit_date']}</td>"
            f"<td style='{_TD}'>${r['notional']:,.0f}</td>"
            f"<td style='{_TD}'>{r['step']}</td>"
            f"<td style='{_TD}'>{r['return_pct']:+.2f}%</td>"
            f"<td style='{_TD}'>{_pnl_html(r['pnl'])}</td>"
            f"<td style='{_TD}'>${r['balance_after']:,.2f}</td>"
            "</tr>"
            for r in summary.recent
        )
        parts.append(
            "<div style='font-size:15px;margin:14px 0 4px'><b>Recent rounds"
            "</b></div>"
            "<div style='overflow-x:auto'>"
            "<table style='border-collapse:collapse;width:100%;font-size:13px'>"
            + head + rows + "</table></div>"
        )
    parts.append(
        f"<p style='margin-top:16px;color:#777;font-size:12px'>"
        f"{html.escape(DISCLAIMER)}</p></div>"
    )
    html_body = "".join(parts)
    return subject, text, html_body
