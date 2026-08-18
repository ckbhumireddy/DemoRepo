"""Command-line entrypoint for the IV-rank scanner.

Examples
--------
Full scan + email:
    python -m iv_scanner

Preview without emailing (portfolio-only universe is much faster):
    IVSCAN_UNIVERSE=portfolio python -m iv_scanner --dry-run
"""

from __future__ import annotations

import argparse
import logging
import sys
from typing import List, Optional

from earnings_notifier.config import ConfigError

from .config import IVScanConfig
from .service import run_scan


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="iv_scanner",
        description="Email the daily top-N IV-rank scan with options ideas.",
    )
    p.add_argument("--dry-run", action="store_true",
                   help="render and log, but never email or write state")
    p.add_argument("--universe", choices=["sp500", "portfolio"], default=None,
                   help="override the scan universe")
    p.add_argument("--top", type=int, default=None, help="override top N")
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

    config = IVScanConfig.from_env()
    if args.dry_run:
        config.dry_run = True
    if args.universe:
        config.ivscan_universe = args.universe
    if args.top:
        config.ivscan_top_n = args.top

    try:
        result = run_scan(config)
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
        f"Done. {result.ranked}/{result.scanned} ranked, top {result.top}, "
        f"{result.ideas} idea(s). " + tail
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
