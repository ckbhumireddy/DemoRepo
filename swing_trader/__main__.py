"""Command-line entrypoint for the swing-trading service.

The lifecycle a strategy walks, in commands:

    # 1. Prove it on history (records the verdict in the registry):
    python -m swing_trader backtest

    # 2. See where every strategy stands:
    python -m swing_trader status

    # 3. Once approved, scan and email today's setups:
    python -m swing_trader scan

    # Preview a scan without emailing (works even before approval —
    # unapproved strategies print instead of emailing regardless):
    python -m swing_trader scan --dry-run

    # Backtest specific tickers via the fallback feed:
    python -m swing_trader backtest --tickers INTC,BA,NKE

    # Tune the strategy: grid-search on the train segment, judge finalists
    # out-of-sample, and promote a validation-passing champion:
    python -m swing_trader research
"""

from __future__ import annotations

import argparse
import logging
import sys
from typing import List, Optional

from earnings_notifier.config import ConfigError

from .config import SwingConfig


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="swing_trader",
        description="Swing-trading strategies, backtest-gated before practice.",
    )
    p.add_argument("--env-file", type=str, default=None,
                   help="load environment variables from this file first")
    p.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    sub = p.add_subparsers(dest="command", required=True)

    scan = sub.add_parser("scan", help="evaluate today's bars and email "
                                       "approved strategies' setups")
    scan.add_argument("--strategy", default=None, help="override the strategy")
    scan.add_argument("--dry-run", action="store_true",
                      help="render and log, but never email")

    bt = sub.add_parser("backtest", help="replay history through the strategy "
                                         "and record the verdict")
    bt.add_argument("--strategy", default=None, help="override the strategy")
    bt.add_argument("--tickers", default=None,
                    help="comma-separated tickers to test instead of the "
                         "discovered candidates")
    bt.add_argument("--dry-run", action="store_true",
                    help="score the backtest but do not write the registry")

    rs = sub.add_parser("research", help="grid-tune the strategy and promote "
                                         "a validation-passing champion")
    rs.add_argument("--strategy", default=None, help="override the strategy")
    rs.add_argument("--tickers", default=None,
                    help="comma-separated tickers instead of discovery")
    rs.add_argument("--train-fraction", type=float, default=0.7,
                    help="history share used for tuning (rest validates)")
    rs.add_argument("--dry-run", action="store_true",
                    help="report the champion but do not promote it")

    sub.add_parser("status", help="show every strategy's backtest standing")
    return p


def _load_config(args) -> SwingConfig:
    if args.env_file:
        from earnings_notifier.__main__ import _load_env_file

        _load_env_file(args.env_file)
    config = SwingConfig.from_env()
    if getattr(args, "dry_run", False):
        config.dry_run = True
    if getattr(args, "strategy", None):
        config.swing_strategy = args.strategy.strip().lower()
    return config


def _cmd_status(config: SwingConfig) -> int:
    from .registry import Registry
    from .strategies import strategy_names

    registry = Registry(config.swing_registry_file,
                        config.swing_approval_ttl_days)
    print(f"{'STRATEGY':<16} {'STANDING':<10} {'TRADES':>6} {'WIN':>5} "
          f"{'PF':>6} {'EXP':>7} {'MAXDD':>6}  TESTED")
    for name in strategy_names():
        rec = registry.get(name)
        if not rec:
            print(f"{name:<16} {'untested':<10} {'—':>6} {'—':>5} {'—':>6} "
                  f"{'—':>7} {'—':>6}  run: python -m swing_trader backtest")
            continue
        standing = "APPROVED" if registry.is_approved(name) else "rejected"
        print(
            f"{name:<16} {standing:<10} {rec['trades']:>6} "
            f"{rec['win_rate'] * 100:>4.0f}% {rec['profit_factor']:>6.2f} "
            f"{rec['expectancy'] * 100:>6.2f}% {rec['max_drawdown'] * 100:>5.1f}%  "
            f"{rec['tested_at']}"
        )
        for reason in rec.get("reasons") or []:
            print(f"{'':<16}   - {reason}")
    return 0


def _cmd_backtest(config: SwingConfig, args) -> int:
    from .service import run_strategy_backtest

    history = None
    if args.tickers:
        from .service import _yahoo_history

        tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
        history = _yahoo_history(config, tickers, None)
        missing = sorted(set(tickers) - set(history))
        if missing:
            print(f"No history for: {', '.join(missing)}", file=sys.stderr)
    if args.dry_run:
        config.swing_registry_file = ""      # score, but record nothing
    result, record = run_strategy_backtest(config, history=history)
    verdict = "APPROVED for practice" if record.approved else "REJECTED"
    print(
        f"{result.strategy}: {result.trades} trade(s) across "
        f"{result.tickers} ticker(s) [{record.span or 'no span'}]\n"
        f"  win rate {result.win_rate:.0%} · profit factor "
        f"{record.profit_factor:.2f} · expectancy {result.expectancy:+.2%} "
        f"per trade · max drawdown {result.max_drawdown:.1%} · avg hold "
        f"{result.avg_hold:.1f} bars\n"
        f"  -> {verdict}"
    )
    for reason in record.reasons:
        print(f"     - {reason}")
    if args.dry_run:
        print("  (dry-run: verdict not recorded)")
    return 0 if record.approved else 1


def _cmd_research(config: SwingConfig, args) -> int:
    from .research import format_report, promote, run_research
    from .service import _yahoo_history, gather_history

    strategy = config.build_strategy()
    if args.tickers:
        tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
        history = _yahoo_history(config, tickers, None)
    else:
        history, note = gather_history(config, strategy)
        if note:
            print(note)
    if not history:
        print("No history to research against.", file=sys.stderr)
        return 1
    report = run_research(
        strategy.name, history, config.thresholds(),
        train_fraction=args.train_fraction,
        max_hold=config.swing_max_hold,
    )
    print(format_report(report))
    if report.promoted and not args.dry_run:
        entry = promote(report)
        print("\nChampion params now in force: " + str(entry["params"]))
    elif report.promoted:
        print("\n(dry-run: champion not written to champions.json)")
    return 0 if report.promoted else 1


def _cmd_scan(config: SwingConfig) -> int:
    from .service import run_scan

    result = run_scan(config)
    tail = (
        "(not approved — printed, not emailed)" if not result.approved
        else "(dry-run, nothing sent)" if config.dry_run
        else f"{result.emails_sent} email(s) sent."
    )
    print(
        f"Done. {result.signals} setup(s) from {result.candidates} "
        f"candidate(s) via {result.strategy}. " + tail
    )
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        config = _load_config(args)
        if args.command == "status":
            return _cmd_status(config)
        if args.command == "backtest":
            return _cmd_backtest(config, args)
        if args.command == "research":
            return _cmd_research(config, args)
        return _cmd_scan(config)
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2
    except KeyError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001
        logging.getLogger(__name__).exception("run failed")
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
