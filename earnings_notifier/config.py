"""Configuration loaded from environment variables.

All secrets (SMTP credentials, recipient list) come from the environment so
nothing sensitive is committed to the repository. In production these are
supplied as GitHub Actions secrets; locally you can use a ``.env`` file (see
``.env.example``) together with ``--env-file`` on the CLI.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import List


def _get_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _get_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer, got {raw!r}") from exc


def _get_list(name: str) -> List[str]:
    """Parse a comma- or semicolon-separated list, trimming blanks."""
    raw = os.environ.get(name, "")
    parts = [p.strip() for chunk in raw.split(";") for p in chunk.split(",")]
    return [p for p in parts if p]


class ConfigError(ValueError):
    """Raised when configuration is missing or invalid."""


@dataclass
class Config:
    """Runtime configuration for the notifier."""

    # --- SMTP / email ---
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_use_tls: bool = True   # STARTTLS on a plain connection (port 587)
    smtp_use_ssl: bool = False  # implicit TLS (port 465); overrides use_tls
    email_from: str = ""
    email_to: List[str] = field(default_factory=list)

    # --- Notification window ---
    # Notify for any earnings landing between ``min_days`` and ``lead_days``
    # from today, inclusive. Defaults cover "any time in the next week"
    # (today through 7 days out). Deduplication (below) makes sure each
    # earnings is emailed only once even though it stays in this window for
    # several days.
    lead_days: int = 7
    min_days: int = 0

    # --- Once-only state ---
    # To email each earnings just once, notified (ticker, date) pairs are
    # remembered in a small JSON file between runs. Set ``use_state`` False to
    # disable (every run reports the full window).
    use_state: bool = True
    state_file: str = "state/notified.json"
    state_retention_days: int = 120   # prune remembered entries older than this
    send_empty: bool = False          # email even when nothing new is due

    # --- Data fetching ---
    max_workers: int = 8          # concurrent yfinance lookups
    request_timeout: int = 20     # per-ticker timeout hint (seconds)
    ticker_limit: int = 0         # 0 = all constituents; >0 caps for testing
    extra_tickers: List[str] = field(default_factory=list)  # watchlist beyond S&P 500

    # --- Behaviour ---
    dry_run: bool = False         # render + print instead of sending email

    # --- Downstream hand-off ---
    # When set, the full notification window (pre-dedupe) is written to this
    # JSON file each run so the earnings analyzer can consume it.
    window_file: str = ""

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            smtp_host=os.environ.get("SMTP_HOST", "").strip(),
            smtp_port=_get_int("SMTP_PORT", 587),
            smtp_user=os.environ.get("SMTP_USER", "").strip(),
            smtp_password=os.environ.get("SMTP_PASSWORD", ""),
            smtp_use_tls=_get_bool("SMTP_USE_TLS", True),
            smtp_use_ssl=_get_bool("SMTP_USE_SSL", False),
            email_from=os.environ.get("EMAIL_FROM", "").strip(),
            email_to=_get_list("EMAIL_TO"),
            extra_tickers=_get_list("EXTRA_TICKERS"),
            lead_days=_get_int("LEAD_DAYS", 7),
            min_days=_get_int("MIN_DAYS", 0),
            use_state=_get_bool("USE_STATE", True),
            state_file=os.environ.get("STATE_FILE", "state/notified.json").strip()
            or "state/notified.json",
            state_retention_days=_get_int("STATE_RETENTION_DAYS", 120),
            send_empty=_get_bool("SEND_EMPTY", False),
            max_workers=_get_int("MAX_WORKERS", 8),
            request_timeout=_get_int("REQUEST_TIMEOUT", 20),
            ticker_limit=_get_int("TICKER_LIMIT", 0),
            dry_run=_get_bool("DRY_RUN", False),
            window_file=os.environ.get("WINDOW_FILE", "").strip(),
        )

    def validate(self) -> None:
        """Raise :class:`ConfigError` if the config cannot be used to send mail.

        Skipped in dry-run mode, where no email is actually sent.
        """
        if self.lead_days < 0:
            raise ConfigError("LEAD_DAYS must be >= 0")
        if self.min_days < 0:
            raise ConfigError("MIN_DAYS must be >= 0")
        if self.min_days > self.lead_days:
            raise ConfigError("MIN_DAYS must be <= LEAD_DAYS")
        if self.max_workers < 1:
            raise ConfigError("MAX_WORKERS must be >= 1")

        if self.dry_run:
            return

        missing = []
        if not self.smtp_host:
            missing.append("SMTP_HOST")
        if not self.email_from:
            missing.append("EMAIL_FROM")
        if not self.email_to:
            missing.append("EMAIL_TO")
        if missing:
            raise ConfigError(
                "Missing required email configuration: "
                + ", ".join(missing)
                + ". Set these environment variables or run with --dry-run."
            )
