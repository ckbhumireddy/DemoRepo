"""Analyzer configuration: the notifier's Config plus analysis tuning knobs."""

from __future__ import annotations

import dataclasses
import os
from dataclasses import dataclass

from earnings_notifier.config import Config, _get_bool, _get_int


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
class AnalyzerConfig(Config):
    """Runtime configuration for the trade-sheet analyzer."""

    # How many past quarters feed the history/reaction statistics.
    history_quarters: int = 12

    # Implied/historical move ratio thresholds for the rich/cheap verdict.
    rich_threshold: float = 1.25
    cheap_threshold: float = 0.80

    # Options liquidity gates (ATM contracts).
    min_open_interest: float = 200
    min_option_volume: float = 50
    max_spread_pct: float = 8.0

    # Fetching: chains + history are heavy, keep concurrency modest.
    analyzer_max_workers: int = 4
    analyzer_ticker_limit: int = 0     # 0 = analyze the whole window
    analyzer_send_empty: bool = False  # email even when nothing analyzed

    # Charles Schwab Trader API (optional). When all three are set, quotes,
    # chains, and price history come from Schwab in real time, with Yahoo as
    # automatic fallback. See scripts/schwab_auth.py for the token.
    schwab_app_key: str = ""
    schwab_app_secret: str = ""
    schwab_token: str = ""        # the token JSON itself (e.g. a repo secret)
    schwab_token_file: str = ""   # ...or a path to it on disk

    @classmethod
    def from_env(cls) -> "AnalyzerConfig":
        base = dataclasses.asdict(Config.from_env())
        cfg = cls(
            **base,
            history_quarters=_get_int("HISTORY_QUARTERS", 12),
            rich_threshold=_get_float("RICH_THRESHOLD", 1.25),
            cheap_threshold=_get_float("CHEAP_THRESHOLD", 0.80),
            min_open_interest=_get_float("MIN_OPEN_INTEREST", 200),
            min_option_volume=_get_float("MIN_OPTION_VOLUME", 50),
            max_spread_pct=_get_float("MAX_SPREAD_PCT", 8.0),
            analyzer_max_workers=_get_int("ANALYZER_MAX_WORKERS", 4),
            analyzer_ticker_limit=_get_int("ANALYZER_TICKER_LIMIT", 0),
            analyzer_send_empty=_get_bool("ANALYZER_SEND_EMPTY", False),
            schwab_app_key=os.environ.get("SCHWAB_APP_KEY", "").strip(),
            schwab_app_secret=os.environ.get("SCHWAB_APP_SECRET", "").strip(),
            schwab_token=os.environ.get("SCHWAB_TOKEN", "").strip(),
            schwab_token_file=os.environ.get("SCHWAB_TOKEN_FILE", "").strip(),
        )
        # The analyzer defaults to reading the hand-off file the notifier wrote.
        if not cfg.window_file:
            cfg.window_file = "out/window.json"
        return cfg
