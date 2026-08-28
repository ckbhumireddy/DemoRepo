"""The approval gate: backtest results on disk, and the yes/no they imply.

This file is the whole enforcement mechanism for "backtested before
practice". The scan asks :func:`is_approved` before emailing a strategy's
signals; approval exists only as a recorded backtest that cleared the
thresholds. There is no override flag — the way to approve a strategy is to
run a backtest good enough to pass, and the way to revoke one is to record
a backtest that fails.

Approvals also expire (default 90 days): a market regime shifts, and an
approval earned against last year's tape should not authorize trades
forever without being re-earned.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import json
import logging
import os
from dataclasses import dataclass
from typing import Dict, Optional

from .backtest import BacktestResult

logger = logging.getLogger(__name__)

DEFAULT_REGISTRY_FILE = "state/swing_registry.json"
DEFAULT_APPROVAL_TTL_DAYS = 90


@dataclass
class Thresholds:
    """What a backtest must show before its strategy may go live."""

    min_trades: int = 20          # below this the stats are anecdotes
    min_profit_factor: float = 1.3
    min_win_rate: float = 0.0     # optional extra bar; PF is the main gate
    max_drawdown: float = 0.35    # an equity curve this jagged fails on risk

    def verdict(self, result: BacktestResult) -> tuple:
        """(approved, reasons) — every failing criterion is named."""
        reasons = []
        if result.trades < self.min_trades:
            reasons.append(
                f"only {result.trades} trade(s); need {self.min_trades} "
                "for the stats to mean anything"
            )
        if result.profit_factor < self.min_profit_factor:
            reasons.append(
                f"profit factor {result.profit_factor:.2f} below "
                f"{self.min_profit_factor:.2f}"
            )
        if result.win_rate < self.min_win_rate:
            reasons.append(
                f"win rate {result.win_rate:.0%} below {self.min_win_rate:.0%}"
            )
        if result.max_drawdown > self.max_drawdown:
            reasons.append(
                f"max drawdown {result.max_drawdown:.0%} exceeds "
                f"{self.max_drawdown:.0%}"
            )
        return (not reasons, reasons)


@dataclass
class Record:
    """One strategy's standing: its last backtest and the verdict on it."""

    strategy: str
    approved: bool
    tested_at: str                # ISO date
    trades: int
    win_rate: float
    profit_factor: float
    expectancy: float
    max_drawdown: float
    avg_hold: float
    tickers: int
    span: str = ""                # "first_day..last_day" of the tested tape
    reasons: list = dataclasses.field(default_factory=list)


class Registry:
    """JSON-backed store of strategy standings."""

    def __init__(self, path: str = DEFAULT_REGISTRY_FILE,
                 approval_ttl_days: int = DEFAULT_APPROVAL_TTL_DAYS) -> None:
        self.path = path
        self.approval_ttl_days = approval_ttl_days
        self._records: Dict[str, dict] = self._load()

    def _load(self) -> Dict[str, dict]:
        if not self.path or not os.path.exists(self.path):
            return {}
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            return data if isinstance(data, dict) else {}
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("could not read registry %s (%s)", self.path, exc)
            return {}

    def _save(self) -> None:
        if not self.path:
            return
        directory = os.path.dirname(self.path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as fh:
            json.dump(self._records, fh, indent=2, sort_keys=True)

    def record(
        self,
        result: BacktestResult,
        thresholds: Thresholds,
        today: Optional[dt.date] = None,
    ) -> Record:
        """Score a backtest against the gate and persist the verdict."""
        approved, reasons = thresholds.verdict(result)
        span = ""
        if result.first_day and result.last_day:
            span = f"{result.first_day.isoformat()}..{result.last_day.isoformat()}"
        rec = Record(
            strategy=result.strategy,
            approved=approved,
            tested_at=(today or dt.date.today()).isoformat(),
            trades=result.trades,
            win_rate=round(result.win_rate, 4),
            profit_factor=(
                round(result.profit_factor, 4)
                if result.profit_factor != float("inf") else 999.0
            ),
            expectancy=round(result.expectancy, 5),
            max_drawdown=round(result.max_drawdown, 4),
            avg_hold=round(result.avg_hold, 2),
            tickers=result.tickers,
            span=span,
            reasons=reasons,
        )
        self._records[result.strategy] = dataclasses.asdict(rec)
        self._save()
        logger.info(
            "Recorded backtest for %s: %s",
            result.strategy,
            "APPROVED" if approved else f"rejected ({'; '.join(reasons)})",
        )
        return rec

    def get(self, strategy: str) -> Optional[dict]:
        return self._records.get(strategy)

    def is_approved(self, strategy: str, today: Optional[dt.date] = None) -> bool:
        """Approved, and recently enough that the approval still stands."""
        rec = self._records.get(strategy)
        if not rec or not rec.get("approved"):
            return False
        try:
            tested = dt.date.fromisoformat(rec["tested_at"])
        except (KeyError, ValueError):
            return False
        age = ((today or dt.date.today()) - tested).days
        if age > self.approval_ttl_days:
            logger.info(
                "Approval for %s is %d day(s) old (limit %d) — re-run the "
                "backtest to renew it", strategy, age, self.approval_ttl_days,
            )
            return False
        return True

    def why_not(self, strategy: str, today: Optional[dt.date] = None) -> str:
        """A human sentence for 'why is this strategy not live?'."""
        rec = self._records.get(strategy)
        if not rec:
            return "never backtested — run: python -m swing_trader backtest"
        if not rec.get("approved"):
            reasons = rec.get("reasons") or ["backtest did not pass"]
            return "backtest rejected: " + "; ".join(reasons)
        if not self.is_approved(strategy, today):
            return (
                f"approval from {rec.get('tested_at')} has expired "
                f"(limit {self.approval_ttl_days} days) — re-run the backtest"
            )
        return "approved"

    def all_records(self) -> Dict[str, dict]:
        return dict(self._records)
