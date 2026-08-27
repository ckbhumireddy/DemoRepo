"""The researcher loop: grid-tune a strategy, promote a champion honestly.

Tuning a backtest is how strategies get quietly ruined — every parameter
you fit to history is a parameter fitted to noise. So the loop is built
around one discipline, walk-forward validation:

1. Split every ticker's history at ``train_fraction`` (default 70%). The
   validation slice keeps a warmup overlap so the strategy has the history
   it needs, but **no trade may begin before the split date** — entries in
   the overlap are discarded, so a training-period trade can never leak
   into the validation score.
2. Run the whole parameter grid on the TRAIN segment only, ranking by
   profit factor (expectancy as tiebreak) among configs with enough trades.
3. Take the top ``finalists`` configs — and only those — to the VALIDATE
   segment they have never seen.
4. The champion is the best validator, and it is only *promoted* (written
   to ``swing_trader/champions.json``) if its validation result also clears
   the same approval thresholds a live strategy must pass. A grid whose
   winner falls apart out-of-sample produces a report, not a champion.

``SwingConfig.build_strategy`` reads the champions file, so a promoted
config takes effect everywhere — scan, backtest, future research — with
explicit environment overrides still winning over the champion.

Precedent: ``martingale_research`` promotes into the live trader's
``champions.json`` only after configs survive a stress gate; this is the
same contract with validation as the stress.
"""

from __future__ import annotations

import datetime as dt
import itertools
import json
import logging
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .backtest import BacktestResult, Trade, backtest_ticker, score
from .registry import Thresholds
from .strategies import get_strategy

logger = logging.getLogger(__name__)

CHAMPIONS_FILE = os.path.join(os.path.dirname(__file__), "champions.json")

# The default grid for strategy 1. Deliberately small: 4 knobs the strategy
# is actually sensitive to, coarse steps. A finer grid mostly finds noise.
DEFAULT_GRID: Dict[str, List] = {
    "entry_band": [0.015, 0.02, 0.03],
    "stop_buffer": [0.02, 0.03, 0.05],
    "min_rr": [1.5, 2.0, 2.5],
    "min_touches": [2, 3],
}

DEFAULT_TRAIN_FRACTION = 0.7
DEFAULT_FINALISTS = 5
MIN_TRAIN_TRADES = 8      # fewer and the training rank is a coin flip


@dataclass
class TrialResult:
    """One grid point's scores on both segments."""

    params: Dict
    train: BacktestResult
    validation: Optional[BacktestResult] = None

    @property
    def train_key(self) -> tuple:
        pf = min(self.train.profit_factor, 999.0)
        return (pf, self.train.expectancy)


@dataclass
class ResearchReport:
    """Everything the loop learned, champion or not."""

    strategy: str
    grid_size: int
    trials: List[TrialResult] = field(default_factory=list)
    finalists: List[TrialResult] = field(default_factory=list)
    champion: Optional[TrialResult] = None
    promoted: bool = False
    reasons: List[str] = field(default_factory=list)
    split_day: Optional[dt.date] = None


def _apply_params(strategy, params: Dict) -> None:
    for key, value in params.items():
        if not hasattr(strategy, key):
            raise KeyError(f"strategy {strategy.name!r} has no parameter {key!r}")
        setattr(strategy, key, value)


def _segment_trades(
    strategy, history: Dict, *, start: Optional[dt.date], end: Optional[dt.date],
    max_hold: int,
) -> List[Trade]:
    """All trades whose ENTRY falls inside [start, end)."""
    trades: List[Trade] = []
    for ticker, bars in sorted(history.items()):
        for trade in backtest_ticker(strategy, ticker, bars, max_hold):
            if start is not None and trade.entry_day < start:
                continue
            if end is not None and trade.entry_day >= end:
                continue
            trades.append(trade)
    return trades


def split_day_for(history: Dict, train_fraction: float) -> Optional[dt.date]:
    """The calendar date splitting train from validation, across all tickers."""
    days = sorted({b.day for bars in history.values() for b in bars})
    if len(days) < 10:
        return None
    return days[int(len(days) * train_fraction)]


