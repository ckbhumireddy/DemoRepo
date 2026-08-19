"""Condor trader configuration: the notifier's Config plus condor knobs."""

from __future__ import annotations

import dataclasses
import os
from dataclasses import dataclass

import json

from earnings_notifier.config import Config, ConfigError, _get_int
from earnings_trader.config import _get_float

DEFAULT_QTY_LADDER = (1, 2, 7, 20, 51)


def _parse_qty_ladder(raw: str) -> tuple:
    """Parse CONDOR_QTY_LADDER: JSON list, comma list, or level map.

    A levelQuantityMap object ({"1": 0, "2": 1, ...}) is read in level
    order; leading zero-quantity levels are the idle state and dropped.
    """
    raw = (raw or "").strip()
    if not raw:
        return DEFAULT_QTY_LADDER
    try:
        if raw.startswith(("{", "[")):
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                values = [int(parsed[k]) for k in sorted(parsed, key=int)]
            else:
                values = [int(v) for v in parsed]
        else:
            values = [int(p) for p in raw.split(",") if p.strip()]
    except (ValueError, TypeError, KeyError) as exc:
        raise ConfigError(f"CONDOR_QTY_LADDER is not parseable: {raw!r}") from exc
    while values and values[0] == 0:
        values.pop(0)
    if not values:
        raise ConfigError("CONDOR_QTY_LADDER has no tradable levels")
    return tuple(values)


@dataclass
class CondorConfig(Config):
    """Runtime configuration for the SPX iron-condor paper trader."""

    # State location. Lives under state/ so the workflow cache persists it
    # between runs.
    condor_state_file: str = "state/condor.json"
    condor_start_balance: float = 25000.0
    # The condor shape (defaults mirror the reference SPXW trade: $5
    # wings, short strikes ~1.25% out-of-the-money each side).
    condor_otm_pct: float = 1.25       # short-strike distance from spot, %
    condor_wing: float = 5.0           # wing width in index points
    condor_min_dte: int = 5            # nearest expiry at least this far out
    condor_max_dte: int = 12           # ... and no further than this
    condor_min_credit: float = 0.50    # skip setups collecting less (per share)
    # The martingale quantity ladder: contracts per consecutive-loss step,
    # holding at the last entry until a win resets to the first. Capped by
    # max_risk and the balance. CONDOR_QTY_LADDER accepts a JSON list
    # ("[1, 2, 7, 20, 51]"), a comma list ("1,2,7,20,51"), or a
    # levelQuantityMap JSON object ({"1": 0, "2": 1, ...} — leading zero
    # levels are the idle state and are dropped).
    condor_qty_ladder: tuple = (1, 2, 7, 20, 51)
    condor_max_risk: float = 25000.0   # max loss allowed on one position
    # Schwab symbol for the S&P 500 index (SPXW chains live under $SPX).
    condor_symbol: str = "$SPX"

    # Charles Schwab Trader API — REQUIRED: chains and closes come from
    # Schwab only (no Yahoo fallback); a run without credentials fails.
    schwab_app_key: str = ""
    schwab_app_secret: str = ""
    schwab_token: str = ""
    schwab_token_file: str = ""

    @classmethod
    def from_env(cls) -> "CondorConfig":
        base = dataclasses.asdict(Config.from_env())
        return cls(
            **base,
            condor_state_file=os.environ.get(
                "CONDOR_STATE_FILE", "state/condor.json"
            ).strip(),
            condor_start_balance=_get_float("CONDOR_START_BALANCE", 25000.0),
            condor_otm_pct=_get_float("CONDOR_OTM_PCT", 1.25),
            condor_wing=_get_float("CONDOR_WING", 5.0),
            condor_min_dte=_get_int("CONDOR_MIN_DTE", 5),
            condor_max_dte=_get_int("CONDOR_MAX_DTE", 12),
            condor_min_credit=_get_float("CONDOR_MIN_CREDIT", 0.50),
            condor_qty_ladder=_parse_qty_ladder(
                os.environ.get("CONDOR_QTY_LADDER", "")
            ),
            condor_max_risk=_get_float("CONDOR_MAX_RISK", 25000.0),
            condor_symbol=os.environ.get("CONDOR_SYMBOL", "$SPX").strip(),
            schwab_app_key=os.environ.get("SCHWAB_APP_KEY", "").strip(),
            schwab_app_secret=os.environ.get("SCHWAB_APP_SECRET", "").strip(),
            schwab_token=os.environ.get("SCHWAB_TOKEN", "").strip(),
            schwab_token_file=os.environ.get("SCHWAB_TOKEN_FILE", "").strip(),
        )

    def validate(self) -> None:
        super().validate()
        if self.condor_start_balance <= 0:
            raise ConfigError("CONDOR_START_BALANCE must be > 0")
        if not 0 < self.condor_otm_pct < 20:
            raise ConfigError("CONDOR_OTM_PCT must be between 0 and 20")
        if self.condor_wing <= 0:
            raise ConfigError("CONDOR_WING must be > 0")
        if not 0 <= self.condor_min_dte <= self.condor_max_dte:
            raise ConfigError("CONDOR_MIN_DTE must be <= CONDOR_MAX_DTE")
        if not self.condor_qty_ladder or any(
            q < 1 for q in self.condor_qty_ladder
        ):
            raise ConfigError(
                "CONDOR_QTY_LADDER must contain at least one quantity >= 1"
            )
        if self.condor_max_risk <= 0:
            raise ConfigError("CONDOR_MAX_RISK must be > 0")
