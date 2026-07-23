"""Command-line interface for the earnings-crash screener.

Examples
--------
Scan the default watchlist with live Yahoo data::

    python -m stock_screener.cli screen

Scan specific tickers and show full detail::

    python -m stock_screener.cli screen --tickers AAPL NKE PYPL --detail

Loosen the filters and emit JSON::

    python -m stock_screener.cli screen --min-crash 0.05 --min-score 50 --json

Run against the built-in offline demo data (no network needed)::

    python -m stock_screener.cli demo
"""

from __future__ import annotations

import argparse
import sys
from typing import List, Optional

from .config import ScreenConfig
from .analysis.screener import Screener
from .report import render_detail, render_json, render_table
from .universe import DEFAULT_UNIVERSE, load_tickers_from_file

DISCLAIMER = (
    "This tool is for research and education only. It is NOT financial advice "
    "and does not place trades. Options carry substantial risk of loss. Do your "
    "own research and consider consulting a licensed advisor."
)


def _config_from_args(args: argparse.Namespace) -> ScreenConfig:
    cfg = ScreenConfig()
    if args.min_crash is not None:
        cfg.min_crash_pct = args.min_crash
    if args.min_score is not None:
        cfg.min_fundamental_score = args.min_score
    if args.lookback is not None:
        cfg.crash_lookback_days = args.lookback
    if args.include_recovered:
        cfg.require_still_depressed = False
    return cfg


def _resolve_tickers(args: argparse.Namespace) -> List[str]:
    if args.tickers:
        return [t.upper() for t in args.tickers]
    if args.tickers_file:
        return load_tickers_from_file(args.tickers_file)
    return list(DEFAULT_UNIVERSE)


def _output(candidates, args) -> None:
    if args.json:
        print(render_json(candidates))
        return
    print(render_table(candidates))
    if args.detail:
        print()
        for c in candidates:
            print(render_detail(c))


def _cmd_screen(args: argparse.Namespace) -> int:
    try:
        from .data.yahoo import YahooProvider
    except Exception as exc:  # pragma: no cover
        print(f"error: {exc}", file=sys.stderr)
        return 2

    try:
        provider = YahooProvider()
    except ImportError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    cfg = _config_from_args(args)
    tickers = _resolve_tickers(args)
    if not args.json:
        print(f"Scanning {len(tickers)} tickers...\n", file=sys.stderr)

    screener = Screener(provider, cfg)
    candidates = screener.screen(tickers)
    if args.limit:
        candidates = candidates[: args.limit]

    _output(candidates, args)
    if not args.json:
        print(f"\n{DISCLAIMER}", file=sys.stderr)
    return 0


def _cmd_demo(args: argparse.Namespace) -> int:
    """Run the screener against built-in synthetic data (offline)."""
    from .demo_data import build_demo_provider

    provider, tickers, today = build_demo_provider()
    cfg = _config_from_args(args)
    screener = Screener(provider, cfg)
    candidates = screener.screen(tickers, today=today)
    if args.limit:
        candidates = candidates[: args.limit]

    if not args.json:
        print("(demo mode — synthetic offline data)\n")
    _output(candidates, args)
    if not args.json:
        print(f"\n{DISCLAIMER}", file=sys.stderr)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="stock_screener",
        description="Find fundamentally strong stocks that crashed on earnings, "
        "with options-strategy ideas for a potential recovery.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def add_common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--tickers", nargs="+", help="Explicit tickers to scan.")
        p.add_argument("--tickers-file", help="File of tickers (one/row or CSV).")
        p.add_argument("--min-crash", type=float,
                       help="Min crash fraction, e.g. 0.08 for 8%%.")
        p.add_argument("--min-score", type=float,
                       help="Min fundamental quality score (0-100).")
        p.add_argument("--lookback", type=int,
                       help="Only earnings within this many days.")
        p.add_argument("--include-recovered", action="store_true",
                       help="Also include names that already bounced above pre-earnings.")
        p.add_argument("--detail", action="store_true",
                       help="Print full per-candidate detail.")
        p.add_argument("--json", action="store_true", help="Emit JSON.")
        p.add_argument("--limit", type=int, help="Cap number of results.")

    p_screen = sub.add_parser("screen", help="Scan tickers using live Yahoo data.")
    add_common(p_screen)
    p_screen.set_defaults(func=_cmd_screen)

    p_demo = sub.add_parser("demo", help="Run against offline synthetic data.")
    add_common(p_demo)
    p_demo.set_defaults(func=_cmd_demo)

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
