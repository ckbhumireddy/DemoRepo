"""CLI for the ladder research harness.

Examples
--------
Score a named champion (reads martingale_trader/champions.json):
    python -m martingale_research --champion tripledip

Run a grid experiment (JSON list of ladder configs):
    python -m martingale_research --tag exp1 \
      --grid "[{\"base\": 0.3, \"pct\": true, \"factor\": 3.0}]"

Add --stress to also run the 1.25x amplified-moves gate.
"""

from __future__ import annotations

import argparse
import json
import sys

from . import data, harness


def _print(prefix, metrics, cfg):
    if metrics is None:
        print(f"  {prefix}BUST                {cfg}")
    else:
        print(f"  {prefix}worst {metrics['worst_cagr'] * 100:5.2f}%  "
              f"full-run ${metrics['final_full']:>12,.0f}  "
              f"dd {metrics['max_dd_full'] * 100:4.1f}%  {cfg}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="martingale_research")
    parser.add_argument("--tag", default="adhoc",
                        help="experiment tag for the results log")
    parser.add_argument("--grid", type=str, default=None,
                        help="JSON list of ladder configs to score")
    parser.add_argument("--champion", type=str, default=None,
                        help="score a named preset from champions.json")
    parser.add_argument("--stress", action="store_true",
                        help="also score with moves amplified 1.25x")
    parser.add_argument("--promote", type=str, default=None, metavar="NAME",
                        help="write the best stress-surviving config into "
                             "champions.json under this name")
    parser.add_argument("--results", default=harness.RESULTS_FILE,
                        help="results TSV path")
    args = parser.parse_args(argv)

    if args.champion:
        from martingale_trader.config import load_champion

        grid = [harness.champion_cfg(load_champion(args.champion))]
    elif args.grid:
        grid = json.loads(args.grid)
    else:
        parser.error("pass --grid or --champion")

    rows = data.closes()
    print(f"== {args.tag} ({len(rows)} days, "
          f"{rows[0][0]}..{rows[-1][0]}) ==")
    ranked = harness.experiment(args.tag, grid, rows,
                                results_file=args.results)
    for metrics, cfg in ranked:
        _print("", metrics, cfg)
        if args.stress or args.promote:
            stressed = harness.score(rows, cfg,
                                     amplify=harness.STRESS_AMPLIFY)
            _print("stress: ", stressed, cfg)

    if args.promote:
        from martingale_trader.config import CHAMPIONS_FILE

        for metrics, cfg in ranked:   # best first; skip stress failures
            if metrics is None:
                continue
            stressed = harness.score(rows, cfg,
                                     amplify=harness.STRESS_AMPLIFY)
            if stressed is None:
                print(f"  not promotable (busts under stress): {cfg}")
                continue
            entry = harness.promote(args.promote, cfg, metrics, stressed,
                                    champions_file=CHAMPIONS_FILE)
            print(f"\nPromoted {args.promote!r} to champions.json: {entry}")
            print(f"Deploy with MARTINGALE_CHAMPION={args.promote}")
            return 0
        print("No config survived the stress gate; nothing promoted.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
