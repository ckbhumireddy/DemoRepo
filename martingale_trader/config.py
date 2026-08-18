"""Martingale trader configuration: the notifier's Config plus sizing knobs."""

from __future__ import annotations

import dataclasses
import os
from dataclasses import dataclass

from earnings_notifier.config import Config, ConfigError, _get_int
from earnings_trader.config import _get_float


@dataclass
class MartingaleConfig(Config):
    """Runtime configuration for the SPX martingale paper trader."""

    # State location. Lives under state/ so the workflow cache persists it
    # between runs.
    martingale_state_file: str = "state/martingale.json"
    martingale_start_balance: float = 25000.0
    # The stake ladder: notional = base * 2**step, capped at
    # base * 2**max_doublings (the "table limit").
    martingale_base_notional: float = 5000.0
    martingale_max_doublings: int = 6
    # Schwab symbol for the S&P 500 index.
    martingale_symbol: str = "$SPX"

    # Charles Schwab Trader API — REQUIRED: this trader prices from Schwab
    # only (no Yahoo fallback); a run without usable credentials fails.
    schwab_app_key: str = ""
    schwab_app_secret: str = ""
    schwab_token: str = ""
    schwab_token_file: str = ""

    @classmethod
    def from_env(cls) -> "MartingaleConfig":
        base = dataclasses.asdict(Config.from_env())
        return cls(
            **base,
            martingale_state_file=os.environ.get(
                "MARTINGALE_STATE_FILE", "state/martingale.json"
            ).strip(),
            martingale_start_balance=_get_float(
                "MARTINGALE_START_BALANCE", 25000.0
            ),
            martingale_base_notional=_get_float(
                "MARTINGALE_BASE_NOTIONAL", 5000.0
            ),
            martingale_max_doublings=_get_int("MARTINGALE_MAX_DOUBLINGS", 6),
            martingale_symbol=os.environ.get(
                "MARTINGALE_SYMBOL", "$SPX"
            ).strip(),
            schwab_app_key=os.environ.get("SCHWAB_APP_KEY", "").strip(),
            schwab_app_secret=os.environ.get("SCHWAB_APP_SECRET", "").strip(),
            schwab_token=os.environ.get("SCHWAB_TOKEN", "").strip(),
            schwab_token_file=os.environ.get("SCHWAB_TOKEN_FILE", "").strip(),
        )

    def validate(self) -> None:
        super().validate()
        if self.martingale_start_balance <= 0:
            raise ConfigError("MARTINGALE_START_BALANCE must be > 0")
        if self.martingale_base_notional <= 0:
            raise ConfigError("MARTINGALE_BASE_NOTIONAL must be > 0")
        if self.martingale_max_doublings < 0:
            raise ConfigError("MARTINGALE_MAX_DOUBLINGS must be >= 0")
