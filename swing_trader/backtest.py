"""Walk-forward backtest: replay history through a strategy, score it.

The engine's one promise is **no lookahead**: at every step the strategy
sees only ``bars[:t+1]`` — exactly what a live scan would have seen at that
close — and fills are simulated against bars the strategy has not seen.
Conservative fill rules, because a backtest should understate:

- Entry at the signal bar's close (the scan runs post-close, so the real
  fill would be the next open; the difference is noise either way).
- On later bars the STOP is checked before the target when both are inside
  one bar's range — the pessimistic reading of an ambiguous bar.
- Gaps fill at the open, not at the stop/target price: a stock that gaps
  below the stop cost you the gap, and pretending otherwise is how
  backtests lie.
- ``max_hold`` bars without resolution exits at the close: swing trades
  that stop swinging are dead capital.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass, field
from typing import List, Optional, Sequence

from earnings_analyzer.models import PriceBar

from .strategies.base import Signal, Strategy

logger = logging.getLogger(__name__)

DEFAULT_MAX_HOLD = 20      # trading days before a stale trade is cut


@dataclass
class Trade:
    """One simulated round trip."""

    ticker: str
    entry_day: dt.date
    entry: float
    exit_day: dt.date
    exit: float
    stop: float
    target: float
    outcome: str             # "target" | "stop" | "time" | "open"
    held: int                # bars from entry to exit

    @property
    def pct(self) -> float:
        return (self.exit - self.entry) / self.entry if self.entry > 0 else 0.0


@dataclass
class BacktestResult:
    """The scoreboard a strategy must beat to earn live signals."""

    strategy: str
    trades: int = 0
    wins: int = 0
    win_rate: float = 0.0
    avg_win: float = 0.0          # mean winning return (decimal)
    avg_loss: float = 0.0         # mean losing return (negative decimal)
    expectancy: float = 0.0       # mean return per trade (decimal)
    profit_factor: float = 0.0    # gross gains / gross losses
    max_drawdown: float = 0.0     # worst peak-to-trough of the equity curve
    avg_hold: float = 0.0
    tickers: int = 0
    first_day: Optional[dt.date] = None
    last_day: Optional[dt.date] = None
    trade_log: List[Trade] = field(default_factory=list)


def _resolve(
    ticker: str, signal: Signal, future: Sequence[PriceBar], max_hold: int
) -> Optional[Trade]:
    """Simulate one open position against bars the strategy never saw."""
    held = 0
    for bar in future:
        held += 1
        # Gap through the stop: the fill is the open, not the stop.
        if bar.open <= signal.stop:
            exit_price = bar.open
            outcome = "stop"
        elif bar.low <= signal.stop:
            exit_price = signal.stop
            outcome = "stop"
        elif bar.open >= signal.target:
            exit_price = bar.open
            outcome = "target"
        elif bar.high >= signal.target:
            exit_price = signal.target
            outcome = "target"
        elif held >= max_hold:
            exit_price = bar.close
            outcome = "time"
        else:
            continue
        return Trade(
            ticker=ticker,
            entry_day=signal.day,
            entry=signal.entry,
            exit_day=bar.day,
            exit=exit_price,
            stop=signal.stop,
            target=signal.target,
            outcome=outcome,
            held=held,
        )
    if not future:
        return None
    # History ended while the trade was open — score it at the last close so
    # a losing open position cannot hide by never resolving.
    last = future[-1]
    return Trade(
        ticker=ticker,
        entry_day=signal.day,
        entry=signal.entry,
        exit_day=last.day,
        exit=last.close,
        stop=signal.stop,
        target=signal.target,
        outcome="open",
        held=held,
    )


def backtest_ticker(
    strategy: Strategy,
    ticker: str,
    bars: Sequence[PriceBar],
    max_hold: int = DEFAULT_MAX_HOLD,
) -> List[Trade]:
    """Replay one ticker's history; one position at a time, no pyramiding."""
    ordered = sorted(bars, key=lambda b: b.day)
    trades: List[Trade] = []
    t = strategy.min_history()
    n = len(ordered)
    while t < n:
        signal = strategy.evaluate(ticker, ordered[:t + 1])
        if signal is None:
            t += 1
            continue
        trade = _resolve(ticker, signal, ordered[t + 1:], max_hold)
        if trade is None:
            break                       # signal on the very last bar
        trades.append(trade)
        # Resume the day after the exit — the position blocked re-entry.
        while t < n and ordered[t].day <= trade.exit_day:
            t += 1
    return trades


def score(strategy_name: str, trades: Sequence[Trade]) -> BacktestResult:
    """Fold a trade list into the metrics the approval gate reads."""
    result = BacktestResult(strategy=strategy_name, trade_log=list(trades))
    if not trades:
        return result
    returns = [t.pct for t in trades]
    wins = [r for r in returns if r > 0]
    losses = [r for r in returns if r <= 0]
    gross_gain = sum(wins)
    gross_loss = -sum(losses)

    # Equity curve: compound in trade order (by exit day) to expose streaks.
    equity = 1.0
    peak = 1.0
    max_dd = 0.0
    for trade in sorted(trades, key=lambda t: t.exit_day):
        equity *= 1.0 + trade.pct
        peak = max(peak, equity)
        if peak > 0:
            max_dd = max(max_dd, 1.0 - equity / peak)

    result.trades = len(trades)
    result.wins = len(wins)
    result.win_rate = len(wins) / len(trades)
    result.avg_win = gross_gain / len(wins) if wins else 0.0
    result.avg_loss = -gross_loss / len(losses) if losses else 0.0
    result.expectancy = sum(returns) / len(returns)
    result.profit_factor = (
        gross_gain / gross_loss if gross_loss > 0 else float("inf")
    )
    result.max_drawdown = max_dd
    result.avg_hold = sum(t.held for t in trades) / len(trades)
    result.tickers = len({t.ticker for t in trades})
    result.first_day = min(t.entry_day for t in trades)
    result.last_day = max(t.exit_day for t in trades)
    return result


def run_backtest(
    strategy: Strategy,
    history: dict,
    max_hold: int = DEFAULT_MAX_HOLD,
) -> BacktestResult:
    """Backtest a strategy over ``{ticker: bars}`` and score the whole book."""
    all_trades: List[Trade] = []
    for ticker, bars in sorted(history.items()):
        try:
            all_trades.extend(backtest_ticker(strategy, ticker, bars, max_hold))
        except Exception as exc:  # noqa: BLE001 - one bad ticker never kills a run
            logger.warning("backtest failed for %s (%s)", ticker, exc)
    logger.info(
        "Backtested %s over %d ticker(s): %d trade(s)",
        strategy.name, len(history), len(all_trades),
    )
    return score(strategy.name, all_trades)
