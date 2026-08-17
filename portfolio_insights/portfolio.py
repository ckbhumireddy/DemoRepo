"""Portfolio source: a JSON document the user maintains (pure loaders).

The holdings do not live at a broker this service can query, so the user
supplies them: locally as a ``portfolio.json`` file (gitignored), and in CI
as the ``PORTFOLIO_JSON`` secret carrying the same document — holdings
never enter the repository.

Document format::

    {
      "cash": 1200.50,                      // optional
      "positions": [
        {"ticker": "TGT",  "quantity": 100, "cost_basis": 95.50},
        {"ticker": "SNDK", "quantity": 20,  "cost_basis": 41.00}
      ]
    }

``cost_basis`` is per share and optional (P&L-vs-cost insights are skipped
without it). Market values are computed live from quotes, never stored.

Privacy contract: nothing in this package logs tickers, quantities, or
dollar amounts — counts only. Holdings appear only in the emails.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from typing import List, Optional

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Position:
    ticker: str
    quantity: float
    cost_basis: Optional[float] = None   # per share


@dataclass
class PortfolioSnapshot:
    positions: List[Position] = field(default_factory=list)
    cash: Optional[float] = None
    source: str = ""                      # "env" | "file:<path>"
    fetch_error: Optional[str] = None


def _as_float(value) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return None if f != f else f


def parse_portfolio(document, source: str = "") -> PortfolioSnapshot:
    """Parse the portfolio document. Malformed rows are skipped, duplicate
    tickers are merged (quantities added, cost basis weighted)."""
    snapshot = PortfolioSnapshot(source=source)
    if not isinstance(document, dict):
        snapshot.fetch_error = "portfolio document must be a JSON object"
        return snapshot
    snapshot.cash = _as_float(document.get("cash"))

    merged = {}
    for row in document.get("positions") or []:
        if not isinstance(row, dict):
            continue
        ticker = str(row.get("ticker") or "").strip().upper()
        qty = _as_float(row.get("quantity"))
        if not ticker or not qty:
            continue
        cost = _as_float(row.get("cost_basis"))
        slot = merged.setdefault(ticker, {"qty": 0.0, "cost": 0.0, "known": True})
        slot["qty"] += qty
        if cost is None:
            slot["known"] = False
        else:
            slot["cost"] += cost * qty

    for ticker, slot in merged.items():
        if slot["qty"] == 0:
            continue
        cost_basis = (
            round(slot["cost"] / slot["qty"], 4) if slot["known"] else None
        )
        snapshot.positions.append(
            Position(ticker=ticker, quantity=slot["qty"], cost_basis=cost_basis)
        )
    if not snapshot.positions and snapshot.fetch_error is None:
        # An empty portfolio is legal; the emails say so explicitly.
        pass
    snapshot.positions.sort(key=lambda p: p.ticker)
    return snapshot


def load_portfolio(portfolio_json: str, portfolio_file: str) -> PortfolioSnapshot:
    """Load the portfolio: inline JSON (the CI secret) wins over the file."""
    raw, source = "", ""
    if portfolio_json.strip():
        raw, source = portfolio_json, "env"
    elif portfolio_file and os.path.exists(portfolio_file):
        try:
            with open(portfolio_file, "r", encoding="utf-8") as fh:
                raw = fh.read()
            source = f"file:{portfolio_file}"
        except OSError as exc:
            return PortfolioSnapshot(
                fetch_error=f"could not read {portfolio_file}: {exc}"
            )
    if not raw:
        return PortfolioSnapshot(
            fetch_error=(
                "no portfolio source: set the PORTFOLIO_JSON secret or "
                f"provide {portfolio_file or 'a portfolio file'}"
            )
        )
    try:
        document = json.loads(raw)
    except json.JSONDecodeError as exc:
        return PortfolioSnapshot(fetch_error=f"portfolio JSON is invalid: {exc}")
    snapshot = parse_portfolio(document, source)
    logger.info("Loaded %d position(s) from %s",
                len(snapshot.positions), source or "nothing")
    return snapshot
