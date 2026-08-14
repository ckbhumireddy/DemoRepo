"""Trader configuration: the notifier's Config plus trading knobs."""

from __future__ import annotations

import dataclasses
import os
from dataclasses import dataclass

from earnings_notifier.config import Config, _get_int


def _get_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except ValueError as exc:
        from earnings_notifier.config import ConfigError

        raise ConfigError(f"{name} must be a number, got {raw!r}") from exc


@dataclass
class TraderConfig(Config):
    """Runtime configuration for the earnings paper trader."""

    # Ledger location. Lives under state/ so the workflow cache persists it
    # between runs. Empty disables trading entirely.
    trader_ledger_file: str = "state/trader.json"
    trader_start_balance: float = 25000.0
    trader_max_risk: float = 5000.0    # max capital at risk per position

    # Signal knobs — TRADER_-prefixed so the trader is tunable independently
    # of the trade sheet even though the defaults match.
    history_quarters: int = 12
    rich_threshold: float = 1.25
    cheap_threshold: float = 0.80
    min_open_interest: float = 200
    min_option_volume: float = 50
    max_spread_pct: float = 8.0
    trader_max_workers: int = 4

    # Charles Schwab Trader API (optional) — same duck-typed fields the
    # shared build_provider() reads; Yahoo fallback when unset.
    schwab_app_key: str = ""
    schwab_app_secret: str = ""
    schwab_token: str = ""
    schwab_token_file: str = ""

    @classmethod
    def from_env(cls) -> "TraderConfig":
        base = dataclasses.asdict(Config.from_env())
        cfg = cls(
            **base,
            trader_ledger_file=os.environ.get(
                "TRADER_LEDGER_FILE", "state/trader.json"
            ).strip(),
            trader_start_balance=_get_float("TRADER_START_BALANCE", 25000.0),
            trader_max_risk=_get_float("TRADER_MAX_RISK", 5000.0),
            history_quarters=_get_int("TRADER_HISTORY_QUARTERS", 12),
            rich_threshold=_get_float("TRADER_RICH_THRESHOLD", 1.25),
            cheap_threshold=_get_float("TRADER_CHEAP_THRESHOLD", 0.80),
            min_open_interest=_get_float("TRADER_MIN_OPEN_INTEREST", 200),
            min_option_volume=_get_float("TRADER_MIN_OPTION_VOLUME", 50),
            max_spread_pct=_get_float("TRADER_MAX_SPREAD_PCT", 8.0),
            trader_max_workers=_get_int("TRADER_MAX_WORKERS", 4),
            schwab_app_key=os.environ.get("SCHWAB_APP_KEY", "").strip(),
            schwab_app_secret=os.environ.get("SCHWAB_APP_SECRET", "").strip(),
            schwab_token=os.environ.get("SCHWAB_TOKEN", "").strip(),
            schwab_token_file=os.environ.get("SCHWAB_TOKEN_FILE", "").strip(),
        )
        # The trader reads the hand-off file the notifier wrote.
        if not cfg.window_file:
            cfg.window_file = "out/window.json"
        return cfg
