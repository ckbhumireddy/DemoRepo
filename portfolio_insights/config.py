"""Insights configuration: the notifier's Config plus monitoring knobs."""

from __future__ import annotations

import dataclasses
import os
from dataclasses import dataclass

from earnings_notifier.config import Config, ConfigError, _get_bool, _get_int


def _get_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be a number, got {raw!r}") from exc


@dataclass
class InsightsConfig(Config):
    """Runtime configuration for the portfolio insights service."""

    # State for send-dedupe markers. Lives under state/ so the workflow
    # cache persists it. Empty disables dedupe (every run sends).
    insights_state_file: str = "state/insights.json"

    # Midday alert thresholds. A percentage OR a dollar amount triggers:
    # a huge position can lose real money on a small percent move.
    insights_move_alert_pct: float = 5.0            # per position, day %
    insights_move_alert_dollars: float = 5000.0     # per position, day P&L
    insights_portfolio_alert_pct: float = 2.0       # whole portfolio, day %
    insights_portfolio_alert_dollars: float = 15000.0  # portfolio, day P&L

    # EOD watch-item thresholds.
    insights_max_weight_pct: float = 20.0   # concentration warning
    insights_vol_spike_mult: float = 2.0    # today vs 30-day typical move
    insights_event_days: int = 7            # earnings look-ahead

    # Portfolio source: inline JSON (the PORTFOLIO_JSON secret in CI) wins
    # over the local file. Holdings never live in the repository.
    portfolio_json: str = ""
    portfolio_file: str = "portfolio.json"

    # Options reporting is OFF by default: marks for far-dated, thinly
    # traded contracts proved unreliable, and wrong numbers are worse than
    # none. The emails note the exclusion; flip INSIGHTS_TRACK_OPTIONS to
    # re-enable.
    insights_track_options: bool = False

    # Charles Schwab credentials — market data only (quotes + history),
    # with automatic Yahoo fallback when unset or lapsed.
    schwab_app_key: str = ""
    schwab_app_secret: str = ""
    schwab_token: str = ""
    schwab_token_file: str = ""

    @classmethod
    def from_env(cls) -> "InsightsConfig":
        base = dataclasses.asdict(Config.from_env())
        return cls(
            **base,
            insights_state_file=os.environ.get(
                "INSIGHTS_STATE_FILE", "state/insights.json"
            ).strip(),
            insights_move_alert_pct=_get_float("INSIGHTS_MOVE_ALERT_PCT", 5.0),
            insights_move_alert_dollars=_get_float(
                "INSIGHTS_MOVE_ALERT_DOLLARS", 5000.0
            ),
            insights_portfolio_alert_pct=_get_float(
                "INSIGHTS_PORTFOLIO_ALERT_PCT", 2.0
            ),
            insights_portfolio_alert_dollars=_get_float(
                "INSIGHTS_PORTFOLIO_ALERT_DOLLARS", 15000.0
            ),
            insights_max_weight_pct=_get_float("INSIGHTS_MAX_WEIGHT_PCT", 20.0),
            insights_vol_spike_mult=_get_float("INSIGHTS_VOL_SPIKE_MULT", 2.0),
            insights_event_days=_get_int("INSIGHTS_EVENT_DAYS", 7),
            portfolio_json=os.environ.get("PORTFOLIO_JSON", "").strip(),
            portfolio_file=os.environ.get(
                "PORTFOLIO_FILE", "portfolio.json"
            ).strip(),
            insights_track_options=_get_bool("INSIGHTS_TRACK_OPTIONS", False),
            schwab_app_key=os.environ.get("SCHWAB_APP_KEY", "").strip(),
            schwab_app_secret=os.environ.get("SCHWAB_APP_SECRET", "").strip(),
            schwab_token=os.environ.get("SCHWAB_TOKEN", "").strip(),
            schwab_token_file=os.environ.get("SCHWAB_TOKEN_FILE", "").strip(),
        )

    def validate(self) -> None:
        super().validate()
        if not (self.portfolio_json or self.portfolio_file):
            raise ConfigError(
                "No portfolio source: set PORTFOLIO_JSON or PORTFOLIO_FILE"
            )