def run_research(
    strategy_name: str,
    history: Dict,
    thresholds: Thresholds,
    *,
    grid: Optional[Dict[str, List]] = None,
    train_fraction: float = DEFAULT_TRAIN_FRACTION,
    finalists: int = DEFAULT_FINALISTS,
    max_hold: int = 20,
) -> ResearchReport:
    """Grid-search on train, judge finalists on validation, crown honestly."""
    if grid is None:
        probe = get_strategy(strategy_name)
        grid = getattr(probe, "research_grid", lambda: None)() or DEFAULT_GRID
    combos = [
        dict(zip(grid.keys(), values))
        for values in itertools.product(*grid.values())
    ]
    report = ResearchReport(strategy=strategy_name, grid_size=len(combos))

    split = split_day_for(history, train_fraction)
    if split is None:
        report.reasons.append("not enough history to split train/validation")
        return report
    report.split_day = split

    # --- Stage 1: the whole grid, train segment only. ---
    for params in combos:
        strategy = get_strategy(strategy_name)
        _apply_params(strategy, params)
        train = score(strategy_name,
                      _segment_trades(strategy, history, start=None, end=split,
                                      max_hold=max_hold))
        report.trials.append(TrialResult(params=params, train=train))
    logger.info("Researched %d config(s) on the train segment (< %s)",
                len(report.trials), split)

    eligible = [t for t in report.trials if t.train.trades >= MIN_TRAIN_TRADES]
    if not eligible:
        report.reasons.append(
            f"no config produced {MIN_TRAIN_TRADES}+ training trades — the "
            "screen may be too strict for this universe"
        )
        return report

    # --- Stage 2: only the finalists ever see the validation segment. ---
    report.finalists = sorted(eligible, key=lambda t: t.train_key,
                              reverse=True)[:finalists]
    for trial in report.finalists:
        strategy = get_strategy(strategy_name)
        _apply_params(strategy, trial.params)
        trial.validation = score(
            strategy_name,
            _segment_trades(strategy, history, start=split, end=None,
                            max_hold=max_hold),
        )

    def _validation_key(trial: TrialResult) -> tuple:
        v = trial.validation
        return (min(v.profit_factor, 999.0), v.expectancy) if v else (0.0, 0.0)

    report.champion = max(report.finalists, key=_validation_key)

    # --- Stage 3: the champion must survive the live gate out-of-sample. ---
    approved, reasons = thresholds.verdict(report.champion.validation)
    if approved:
        report.promoted = True
    else:
        report.reasons = [f"validation: {r}" for r in reasons]
    return report


def promote(
    report: ResearchReport,
    *,
    champions_file: Optional[str] = None,
    today: Optional[dt.date] = None,
) -> dict:
    """Write the champion's params where build_strategy will find them.

    Refuses a report whose champion did not pass validation — the file only
    ever holds configs that survived out-of-sample.
    """
    if not report.promoted or report.champion is None:
        raise ValueError(
            "only a validation-passing champion can be promoted: "
            + ("; ".join(report.reasons) or "no champion")
        )
    champion = report.champion
    v = champion.validation
    entry = {
        "params": champion.params,
        "promoted": (today or dt.date.today()).isoformat(),
        "split_day": report.split_day.isoformat() if report.split_day else "",
        "validation": {
            "trades": v.trades,
            "win_rate": round(v.win_rate, 4),
            "profit_factor": round(min(v.profit_factor, 999.0), 4),
            "expectancy": round(v.expectancy, 5),
            "max_drawdown": round(v.max_drawdown, 4),
        },
        "grid_size": report.grid_size,
    }
    champions_file = CHAMPIONS_FILE if champions_file is None else champions_file
    champions = load_champions(champions_file)
    champions[report.strategy] = entry
    with open(champions_file, "w", encoding="utf-8") as fh:
        json.dump(champions, fh, indent=2, sort_keys=True)
    logger.info("Promoted %s champion to %s: %s",
                report.strategy, champions_file, champion.params)
    return entry


def load_champions(champions_file: Optional[str] = None) -> dict:
    champions_file = CHAMPIONS_FILE if champions_file is None else champions_file
    if not champions_file or not os.path.exists(champions_file):
        return {}
    try:
        with open(champions_file, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("could not read champions file %s (%s)",
                       champions_file, exc)
        return {}


def champion_params(strategy_name: str,
                    champions_file: Optional[str] = None) -> Dict:
    return dict(load_champions(champions_file).get(strategy_name, {})
                .get("params", {}))


def format_report(report: ResearchReport) -> str:
    """The research summary a human reads before trusting the promotion."""
    lines = [
        f"Research: {report.strategy} — {report.grid_size} config(s), "
        f"split at {report.split_day}",
        "",
        f"  {'PARAMS':<52} {'TRAIN PF':>8} {'N':>4}   {'VAL PF':>7} {'N':>4} "
        f"{'VAL EXP':>8}",
    ]
    for trial in report.finalists:
        params = ", ".join(f"{k}={v}" for k, v in sorted(trial.params.items()))
        v = trial.validation
        marker = " <- champion" if trial is report.champion else ""
        lines.append(
            f"  {params:<52} {min(trial.train.profit_factor, 999):>8.2f} "
            f"{trial.train.trades:>4}   "
            + (f"{min(v.profit_factor, 999):>7.2f} {v.trades:>4} "
               f"{v.expectancy:>+7.2%}" if v else f"{'—':>7} {'—':>4} {'—':>8}")
            + marker
        )
    lines.append("")
    if report.promoted:
        lines.append("Champion PASSED validation — promoted to champions.json.")
    else:
        lines.append("NOT promoted:")
        lines += [f"  - {r}" for r in report.reasons]
    return "\n".join(lines)
