"""Command-line entrypoint for the earnings trade sheet.

Examples
--------
Send the real trade sheet (needs SMTP_* + EMAIL_* env vars, and normally the
window file written by the notifier):
    python -m earnings_analyzer

Preview against live Yahoo data for two tickers:
    python -m earnings_analyzer --dry-run --tickers AAPL,MSFT

Preview the sheet layout completely offline (no network, canned data):
    python -m earnings_analyzer --demo
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import sys
from typing import List, Optional

from earnings_notifier.config import ConfigError

from .config import AnalyzerConfig
from .service import run_analyzer


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="earnings_analyzer",
        description="Email a deep-analysis trade sheet for upcoming earnings.",
    )
    p.add_argument("--dry-run", action="store_true",
                   help="render and print the trade sheet instead of emailing")
    p.add_argument("--demo", action="store_true",
                   help="use built-in sample data (fully offline); implies --dry-run")
    p.add_argument("--window-file", type=str, default=None,
                   help="window JSON written by the notifier (default out/window.json)")
    p.add_argument("--tickers", type=str, default=None,
                   help="comma-separated tickers to analyze instead of the window")
    p.add_argument("--limit", type=int, default=None,
                   help="only analyze the first N entries (testing)")
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

    config = AnalyzerConfig.from_env()
    if args.dry_run or args.demo:
        config.dry_run = True
    if args.window_file is not None:
        config.window_file = args.window_file
    if args.limit is not None:
        config.analyzer_ticker_limit = args.limit

    today = dt.date.today()
    provider = None
    entries = None
    tickers = None

    if args.demo:
        from .demo_data import build_demo_provider

        provider, entries = build_demo_provider(today)
    elif args.tickers:
        tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]

    try:
        result = run_analyzer(
            config, today=today, provider=provider, entries=entries, tickers=tickers
        )
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001
        logging.getLogger(__name__).exception("run failed")
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if result.notified:
        tail = "Trade sheet sent."
    elif config.dry_run:
        tail = "(dry-run, no email sent)"
    else:
        tail = "(nothing to analyze; no email sent)"
    print(
        f"Done. {result.analyzed}/{result.requested} setup(s) analyzed. " + tail
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
