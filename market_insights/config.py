"""Market-insights configuration: the notifier's Config plus scan knobs."""

from __future__ import annotations

import dataclasses
import os
from dataclasses import dataclass

from earnings_notifier.config import Config, ConfigError, _get_int


def _get_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be a number, got {raw!r}") from exc


@dataclass
class MarketInsightsConfig(Config):
    """Runtime configuration for the unusual-volume scan."""

    insights_top_n: int = 25
    insights_universe: str = "sp500"      # "sp500" | "portfolio"
    insights_max_workers: int = 4
    insights_lookback: int = 20           # sessions in the volume baseline

    # Screening thresholds.
    insights_min_rvol: float = 2.0            # projected volume vs its median
    insights_min_price: float = 5.0           # skip sub-$5 names
    insights_min_dollar_volume: float = 25e6  # skip thin tape
    # Stage-1 screen against the prior session (loose on purpose — see
    # scanner.prefilter).
    insights_prefilter_multiple: float = 1.5
    # Ceiling on stage-2 history requests, so a market-wide volume day
    # cannot stretch the run past its rate budget.
    insights_max_candidates: int = 150

    # Once-per-day send marker, riding the shared state/ cache; empty
    # disables the marker (every run sends).
    insights_state_file: str = "state/market_insights.json"

    # Portfolio (for holding highlights); same sources as portfolio_insights.
    portfolio_json: str = ""
    portfolio_file: str = "portfolio.json"

    # TradeStation market data — required for a full S&P 500 sweep; without
    # it the scan degrades to the portfolio and watchlist via Yahoo.
    tradestation_client_id: str = ""
    tradestation_client_secret: str = ""
    tradestation_token: str = ""
    tradestation_token_file: str = ""
    tradestation_environment: str = "live"    # "live" | "sim"

    @classmethod
    def from_env(cls) -> "MarketInsightsConfig":
        base = dataclasses.asdict(Config.from_env())
        return cls(
            **base,
            insights_top_n=_get_int("INSIGHTS_TOP_N", 25),
            insights_universe=os.environ.get(
                "INSIGHTS_UNIVERSE", "sp500"
            ).strip().lower(),
            insights_max_workers=_get_int("INSIGHTS_MAX_WORKERS", 4),
            insights_lookback=_get_int("INSIGHTS_LOOKBACK", 20),
            insights_min_rvol=_get_float("INSIGHTS_MIN_RVOL", 2.0),
            insights_min_price=_get_float("INSIGHTS_MIN_PRICE", 5.0),
            insights_min_dollar_volume=_get_float(
                "INSIGHTS_MIN_DOLLAR_VOLUME", 25e6
            ),
            insights_prefilter_multiple=_get_float(
                "INSIGHTS_PREFILTER_MULTIPLE", 1.5
            ),
            insights_max_candidates=_get_int("INSIGHTS_MAX_CANDIDATES", 150),
            insights_state_file=os.environ.get(
                "INSIGHTS_STATE_FILE", "state/market_insights.json"
            ).strip(),
            portfolio_json=os.environ.get("PORTFOLIO_JSON", "").strip(),
            portfolio_file=os.environ.get("PORTFOLIO_FILE", "portfolio.json").strip(),
            tradestation_client_id=os.environ.get(
                "TRADESTATION_CLIENT_ID", ""
            ).strip(),
            tradestation_client_secret=os.environ.get(
                "TRADESTATION_CLIENT_SECRET", ""
            ).strip(),
            tradestation_token=os.environ.get("TRADESTATION_TOKEN", "").strip(),
            tradestation_token_file=os.environ.get(
                "TRADESTATION_TOKEN_FILE", ""
            ).strip(),
            tradestation_environment=os.environ.get(
                "TRADESTATION_ENVIRONMENT", "live"
            ).strip().lower(),
        )

    def validate(self) -> None:
        super().validate()
        if self.insights_lookback < 5:
            raise ConfigError("INSIGHTS_LOOKBACK must be >= 5")
        if self.insights_min_rvol <= 0:
            raise ConfigError("INSIGHTS_MIN_RVOL must be > 0")
        if self.insights_top_n < 1:
            raise ConfigError("INSIGHTS_TOP_N must be >= 1")
