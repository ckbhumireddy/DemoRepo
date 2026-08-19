import datetime as dt
import json

import pytest

from martingale_research.harness import (
    champion_cfg,
    experiment,
    promote,
    score,
)
from martingale_research.ladder import simulate

CHAMPION = {"base": 0.3, "pct": True, "factor": 3.0, "cap": 160000.0}


def _rows(closes, start=dt.date(1990, 1, 2)):
    return [(start + dt.timedelta(days=i), c) for i, c in enumerate(closes)]


# ---------------------------------------------------------------- ladder
def test_simulate_fixed_ladder_math():
    # -1% on $5k, then +2% on the tripled $15k stake.
    rows = _rows([100.0, 99.0, 100.98])
    r = simulate(rows, {"base": 5000.0, "factor": 3.0, "cap": 160000.0})
    assert round(r["final"], 2) == 25000.0 - 50.0 + 300.0


def test_simulate_pct_base_and_cap():
    rows = _rows([100.0, 99.0])
    # 30% of $25k = $7,500 at step 0; -1% costs $75.
    r = simulate(rows, CHAMPION)
    assert round(r["final"], 2) == 24925.0
    # A tiny cap clamps the stake.
    r = simulate(rows, {**CHAMPION, "cap": 1000.0})
    assert round(r["final"], 2) == 24990.0


def test_simulate_bust_returns_none():
    # A $1,000 fixed stake on a $100 account: one -60% day busts it.
    rows = _rows([100.0, 40.0])
    assert simulate(rows, {"base": 1000.0, "factor": 2.0, "cap": 1e9},
                    start_balance=100.0) is None


def test_simulate_amplify_scales_moves():
    rows = _rows([100.0, 99.0])
    plain = simulate(rows, CHAMPION)
    stressed = simulate(rows, CHAMPION, amplify=1.25)
    assert stressed["final"] < plain["final"]


# ---------------------------------------------------------------- harness
def _years_rows():
    # Three years of gentle drift upward, daily rows.
    rows = []
    price = 100.0
    day = dt.date(1990, 1, 1)
    for i in range(3 * 365):
        price *= 1.0003
        rows.append((day + dt.timedelta(days=i), price))
    return rows


def test_score_multi_start_and_worst():
    rows = _years_rows()
    m = score(rows, CHAMPION, start_years=(1990, 1991, 1992))
    assert m is not None
    assert m["worst_cagr"] <= m["best_cagr"]


def test_experiment_ranks_and_logs(tmp_path):
    rows = _years_rows()
    results = str(tmp_path / "results.tsv")
    ranked = experiment("t", [CHAMPION, {**CHAMPION, "base": 0.1}], rows,
                        results_file=results, start_years=(1990, 1991))
    assert len(ranked) == 2
    assert ranked[0][0]["worst_cagr"] >= ranked[1][0]["worst_cagr"]
    lines = open(results).read().strip().splitlines()
    assert len(lines) == 3   # header + 2 rows


# ---------------------------------------------------------------- promote
def test_champion_cfg_mapping():
    assert champion_cfg({"base_pct": 0.3, "base_notional": 0.0,
                         "factor": 3.0, "max_notional": 160000.0}) == CHAMPION
    fixed = champion_cfg({"base_pct": 0.0, "base_notional": 5000.0,
                          "factor": 2.0, "max_notional": 160000.0})
    assert fixed == {"base": 5000.0, "pct": False, "factor": 2.0,
                     "cap": 160000.0}


def test_promote_writes_live_usable_entry(tmp_path):
    champions = tmp_path / "champions.json"
    champions.write_text("{}", encoding="utf-8")
    metrics = {"worst_cagr": 0.0884, "max_dd_full": 0.363}
    stressed = {"worst_cagr": 0.0948}
    entry = promote("testchamp", CHAMPION, metrics, stressed,
                    champions_file=str(champions),
                    today=dt.date(2026, 8, 19))
    saved = json.loads(champions.read_text(encoding="utf-8"))["testchamp"]
    assert saved == entry
    assert saved["base_pct"] == 0.3
    assert saved["factor"] == 3.0
    assert "8.84%" in saved["description"]
    # The entry round-trips through the trader's champion translation.
    assert champion_cfg(saved) == CHAMPION


def test_promote_rejects_research_only_knobs(tmp_path):
    champions = tmp_path / "champions.json"
    champions.write_text("{}", encoding="utf-8")
    m, s = {"worst_cagr": 0.1, "max_dd_full": 0.1}, {"worst_cagr": 0.1}
    with pytest.raises(ValueError):
        promote("x", {**CHAMPION, "enter_after": 2}, m, s,
                champions_file=str(champions))
    with pytest.raises(ValueError):
        promote("x", {**CHAMPION, "reset": "half"}, m, s,
                champions_file=str(champions))
    with pytest.raises(ValueError):
        promote("x", CHAMPION, None, s, champions_file=str(champions))


# ------------------------------------------------------- config presets
def test_trader_reads_champion_presets(monkeypatch):
    from martingale_trader.config import MartingaleConfig

    for var in ("MARTINGALE_CHAMPION", "MARTINGALE_BASE_PCT",
                "MARTINGALE_FACTOR", "MARTINGALE_MAX_NOTIONAL",
                "MARTINGALE_BASE_NOTIONAL"):
        monkeypatch.delenv(var, raising=False)
    cfg = MartingaleConfig.from_env()
    assert cfg.martingale_champion == "tripledip"
    assert cfg.martingale_base_pct == 0.30
    assert cfg.martingale_factor == 3.0

    monkeypatch.setenv("MARTINGALE_CHAMPION", "classic")
    cfg = MartingaleConfig.from_env()
    assert cfg.martingale_base_pct == 0.0
    assert cfg.martingale_factor == 2.0

    # Explicit env vars override the preset.
    monkeypatch.setenv("MARTINGALE_FACTOR", "2.5")
    assert MartingaleConfig.from_env().martingale_factor == 2.5

    from earnings_notifier.config import ConfigError

    monkeypatch.setenv("MARTINGALE_CHAMPION", "nope")
    with pytest.raises(ConfigError):
        MartingaleConfig.from_env()
