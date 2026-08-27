"""Race every strategy on the same tape (pure functions).

"Which strategy would work better on this ticker?" is only answerable when
every contender sees identical history and identical fill rules, with
buy-and-hold standing beside them as the null hypothesis — a swing strategy
that underperforms just owning the stock is machinery for nothing.

Comparability assumption, stated rather than hidden: one position at a
time, full allocation per trade, so a strategy's compounded return is the
product of its trade returns. Time in market differs wildly between
strategies (that is the point of swinging), so exposure is reported too.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .backtest import BacktestResult, run_backtest
from .registry import Thresholds
from .strategies import get_strategy, strategy_names


@dataclass
class StrategyRow:
    """One strategy's line in the race."""

    name: str
    result: BacktestResult
    compounded: float          # product of (1+r) - 1 across its trades
    exposure_days: int         # bars spent holding
    would_approve: bool
    reasons: List[str] = field(default_factory=list)


@dataclass
class Comparison:
    rows: List[StrategyRow] = field(default_factory=list)
    buy_hold: Optional[float] = None
    span: str = ""
    tickers: int = 0


def _compounded(result: BacktestResult) -> float:
    equity = 1.0
    for trade in sorted(result.trade_log, key=lambda t: t.exit_day):
        equity *= 1.0 + trade.pct
    return equity - 1.0


def buy_and_hold(history: Dict) -> Optional[float]:
    """Equal-weight buy-and-hold across the same tickers and span."""
    returns = []
    for bars in history.values():
        ordered = sorted(bars, key=lambda b: b.day)
        if len(ordered) >= 2 and ordered[0].close > 0:
            returns.append(ordered[-1].close / ordered[0].close - 1.0)
    return sum(returns) / len(returns) if returns else None


def compare_strategies(
    history: Dict,
    thresholds: Thresholds,
    *,
    names: Optional[List[str]] = None,
    max_hold: int = 20,
) -> Comparison:
    """Backtest every strategy over the same history and line them up."""
    comparison = Comparison(buy_hold=buy_and_hold(history),
                            tickers=len(history))
    days = sorted({b.day for bars in history.values() for b in bars})
    if days:
        comparison.span = f"{days[0].isoformat()}..{days[-1].isoformat()}"
    for name in (names or strategy_names()):
        strategy = get_strategy(name)
        result = run_backtest(strategy, history, max_hold=max_hold)
        approved, reasons = thresholds.verdict(result)
        comparison.rows.append(
            StrategyRow(
                name=name,
                result=result,
                compounded=_compounded(result),
                exposure_days=sum(t.held for t in result.trade_log),
                would_approve=approved,
                reasons=reasons,
            )
        )
    comparison.rows.sort(key=lambda r: -r.compounded)
    return comparison


def format_comparison(comparison: Comparison) -> str:
    lines = [
        f"Strategy race — {comparison.tickers} ticker(s), {comparison.span}",
        "",
        f"  {'STRATEGY':<16} {'TRADES':>6} {'WIN':>5} {'PF':>6} {'EXP':>8} "
        f"{'MAXDD':>6} {'RETURN':>8} {'DAYS IN':>8}  GATE",
    ]
    for row in comparison.rows:
        r = row.result
        gate = "would pass" if row.would_approve else "would FAIL"
        lines.append(
            f"  {row.name:<16} {r.trades:>6} "
            f"{r.win_rate * 100:>4.0f}% {min(r.profit_factor, 999):>6.2f} "
            f"{r.expectancy:>+7.2%} {r.max_drawdown * 100:>5.1f}% "
            f"{row.compounded:>+7.1%} {row.exposure_days:>8}  {gate}"
        )
        for reason in row.reasons:
            lines.append(f"  {'':<16}   - {reason}")
    if comparison.buy_hold is not None:
        lines += [
            "",
            f"  {'buy-and-hold':<16} {'—':>6} {'—':>5} {'—':>6} {'—':>8} "
            f"{'—':>6} {comparison.buy_hold:>+7.1%} {'all':>8}  (benchmark)",
        ]
    lines += [
        "",
        "Return = compounded product of trade returns, one full-size position "
        "at a time. 'Days in' is bars spent holding — a strategy matching "
        "buy-and-hold while exposed a fraction of the time is the better "
        "risk-adjusted result. The gate column applies the same thresholds "
        "a live approval requires.",
    ]
    return "\n".join(lines)
