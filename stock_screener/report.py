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
        f"{'CRASH':>8}{'NOW vs PRE':>12}{'DAYS':>6}  NAME"
    )
    lines = [header, "-" * len(header)]
    for i, c in enumerate(candidates, 1):
        lines.append(
            f"{i:>2}  {c.ticker:<7}{c.composite_score:>6.1f}"
            f"{c.fundamental_score:>8.1f}"
            f"{_fmt_pct(c.crash.max_drop_pct):>8}"
            f"{_fmt_pct(c.crash.still_down_pct):>12}"
            f"{c.crash.days_since_earnings:>6}  {c.name[:32]}"
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
