"""Command-line entrypoint for the SPX iron-condor martingale trader.

Examples
--------
Daily run (settle expired condors, open the next one, email):
    python -m condor_trader

Preview without trading or emailing:
    python -m condor_trader --dry-run
"""

from __future__ import annotations

import argparse
import logging
import sys
from typing import List, Optional

from earnings_notifier.config import ConfigError

from .config import CondorConfig
from .service import run_condor


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="condor_trader",
        description="Paper-trade weekly SPX iron condors with martingale "
                    "contract sizing (educational risk demonstration).",
    )
    p.add_argument("--dry-run", action="store_true",
                   help="evaluate and render, but never trade or email")
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

    config = CondorConfig.from_env()
    if args.dry_run:
        config.dry_run = True

    try:
        result = run_condor(config)
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001
        logging.getLogger(__name__).exception("run failed")
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if result.skipped:
        print("Done. Nothing to do (position already open, no setup, or busted).")
        return 0
    tail = "(dry-run, nothing traded or sent)" if config.dry_run else (
        f"{result.emails_sent} email(s) sent."
    )
    print(f"Done. {result.settled} settled, {result.opened} opened. " + tail)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
