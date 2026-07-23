"""Scoring a company's fundamental quality.

The score is a weighted, criterion-based rubric. Each criterion contributes its
weight when the metric is available *and* passes its threshold. Metrics that are
missing from the data provider are excluded from the denominator so a company
isn't penalized for gaps in the data feed -- instead we report `data_coverage`
so the caller knows how complete the picture is.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, List, Optional, Tuple

from ..config import ScreenConfig
from ..data.models import Fundamentals


@dataclass(frozen=True)
class _Criterion:
    key: str
    label: str
    weight: float
    getter: Callable[[Fundamentals], Optional[float]]
    passes: Callable[[float, ScreenConfig], bool]
    # Higher metric value is better? Used only for display/explanation.
    higher_is_better: bool = True


def _build_criteria() -> List[_Criterion]:
    return [
        _Criterion(
            "profit_margin", "Net profit margin", 1.5,
            lambda f: f.profit_margin,
            lambda v, c: v >= c.min_profit_margin,
        ),
        _Criterion(
            "operating_margin", "Operating margin", 1.0,
            lambda f: f.operating_margin,
            lambda v, c: v >= c.min_operating_margin,
        ),
        _Criterion(
            "return_on_equity", "Return on equity", 1.5,
            lambda f: f.return_on_equity,
            lambda v, c: v >= c.min_return_on_equity,
        ),
        _Criterion(
            "revenue_growth", "Revenue growth (yoy)", 1.0,
            lambda f: f.revenue_growth,
            lambda v, c: v >= c.min_revenue_growth,
        ),
        _Criterion(
            "free_cash_flow", "Positive free cash flow", 1.5,
            lambda f: f.free_cash_flow,
            lambda v, c: (v > 0) if c.require_positive_fcf else True,
        ),
        _Criterion(
            "debt_to_equity", "Debt / equity", 1.0,
            lambda f: f.debt_to_equity,
            lambda v, c: v <= c.max_debt_to_equity,
            higher_is_better=False,
        ),
        _Criterion(
            "current_ratio", "Current ratio", 0.75,
            lambda f: f.current_ratio,
            lambda v, c: v >= c.min_current_ratio,
        ),
        _Criterion(
            "trailing_pe", "Reasonable P/E", 0.75,
            lambda f: f.trailing_pe,
            # A negative trailing P/E means no earnings -> fail.
            lambda v, c: 0 < v <= c.max_trailing_pe,
            higher_is_better=False,
        ),
        _Criterion(
            "market_cap", "Market cap floor", 0.5,
            lambda f: f.market_cap,
            lambda v, c: v >= c.min_market_cap,
        ),
    ]


_CRITERIA = _build_criteria()


def _describe(crit: _Criterion, value: float) -> str:
    """Human-readable label with the observed value."""
    if crit.key in {"profit_margin", "operating_margin", "return_on_equity",
                    "revenue_growth"}:
        return f"{crit.label}: {value * 100:.1f}%"
    if crit.key == "market_cap":
        return f"{crit.label}: ${value / 1e9:.1f}B"
    if crit.key == "free_cash_flow":
        return f"{crit.label}: ${value / 1e9:.2f}B"
    return f"{crit.label}: {value:.2f}"


def score_fundamentals(
    f: Fundamentals, cfg: ScreenConfig
) -> Tuple[float, float, List[str], List[str]]:
    """Return ``(score, coverage, passed, failed)``.

    * ``score``    -- 0-100 weighted quality score over the *available* metrics.
    * ``coverage`` -- 0-1 fraction of criteria for which data was present.
    * ``passed`` / ``failed`` -- human-readable criterion descriptions.
    """
    earned = 0.0
    available_weight = 0.0
    total_weight = sum(c.weight for c in _CRITERIA)
    passed: List[str] = []
    failed: List[str] = []

    for crit in _CRITERIA:
        value = crit.getter(f)
        if value is None:
            failed.append(f"{crit.label}: n/a")
            continue
        available_weight += crit.weight
        if crit.passes(value, cfg):
            earned += crit.weight
            passed.append(_describe(crit, value))
        else:
            failed.append(_describe(crit, value))

    score = (earned / available_weight * 100.0) if available_weight else 0.0
    coverage = available_weight / total_weight if total_weight else 0.0
    return round(score, 1), round(coverage, 2), passed, failed
