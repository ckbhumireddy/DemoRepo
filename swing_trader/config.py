"""Swing-trader configuration: the notifier's Config plus strategy knobs."""

from __future__ import annotations

import dataclasses
import os
from dataclasses import dataclass

from earnings_notifier.config import Config, ConfigError, _get_bool, _get_int
from market_insights.config import _get_float


@dataclass
class SwingConfig(Config):
    """Runtime configuration for the swing-trading service."""

    swing_strategy: str = "distressed-sr"
    swing_top_n: int = 10                 # signals in the email
    swing_max_workers: int = 4
    swing_bars_back: int = 400            # history per candidate
    swing_max_candidates: int = 60        # bar-request budget per run
    swing_max_hold: int = 20              # backtest time stop, in bars

    # Strategy-1 parameter overrides. None = not overridden: the strategy's
    # own defaults apply, or the promoted champion's tuned values when one
    # exists. An explicit value here always wins over the champion.
    swing_min_drawdown: "float | None" = None
    swing_min_price: "float | None" = None
    swing_min_rr: "float | None" = None
    # Apply the researcher loop's promoted parameters (champions.json).
    swing_use_champion: bool = True

    # Approval gate.
    swing_registry_file: str = "state/swing_registry.json"
    swing_approval_ttl_days: int = 90
    swing_bt_min_trades: int = 20
    swing_bt_min_profit_factor: float = 1.3
    swing_bt_min_win_rate: float = 0.0
    swing_bt_max_drawdown: float = 0.35

    # TradeStation market data — same credentials as market_insights;
    # without it the scan degrades to EXTRA_TICKERS via Yahoo.
    tradestation_client_id: str = ""
    tradestation_client_secret: str = ""
    tradestation_token: str = ""
    tradestation_token_file: str = "tradestation_token.json"
    tradestation_environment: str = "live"

    @classmethod
    def from_env(cls) -> "SwingConfig":
        base = dataclasses.asdict(Config.from_env())
        return cls(
            **base,
            swing_strategy=os.environ.get(
                "SWING_STRATEGY", "distressed-sr"
            ).strip().lower(),
            swing_top_n=_get_int("SWING_TOP_N", 10),
            swing_max_workers=_get_int("SWING_MAX_WORKERS", 4),
            swing_bars_back=_get_int("SWING_BARS_BACK", 400),
            swing_max_candidates=_get_int("SWING_MAX_CANDIDATES", 60),
            swing_max_hold=_get_int("SWING_MAX_HOLD", 20),
            swing_min_drawdown=_get_float("SWING_MIN_DRAWDOWN", None),
            swing_min_price=_get_float("SWING_MIN_PRICE", None),
            swing_min_rr=_get_float("SWING_MIN_RR", None),
            swing_use_champion=_get_bool("SWING_USE_CHAMPION", True),
            swing_registry_file=os.environ.get(
                "SWING_REGISTRY_FILE", "state/swing_registry.json"
            ).strip(),
            swing_approval_ttl_days=_get_int("SWING_APPROVAL_TTL_DAYS", 90),
            swing_bt_min_trades=_get_int("SWING_BT_MIN_TRADES", 20),
            swing_bt_min_profit_factor=_get_float(
                "SWING_BT_MIN_PROFIT_FACTOR", 1.3
            ),
            swing_bt_min_win_rate=_get_float("SWING_BT_MIN_WIN_RATE", 0.0),
            swing_bt_max_drawdown=_get_float("SWING_BT_MAX_DRAWDOWN", 0.35),
            tradestation_client_id=os.environ.get(
                "TRADESTATION_CLIENT_ID", ""
            ).strip(),
            tradestation_client_secret=os.environ.get(
                "TRADESTATION_CLIENT_SECRET", ""
            ).strip(),
            tradestation_token=os.environ.get("TRADESTATION_TOKEN", "").strip(),
            tradestation_token_file=os.environ.get(
                "TRADESTATION_TOKEN_FILE", "tradestation_token.json"
            ).strip(),
            tradestation_environment=os.environ.get(
                "TRADESTATION_ENVIRONMENT", "live"
            ).strip().lower(),
        )

    def validate(self) -> None:
        super().validate()
        if self.swing_min_drawdown is not None and not (
            0 < self.swing_min_drawdown < 1
        ):
            raise ConfigError("SWING_MIN_DRAWDOWN must be between 0 and 1")
        if self.swing_min_rr is not None and self.swing_min_rr <= 0:
            raise ConfigError("SWING_MIN_RR must be > 0")
        if self.swing_bt_min_trades < 1:
            raise ConfigError("SWING_BT_MIN_TRADES must be >= 1")
        if self.swing_bars_back < 260:
            raise ConfigError(
                "SWING_BARS_BACK must be >= 260 (a year of history plus "
                "warmup is the minimum the strategies can read)"
            )

    def build_strategy(self):
        """The configured strategy: defaults < promoted champion < overrides."""
        from .strategies import get_strategy

        strategy = get_strategy(self.swing_strategy)
        if self.swing_use_champion:
            from .research import champion_params

            for key, value in champion_params(strategy.name).items():
                if hasattr(strategy, key):
                    setattr(strategy, key, value)
        overrides = {
            "min_drawdown": self.swing_min_drawdown,
            "min_price": self.swing_min_price,
            "min_rr": self.swing_min_rr,
        }
        for key, value in overrides.items():
            if value is not None and hasattr(strategy, key):
                setattr(strategy, key, value)
        return strategy

    def thresholds(self):
        from .registry import Thresholds

        return Thresholds(
            min_trades=self.swing_bt_min_trades,
            min_profit_factor=self.swing_bt_min_profit_factor,
            min_win_rate=self.swing_bt_min_win_rate,
            max_drawdown=self.swing_bt_max_drawdown,
        )
