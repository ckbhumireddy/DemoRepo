"""Martingale trader configuration: the notifier's Config plus sizing knobs."""

from __future__ import annotations

import dataclasses
import json
import os
from dataclasses import dataclass

from earnings_notifier.config import Config, ConfigError
from earnings_trader.config import _get_float

CHAMPIONS_FILE = os.path.join(os.path.dirname(__file__), "champions.json")
DEFAULT_CHAMPION = "tripledip"


def load_champion(name: str) -> dict:
    """A named sizing preset from champions.json."""
    with open(CHAMPIONS_FILE, encoding="utf-8") as fh:
        champions = json.load(fh)
    if name not in champions:
        raise ConfigError(
            f"Unknown champion {name!r}; available: "
            + ", ".join(sorted(champions))
        )
    return champions[name]


@dataclass
class MartingaleConfig(Config):
    """Runtime configuration for the SPX martingale paper trader."""

    # State location. Lives under state/ so the workflow cache persists it
    # between runs.
    martingale_state_file: str = "state/martingale.json"
    martingale_start_balance: float = 25000.0
    # The stake ladder:
    #   notional = min(base * factor**step, max_notional)
    # where base is base_pct of the current balance (base_pct=0 -> a
    # fixed base_notional dollar ladder). Defaults come from the
    # MARTINGALE_CHAMPION preset in champions.json; individual
    # MARTINGALE_* env vars override preset values.
    martingale_champion: str = DEFAULT_CHAMPION
    martingale_base_pct: float = 0.30
    martingale_base_notional: float = 5000.0
    martingale_factor: float = 3.0
    martingale_max_notional: float = 160000.0
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
        champion_name = os.environ.get(
            "MARTINGALE_CHAMPION", DEFAULT_CHAMPION
        ).strip() or DEFAULT_CHAMPION
        champion = load_champion(champion_name)
        return cls(
            **base,
            martingale_state_file=os.environ.get(
                "MARTINGALE_STATE_FILE", "state/martingale.json"
            ).strip(),
            martingale_start_balance=_get_float(
                "MARTINGALE_START_BALANCE", 25000.0
            ),
            martingale_champion=champion_name,
            martingale_base_pct=_get_float(
                "MARTINGALE_BASE_PCT", champion["base_pct"]
            ),
            martingale_base_notional=_get_float(
                "MARTINGALE_BASE_NOTIONAL", champion["base_notional"]
            ),
            martingale_factor=_get_float(
                "MARTINGALE_FACTOR", champion["factor"]
            ),
            martingale_max_notional=_get_float(
                "MARTINGALE_MAX_NOTIONAL", champion["max_notional"]
            ),
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
        if not 0.0 <= self.martingale_base_pct <= 1.0:
            raise ConfigError("MARTINGALE_BASE_PCT must be between 0 and 1")
        if self.martingale_base_pct == 0 and self.martingale_base_notional <= 0:
            raise ConfigError(
                "MARTINGALE_BASE_NOTIONAL must be > 0 when MARTINGALE_BASE_PCT is 0"
            )
        if self.martingale_factor <= 1.0:
            raise ConfigError("MARTINGALE_FACTOR must be > 1")
        if self.martingale_max_notional <= 0:
            raise ConfigError("MARTINGALE_MAX_NOTIONAL must be > 0")
