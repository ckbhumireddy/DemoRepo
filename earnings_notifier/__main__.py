"""Command-line entrypoint.

Examples
--------
Send the real digest (needs SMTP_* + EMAIL_* env vars):
    python -m earnings_notifier

Preview without sending, against live Yahoo Finance data, first 25 tickers:
    python -m earnings_notifier --dry-run --limit 25

Preview the email layout completely offline (no network, canned data):
    python -m earnings_notifier --demo
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import os
import sys
from typing import List, Optional

from .config import Config, ConfigError
from .service import run


def _load_env_file(path: str) -> None:
    """Minimal .env loader (KEY=VALUE per line); no external dependency."""
    if not os.path.exists(path):
        print(f"env file not found: {path}", file=sys.stderr)
        return
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            os.environ.setdefault(key, value)


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="earnings_notifier",
        description="Email a digest of S&P 500 earnings coming up in ~1 week.",
    )
    p.add_argument("--dry-run", action="store_true",
                   help="render and print the digest instead of sending email")
    p.add_argument("--demo", action="store_true",
                   help="use built-in sample data (fully offline); implies --dry-run")
    p.add_argument("--lead-days", type=int, default=None,
                   help="days ahead to notify (default 7 / LEAD_DAYS)")
    p.add_argument("--window-days", type=int, default=None,
                   help="+/- tolerance around lead days (default 0 / WINDOW_DAYS)")
    p.add_argument("--limit", type=int, default=None,
                   help="only look up the first N tickers (testing)")
    p.add_argument("--tickers", type=str, default=None,
                   help="comma-separated tickers to use instead of the S&P 500 list")
    p.add_argument("--extra-tickers", type=str, default=None,
                   help="comma-separated tickers to ADD to the S&P 500 list (watchlist)")
    p.add_argument("--env-file", type=str, default=None,
                   help="load environment variables from this file first")
    p.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    return p


def _demo_provider(today: dt.date):
    """A static provider with canned events for offline layout previews."""
    from .earnings import EarningsEvent

    class _Static:
        def __init__(self):
            self._events = {
                "AAPL": EarningsEvent("AAPL", today + dt.timedelta(days=7), True),
                "MSFT": EarningsEvent("MSFT", today + dt.timedelta(days=7), True),
                "NVDA": EarningsEvent("NVDA", today + dt.timedelta(days=7), False),
                "AMZN": EarningsEvent("AMZN", today + dt.timedelta(days=3), True),
                "TSLA": EarningsEvent("TSLA", today + dt.timedelta(days=30), True),
            }

        def next_earnings_date(self, ticker: str):
            return self._events.get(ticker)

    return _Static(), ["AAPL", "MSFT", "NVDA", "AMZN", "TSLA"]


def main(argv: Optional[List[str]] = None) -> int:
    args = _build_parser().parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if args.env_file:
        _load_env_file(args.env_file)

    config = Config.from_env()
    if args.dry_run or args.demo:
        config.dry_run = True
    if args.lead_days is not None:
        config.lead_days = args.lead_days
    if args.window_days is not None:
        config.window_days = args.window_days
    if args.limit is not None:
        config.ticker_limit = args.limit
    if args.extra_tickers:
        config.extra_tickers = [
            t.strip() for t in args.extra_tickers.split(",") if t.strip()
        ]

    today = dt.date.today()
    provider = None
    tickers = None

    if args.demo:
        provider, tickers = _demo_provider(today)
    elif args.tickers:
        tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]

    try:
        result = run(config, today=today, provider=provider, tickers=tickers)
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001
        logging.getLogger(__name__).exception("run failed")
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print(
        f"Done. {result.resolved}/{result.total_tickers} tickers resolved, "
        f"{len(result.selected)} in window. "
        + ("(dry-run, no email sent)" if not result.notified else "Email sent.")
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
