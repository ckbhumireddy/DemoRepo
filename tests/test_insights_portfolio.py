import json

from portfolio_insights.portfolio import load_portfolio, parse_portfolio


def test_parse_typical_document():
    snap = parse_portfolio({
        "cash": 1200.5,
        "positions": [
            {"ticker": "tgt", "quantity": 100, "cost_basis": 95.5},
            {"ticker": "SNDK", "quantity": 20, "cost_basis": 41.0},
        ],
    }, source="env")
    assert snap.cash == 1200.5
    assert [p.ticker for p in snap.positions] == ["SNDK", "TGT"]
    assert snap.positions[1].quantity == 100
    assert snap.positions[1].cost_basis == 95.5
    assert snap.fetch_error is None


def test_duplicate_tickers_merged_weighted():
    snap = parse_portfolio({"positions": [
        {"ticker": "TGT", "quantity": 100, "cost_basis": 90.0},
        {"ticker": "TGT", "quantity": 100, "cost_basis": 110.0},
    ]})
    assert len(snap.positions) == 1
    assert snap.positions[0].quantity == 200
    assert snap.positions[0].cost_basis == 100.0


def test_missing_cost_basis_allowed():
    snap = parse_portfolio({"positions": [{"ticker": "TGT", "quantity": 10}]})
    assert snap.positions[0].cost_basis is None


def test_malformed_rows_skipped():
    snap = parse_portfolio({"positions": [
        {"ticker": "", "quantity": 10},
        {"ticker": "OK", "quantity": 0},
        {"quantity": 5},
        "not a dict",
        {"ticker": "GOOD", "quantity": 5},
    ]})
    assert [p.ticker for p in snap.positions] == ["GOOD"]


def test_non_object_document_is_error():
    assert parse_portfolio([1, 2]).fetch_error is not None


def test_load_env_wins_over_file(tmp_path):
    path = tmp_path / "portfolio.json"
    path.write_text(json.dumps({"positions": [{"ticker": "FILE", "quantity": 1}]}),
                    encoding="utf-8")
    snap = load_portfolio(
        json.dumps({"positions": [{"ticker": "ENV", "quantity": 1}]}), str(path)
    )
    assert [p.ticker for p in snap.positions] == ["ENV"]
    assert snap.source == "env"


def test_load_from_file(tmp_path):
    path = tmp_path / "portfolio.json"
    path.write_text(json.dumps({"positions": [{"ticker": "FILE", "quantity": 1}]}),
                    encoding="utf-8")
    snap = load_portfolio("", str(path))
    assert [p.ticker for p in snap.positions] == ["FILE"]
    assert snap.source.startswith("file:")


def test_load_missing_source_is_error(tmp_path):
    snap = load_portfolio("", str(tmp_path / "nope.json"))
    assert "no portfolio source" in snap.fetch_error


def test_load_invalid_json_is_error():
    assert "invalid" in load_portfolio("{broken", "").fetch_error
