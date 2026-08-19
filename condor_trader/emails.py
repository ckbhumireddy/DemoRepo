"""The condor-martingale report email (pure renderer)."""

from __future__ import annotations

import datetime as dt
import html
from typing import List, Optional, Tuple

from .engine import CondorSummary

GREEN = "#1a7f37"
RED = "#b02a2a"

_WRAP = (
    "<div style=\"font-family:Arial,Helvetica,sans-serif;color:#222;"
    "font-size:14px;max-width:640px\">"
)

DISCLAIMER = (
    "Martingale sizing multiplies the contract count after every losing "
    "week; an iron condor's loss is several times its credit, so one bad "
    "streak escalates fast — this paper trader exists to demonstrate that. "
    "Paper money only. Chains and closes are from the Schwab Trader API. "
    "Not investment advice."
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
    return f"<span style='color:{GREEN if x >= 0 else RED}'><b>{_signed(x)}</b></span>"


def _strikes(p: dict) -> str:
    return (f"{p['long_put']:g}/{p['short_put']:g}P "
            f"{p['short_call']:g}/{p['long_call']:g}C")


def render_report_email(
    summary: CondorSummary,
    settled: List[dict],
    opened: Optional[dict],
    today: dt.date,
    *,
    symbol_label: str = "SPX",
) -> Tuple[str, str, str]:
    day_label = today.strftime("%a %b %d")

    if summary.busted:
        subject = (
            f"[IC] {symbol_label} condor martingale BUSTED — balance "
            f"{_money(summary.balance)} after {summary.settled_total} condors"
        )
    elif settled:
        pnl = sum(p["realized_pnl"] for p in settled)
        subject = (
            f"[IC] {symbol_label} condor martingale — {day_label}: "
            f"{_signed(pnl)} settled · balance {_money(summary.balance)} "
            f"· step {summary.step}"
        )
    else:
        subject = (
            f"[IC] {symbol_label} condor martingale — {day_label}: opened "
            f"{_strikes(opened)} x{opened['qty']} for "
            f"{_money(opened['credit'] * 100 * opened['qty'])} credit"
        )

    # ---- text ----
    lines = [f"{symbol_label} iron-condor martingale paper trader ({day_label})", ""]
    for p in settled:
        lines += [
            "Settled:",
            f"  {p['expiry']}  {_strikes(p)} x{p['qty']} (step {p['step']})",
            f"  Settle {p['settle_price']:,.2f}  credit {p['credit']:.2f}  "
            f"P&L {_signed(p['realized_pnl'])}",
            "",
        ]
    if summary.busted:
        lines += [
            "THE ACCOUNT IS BUSTED. The balance is at or below $0, so no",
            "new condors open. This is how a martingale ends.",
            "",
        ]
    elif opened:
        lines += [
            "Opened:",
            f"  {opened['expiry']}  {_strikes(opened)} x{opened['qty']} "
            f"(step {opened['step']})",
            f"  Credit {opened['credit']:.2f}/sh = "
            f"{_money(opened['max_profit'])} max profit; "
            f"risk {_money(opened['risk_dollars'])}",
            "",
        ]
    lines += [
        "Account:",
        f"  Balance {_money(summary.balance)}  (start "
        f"{_money(summary.start_balance)}, total {_signed(summary.total_pnl)})",
        f"  Condors {summary.settled_total}: {summary.wins} wins, "
        f"{summary.losses} losses"
        + (f"  (win rate {summary.win_rate * 100:.0f}%)"
           if summary.win_rate is not None else ""),
        f"  Loss streak: current {summary.current_loss_streak}, "
        f"max {summary.max_loss_streak}  ·  max drawdown "
        f"{_money(summary.max_drawdown)}",
    ]
    if summary.recent:
        lines += ["", "Recent condors (newest first):",
                  f"  {'EXPIRY':<11} {'STRIKES':<26} {'QTY':>4} {'P&L':>11}"]
        for p in summary.recent:
            lines.append(
                f"  {p['expiry']:<11} {_strikes(p):<26} {p['qty']:>4} "
                f"{_signed(p['realized_pnl']):>11}"
            )
    lines += ["", DISCLAIMER]
    text = "\n".join(lines)

    # ---- html ----
    parts = [
        _WRAP,
        f"<h2 style='margin:0 0 4px'>{html.escape(symbol_label)} iron-condor "
        "martingale</h2>",
        "<p style='margin:0 0 16px;color:#555'>"
        + html.escape(today.strftime("%a %b %d, %Y")) + "</p>",
    ]
    for p in settled:
        parts.append(
            "<div style='border:1px solid #d0d0d0;border-radius:8px;"
            "padding:10px 14px;margin:10px 0'>"
            f"<div style='font-size:15px'><b>Settled {p['expiry']}</b></div>"
            f"<div style='margin-top:4px'>{html.escape(_strikes(p))} "
            f"&times;{p['qty']} (step {p['step']}) &middot; settle "
            f"{p['settle_price']:,.2f} &middot; P&amp;L "
            f"{_pnl_html(p['realized_pnl'])}</div></div>"
        )
    if summary.busted:
        parts.append(
            f"<div style='border:2px solid {RED};border-radius:8px;"
            "padding:10px 14px;margin:10px 0'>"
            f"<div style='font-size:15px;color:{RED}'><b>THE ACCOUNT IS "
            "BUSTED</b></div>"
            "<div style='margin-top:4px'>The balance is at or below $0, so "
            "no new condors open. This is how a martingale ends.</div></div>"
        )
    elif opened:
        parts.append(
            "<div style='border:1px solid #d0d0d0;border-radius:8px;"
            "padding:10px 14px;margin:10px 0'>"
            f"<div style='font-size:15px'><b>Opened {opened['expiry']}</b></div>"
            f"<div style='margin-top:4px'>{html.escape(_strikes(opened))} "
            f"&times;<b>{opened['qty']}</b> (step {opened['step']}) &middot; "
            f"credit {opened['credit']:.2f}/sh = "
            f"<b>{_money(opened['max_profit'])}</b> max profit &middot; risk "
            f"<b>{_money(opened['risk_dollars'])}</b></div></div>"
        )
    win_rate = (f" &middot; win rate {summary.win_rate * 100:.0f}%"
                if summary.win_rate is not None else "")
    parts.append(
        "<div style='border:1px solid #d0d0d0;border-radius:8px;"
        "padding:10px 14px;margin:10px 0'>"
        "<div style='font-size:15px'><b>Account</b></div>"
        f"<div style='margin-top:4px'>Balance <b>{_money(summary.balance)}</b> "
        f"(start {_money(summary.start_balance)}, total "
        f"{_pnl_html(summary.total_pnl)})</div>"
        f"<div>Condors {summary.settled_total}: {summary.wins} wins, "
        f"{summary.losses} losses{win_rate}</div>"
        f"<div>Loss streak: current <b>{summary.current_loss_streak}</b>, "
        f"max {summary.max_loss_streak} &middot; max drawdown "
        f"{_money(summary.max_drawdown)}</div></div>"
    )
    if summary.recent:
        head = ("<tr>"
                f"<th style='{_TH};text-align:left'>Expiry</th>"
                f"<th style='{_TH};text-align:left'>Strikes</th>"
                f"<th style='{_TH}'>Qty</th>"
                f"<th style='{_TH}'>Settle</th>"
                f"<th style='{_TH}'>P&amp;L</th></tr>")
        rows = "".join(
            "<tr>"
            f"<td style='{_TD};text-align:left'>{p['expiry']}</td>"
            f"<td style='{_TD};text-align:left'>{html.escape(_strikes(p))}</td>"
            f"<td style='{_TD}'>{p['qty']}</td>"
            f"<td style='{_TD}'>{p['settle_price']:,.2f}</td>"
            f"<td style='{_TD}'>{_pnl_html(p['realized_pnl'])}</td></tr>"
            for p in summary.recent
        )
        parts.append(
            "<div style='font-size:15px;margin:14px 0 4px'><b>Recent condors"
            "</b></div><div style='overflow-x:auto'>"
            "<table style='border-collapse:collapse;width:100%;font-size:13px'>"
            + head + rows + "</table></div>"
        )
    parts.append(
        f"<p style='margin-top:16px;color:#777;font-size:12px'>"
        f"{html.escape(DISCLAIMER)}</p></div>"
    )
    return subject, text, "".join(parts)
