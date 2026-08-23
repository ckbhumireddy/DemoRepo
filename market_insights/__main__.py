"""Command-line entrypoint for the market-insights scan.

Examples
--------
Full scan + email:
    python -m market_insights

Preview without emailing (portfolio + watchlist only, much faster):
    INSIGHTS_UNIVERSE=portfolio python -m market_insights --dry-run

Loosen the screen to see more names:
    python -m market_insights --min-rvol 1.5 --top 40 --dry-run

Verify TradeStation credentials and that the live feed matches the parsers:
    python -m market_insights --check-feed
"""

from __future__ import annotations

import argparse
import logging
import sys
from typing import List, Optional

from earnings_notifier.config import ConfigError

from .config import MarketInsightsConfig
from .service import run_insights


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="market_insights",
        description="Email the tickers trading on unusual volume, read "
                    "against their short- and long-term trend.",
    )
    p.add_argument("--dry-run", action="store_true",
                   help="render and log, but never email or write state")
    p.add_argument("--universe", choices=["sp500", "portfolio"], default=None,
                   help="override the scan universe")
    p.add_argument("--top", type=int, default=None, help="override top N")
    p.add_argument("--min-rvol", type=float, default=None,
                   help="override the relative-volume threshold (default 2.0)")
    p.add_argument("--check-feed", nargs="?", const="MSFT", default=None,
                   metavar="SYMBOL",
                   help="probe the TradeStation quotes and barcharts endpoints "
                        "for one symbol (default MSFT) and exit; no email")
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

    config = MarketInsightsConfig.from_env()

    if args.check_feed:
        from .diagnostics import check_feed, format_check
        from .tradestation import build_session

        session = build_session(config)
        if session is None:
            print(
                "TradeStation is not configured. Set TRADESTATION_CLIENT_ID, "
                "TRADESTATION_CLIENT_SECRET and TRADESTATION_TOKEN (run "
                "scripts/tradestation_auth.py first), or pass --env-file.",
                file=sys.stderr,
            )
            return 2
        report = check_feed(session, args.check_feed.upper())
        print(format_check(report))
        return 0 if report.ok else 1

    if args.dry_run:
        config.dry_run = True
    if args.universe:
        config.insights_universe = args.universe
    if args.top:
        config.insights_top_n = args.top
    if args.min_rvol:
        config.insights_min_rvol = args.min_rvol

    try:
        result = run_insights(config)
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
        f"Done. {result.unusual} unusual of {result.analyzed} analyzed "
        f"({result.candidates} candidates from {result.universe} scanned). "
        + tail
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
