"""Command-line entrypoint for the earnings paper trader.

Examples
--------
Morning phase (close due positions, send the daily plan email):
    python -m earnings_trader --phase morning

Entry phase near the close (open tonight's / tomorrow morning's trades):
    python -m earnings_trader --phase entry

Preview either phase without trading or emailing:
    python -m earnings_trader --phase morning --dry-run
"""

from __future__ import annotations

import argparse
import logging
import sys
from typing import List, Optional

from earnings_notifier.config import ConfigError

from .config import TraderConfig
from .service import PHASES, run_trader


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="earnings_trader",
        description="Paper-trade option strategies around earnings reports.",
    )
    p.add_argument("--phase", choices=PHASES, required=True,
                   help="morning = close + daily plan; entry = open trades")
    p.add_argument("--dry-run", action="store_true",
                   help="evaluate and render, but never trade or email")
    p.add_argument("--window-file", type=str, default=None,
                   help="window JSON written by the notifier (default out/window.json)")
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

    config = TraderConfig.from_env()
    if args.dry_run:
        config.dry_run = True
    if args.window_file is not None:
        config.window_file = args.window_file

    try:
        result = run_trader(config, args.phase)
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001
        logging.getLogger(__name__).exception("run failed")
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    tail = "(dry-run, nothing traded or sent)" if config.dry_run else (
        f"{result.emails_sent} email(s) sent."
    )
    print(
        f"Done. Phase {result.phase}: {result.candidates} candidate(s), "
        f"{result.opened} opened, {result.closed} closed. " + tail
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
