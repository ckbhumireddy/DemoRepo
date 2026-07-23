"""Rendering screened candidates for the terminal and as JSON."""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import date
from typing import List

from .data.models import Candidate


def _fmt_pct(x: float) -> str:
    return f"{x * 100:+.1f}%"


def render_table(candidates: List[Candidate]) -> str:
    if not candidates:
        return "No candidates matched the screen. Try relaxing thresholds."

    header = (
        f"{'#':>2}  {'TICKER':<7}{'SCORE':>6}{'QUALITY':>8}"
        f"{'CRASH':>8}{'NOW vs PRE':>12}{'DAYS':>6}{'IVR':>6}  NAME"
    )
    lines = [header, "-" * len(header)]
    for i, c in enumerate(candidates, 1):
        ivr = f"{c.iv_rank.rank_pct:.0f}" if c.iv_rank else "-"
        lines.append(
            f"{i:>2}  {c.ticker:<7}{c.composite_score:>6.1f}"
            f"{c.fundamental_score:>8.1f}"
            f"{_fmt_pct(c.crash.max_drop_pct):>8}"
            f"{_fmt_pct(c.crash.still_down_pct):>12}"
            f"{c.crash.days_since_earnings:>6}{ivr:>6}  {c.name[:32]}"
        )
    return "\n".join(lines)


def render_detail(c: Candidate) -> str:
    lines: List[str] = []
    lines.append("=" * 64)
    lines.append(f"{c.ticker} — {c.name}")
    if c.fundamentals.sector:
        lines.append(f"Sector: {c.fundamentals.sector}")
    lines.append("=" * 64)
    lines.append(
        f"Composite score : {c.composite_score:.1f}   "
        f"Quality: {c.fundamental_score:.1f}/100 "
        f"(data coverage {c.data_coverage * 100:.0f}%)"
    )
    lines.append("")
    lines.append("Earnings crash:")
    lines.append(
        f"  Report date     : {c.crash.earnings_date}  "
        f"({c.crash.days_since_earnings} days ago)"
    )
    lines.append(
        f"  Pre-earnings    : ${c.crash.pre_close:.2f}   "
        f"Reaction: {_fmt_pct(c.crash.reaction_drop_pct)}"
    )
    lines.append(
        f"  Worst drop      : {_fmt_pct(c.crash.max_drop_pct)}  "
        f"(trough ${c.crash.trough_close:.2f})"
    )
    lines.append(
        f"  Current         : ${c.crash.current_close:.2f}   "
        f"vs pre-earnings: {_fmt_pct(c.crash.still_down_pct)}"
    )
    lines.append("")
    lines.append("Quality checks passed:")
    for p in c.passed_criteria:
        lines.append(f"  ✓ {p}")
    if c.failed_criteria:
        lines.append("Not passed / missing:")
        for f in c.failed_criteria:
            lines.append(f"  ✗ {f}")
    lines.append("")

    if c.iv_rank is not None:
        iv = c.iv_rank
        lines.append("Implied volatility:")
        lines.append(
            f"  Current ATM IV  : {iv.current_iv * 100:.1f}%   "
            f"IV rank: {iv.rank_pct:.0f}   IV percentile: {iv.iv_percentile * 100:.0f}"
        )
        posture = "rich premium — favors selling" if iv.is_elevated else "cheaper — favors buying"
        lines.append(
            f"  vs realized-vol range {iv.hv_low * 100:.1f}%–{iv.hv_high * 100:.1f}% "
            f"({iv.sample_size} obs) → {posture}"
        )
        lines.append(f"  [{iv.method}]")
        lines.append("")

    if c.priced_strategies:
        lines.append("Priced strategies (live chain — educational, not advice):")
        for s in c.priced_strategies:
            kind = "credit" if s.is_credit else "debit"
            amt = abs(s.net_premium)
            lines.append(f"  • {s.strategy}  [{s.outlook}]  net {kind} ${amt:.2f}/sh")
            for leg in s.legs:
                ivtxt = (
                    f" @ IV {leg.implied_volatility * 100:.0f}%"
                    if leg.implied_volatility is not None else ""
                )
                lines.append(
                    f"      {leg.action.upper():4} {leg.option_type} "
                    f"{leg.strike:g} exp {leg.expiry} — ${leg.price:.2f}{ivtxt}"
                )
            mp = "unlimited" if s.max_profit is None else f"${s.max_profit:.2f}"
            ml = "n/a" if s.max_loss is None else f"${s.max_loss:.2f}"
            be = "n/a" if s.breakeven is None else f"${s.breakeven:.2f}"
            lines.append(
                f"      max profit {mp} / max loss {ml} / breakeven {be} (per share)"
            )
            if s.notes:
                lines.append(f"      {s.notes}")
        lines.append("")
    else:
        lines.append("Options strategy ideas (educational — not advice):")
        for s in c.option_suggestions:
            lines.append(f"  • {s.strategy}  [{s.outlook}]")
            lines.append(f"      {s.description}")
            lines.append(f"      Strikes: {s.strike_guidance}")
            lines.append(f"      Risk:    {s.risk_note}")
        lines.append("")
    return "\n".join(lines)


def _json_default(o):
    if isinstance(o, date):
        return o.isoformat()
    return str(o)


def render_json(candidates: List[Candidate]) -> str:
    payload = [asdict(c) for c in candidates]
    return json.dumps(payload, indent=2, default=_json_default)
