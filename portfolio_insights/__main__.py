"""Command-line entrypoint for the portfolio insights service.

Examples
--------
End-of-day insights email:
    python -m portfolio_insights --mode eod

Midday threshold check (emails only when something crossed a threshold):
    python -m portfolio_insights --mode midday

Preview either mode without emailing or writing state:
    python -m portfolio_insights --mode eod --dry-run
"""

from __future__ import annotations

import argparse
import logging
import sys
from typing import List, Optional

from earnings_notifier.config import ConfigError

from .config import InsightsConfig
from .service import MODES, run_insights


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="portfolio_insights",
        description="Email rule-based insights about the user's portfolio.",
    )
    p.add_argument("--mode", choices=MODES, required=True,
                   help="eod = full daily insights; midday = threshold alert")
    p.add_argument("--dry-run", action="store_true",
                   help="render and log, but never email or write state")
    p.add_argument("--portfolio-file", type=str, default=None,
                   help="portfolio JSON path (default portfolio.json)")
    p.add_argument("--env-file", type=str, default=None,
                   help="load environment variables from this file first")
    p.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = _build_parser().parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if args.env_file:
        from earnings_notifier.__main__ import _load_env_file

        _load_env_file(args.env_file)

    config = InsightsConfig.from_env()
    if args.dry_run:
        config.dry_run = True
    if args.portfolio_file is not None:
        config.portfolio_file = args.portfolio_file

    try:
        result = run_insights(config, args.mode)
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001
        logging.getLogger(__name__).exception("run failed")
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    tail = "(dry-run, nothing sent)" if config.dry_run else (
        f"{result.emails_sent} email(s) sent."
    )
    print(
        f"Done. Mode {result.mode}: {result.positions} position(s), "
        f"{result.insights} insight(s), {result.alerts} alert(s). " + tail
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
