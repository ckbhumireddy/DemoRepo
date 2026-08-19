"""Fixed evaluation harness: score = worst CAGR across start years.

Any bust disqualifies (score None). The stress gate re-scores with
every daily move amplified 1.25x — a config that busts under stress is
not deployable no matter its raw score.
"""

from __future__ import annotations

import csv
import datetime as dt
import json
import os
from typing import Dict, List, Optional, Tuple

from .ladder import simulate

START_YEARS = (1990, 2000, 2007, 2014, 2020)
STRESS_AMPLIFY = 1.25
RESULTS_FILE = "state/research_results.tsv"


def champion_cfg(preset: dict) -> Dict:
    """A champions.json preset, translated to a ladder config."""
    if preset.get("base_pct", 0) > 0:
        base, pct = preset["base_pct"], True
    else:
        base, pct = preset["base_notional"], False
    cfg = {"base": base, "pct": pct, "factor": preset["factor"],
           "cap": preset["max_notional"]}
    if preset.get("withdraw_at"):
        cfg["withdraw_at"] = preset["withdraw_at"]
        cfg["withdraw_amount"] = preset["withdraw_amount"]
    return cfg


def score(
    rows: List[Tuple[object, float]],
    cfg: Dict,
    *,
    start_years=START_YEARS,
    amplify: float = 1.0,
) -> Optional[Dict]:
    """Worst/best CAGR across start years; None if any start busts."""
    cagrs, full = [], None
    for year in start_years:
        subset = [(d, c) for d, c in rows if d.year >= year]
        if len(subset) < 2:
            continue    # start year beyond the data — nothing to score
        result = simulate(subset, cfg, amplify=amplify)
        if result is None:
            return None
        cagrs.append(result["cagr"])
        if year == min(start_years):
            full = result
    if full is None:
        raise ValueError("no start year overlaps the price data")
    return {
        "worst_cagr": min(cagrs),
        "best_cagr": max(cagrs),
        "final_full": full["final"],
        "max_dd_full": full["max_dd"],
    }


def experiment(
    tag: str,
    grid: List[Dict],
    rows: List[Tuple[object, float]],
    *,
    results_file: str = RESULTS_FILE,
    start_years=START_YEARS,
) -> List[Tuple[Optional[Dict], Dict]]:
    """Score every config, append to the results log, return ranked."""
    out = []
    directory = os.path.dirname(results_file)
    if directory:
        os.makedirs(directory, exist_ok=True)
    new = not os.path.exists(results_file)
    with open(results_file, "a", newline="") as fh:
        writer = csv.writer(fh, delimiter="\t")
        if new:
            writer.writerow(["tag", "cfg", "worst_cagr", "final_full",
                             "max_dd_full"])
        for cfg in grid:
            metrics = score(rows, cfg, start_years=start_years)
            if metrics is None:
                writer.writerow([tag, cfg, "BUST", "", ""])
            else:
                writer.writerow([
                    tag, cfg, round(metrics["worst_cagr"], 4),
                    round(metrics["final_full"], 2),
                    round(metrics["max_dd_full"], 3),
                ])
            out.append((metrics, cfg))
    out.sort(key=lambda t: float("-inf") if t[0] is None
             else t[0]["worst_cagr"], reverse=True)
    return out


# --------------------------------------------------------------------- #
# Promotion: research result -> live-service champion artifact
# --------------------------------------------------------------------- #
# Ladder knobs the live trader cannot execute; configs using them are
# research-only and must not be promoted.
_UNSUPPORTED_LIVE = ("enter_after", "max_lev", "withdraw_at")


def promote(
    name: str,
    cfg: Dict,
    metrics: Dict,
    stressed: Dict,
    *,
    champions_file: str,
    today: Optional[dt.date] = None,
) -> dict:
    """Write a config into champions.json as a named, deployable preset.

    The caller must have verified metrics (no bust) and stressed (no
    bust under 1.25x moves); passing None for either raises.
    """
    if metrics is None or stressed is None:
        raise ValueError("only stress-surviving configs can be promoted")
    for key in _UNSUPPORTED_LIVE:
        if cfg.get(key):
            raise ValueError(
                f"{key!r} is not supported by the live trader; "
                "cannot promote this config"
            )
    if cfg.get("reset", "full") != "full":
        raise ValueError("the live trader only supports reset='full'")
    today = today or dt.date.today()
    entry = {
        "description": (
            f"Promoted {today.isoformat()} by martingale_research: worst "
            f"CAGR {metrics['worst_cagr'] * 100:.2f}% across start years "
            f"{START_YEARS}, max drawdown "
            f"{metrics['max_dd_full'] * 100:.1f}%, stress(1.25x) worst "
            f"CAGR {stressed['worst_cagr'] * 100:.2f}%."
        ),
        "base_pct": cfg["base"] if cfg.get("pct") else 0.0,
        "base_notional": 0.0 if cfg.get("pct") else cfg["base"],
        "factor": cfg["factor"],
        "max_notional": cfg.get("cap", 160000.0),
    }
    with open(champions_file, encoding="utf-8") as fh:
        champions = json.load(fh)
    champions[name] = entry
    with open(champions_file, "w", encoding="utf-8") as fh:
        json.dump(champions, fh, indent=2)
        fh.write("\n")
    return entry
