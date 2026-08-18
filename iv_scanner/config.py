"""Scanner configuration: the notifier's Config plus scan knobs."""

from __future__ import annotations

import dataclasses
import os
from dataclasses import dataclass

from earnings_notifier.config import Config, _get_int


@dataclass
class IVScanConfig(Config):
    """Runtime configuration for the IV-rank scanner."""

    ivscan_top_n: int = 50
    ivscan_universe: str = "sp500"       # "sp500" | "portfolio"
    ivscan_max_workers: int = 4
    # Once-per-day send marker. Rides the shared state/ cache; empty
    # disables the marker (every run sends).
    ivscan_state_file: str = "state/ivscan.json"

    # Portfolio (for holding highlights); same sources as portfolio_insights.
    portfolio_json: str = ""
    portfolio_file: str = "portfolio.json"

    # Schwab market data — the scanner needs it for a full S&P 500 sweep;
    # without it the scan degrades to portfolio + earnings-window tickers
    # via Yahoo.
    schwab_app_key: str = ""
    schwab_app_secret: str = ""
    schwab_token: str = ""
    schwab_token_file: str = ""

    @classmethod
    def from_env(cls) -> "IVScanConfig":
        base = dataclasses.asdict(Config.from_env())
        cfg = cls(
            **base,
            ivscan_top_n=_get_int("IVSCAN_TOP_N", 50),
            ivscan_universe=os.environ.get("IVSCAN_UNIVERSE", "sp500").strip().lower(),
            ivscan_max_workers=_get_int("IVSCAN_MAX_WORKERS", 4),
            ivscan_state_file=os.environ.get(
                "IVSCAN_STATE_FILE", "state/ivscan.json"
            ).strip(),
            portfolio_json=os.environ.get("PORTFOLIO_JSON", "").strip(),
            portfolio_file=os.environ.get(
                "PORTFOLIO_FILE", "portfolio.json"
            ).strip(),
            schwab_app_key=os.environ.get("SCHWAB_APP_KEY", "").strip(),
            schwab_app_secret=os.environ.get("SCHWAB_APP_SECRET", "").strip(),
            schwab_token=os.environ.get("SCHWAB_TOKEN", "").strip(),
            schwab_token_file=os.environ.get("SCHWAB_TOKEN_FILE", "").strip(),
        )
        if not cfg.window_file:
            cfg.window_file = "out/window.json"
        return cfg
