import importlib.util
import json
import pathlib

_spec = importlib.util.spec_from_file_location(
    "fidelity_to_portfolio",
    pathlib.Path(__file__).resolve().parent.parent
    / "scripts" / "fidelity_to_portfolio.py",
)
fidelity = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fidelity)

CSV = """\
Account Number,Account Name,Symbol,Description,Quantity,Last Price,Current Value,Total Gain/Loss Dollar,Average Cost Basis,Type
X123,Brokerage,TGT,TARGET CORP,100,$156.13,"$15,613.00","$5,613.00",$100.00,Cash
X123,Brokerage,SNDK,SANDISK CORP,20,$259.20,"$5,184.00",$820.00,$41.00,Cash
X123,Brokerage, -SNDK260917C1500,SNDK SEP 17 2027 $1500 CALL,1,$692.00,"$69,200.00",--,$695.91,Cash
X123,Brokerage,SPAXX**,FIDELITY GOVERNMENT MONEY MARKET,--,--,"$1,234.56",--,--,Cash
X123,Brokerage,Pending Activity,,--,--,$0.00,--,--,--

"Brokerage services are provided by Fidelity Brokerage Services LLC"
"""


def test_convert(tmp_path):
    csv_path = tmp_path / "positions.csv"
    csv_path.write_text(CSV, encoding="utf-8")
    document = fidelity.convert(str(csv_path))
    skipped = document.pop("_skipped_options")

    tickers = {p["ticker"]: p for p in document["positions"]}
    assert set(tickers) == {"TGT", "SNDK"}
    assert tickers["TGT"]["quantity"] == 100
    assert tickers["TGT"]["cost_basis"] == 100.0
    assert document["cash"] == 1234.56
    assert len(skipped) == 1 and "1500 CALL" in skipped[0]


def test_convert_output_loads_in_service(tmp_path):
    from portfolio_insights.portfolio import parse_portfolio

    csv_path = tmp_path / "positions.csv"
    csv_path.write_text(CSV, encoding="utf-8")
    document = fidelity.convert(str(csv_path))
    document.pop("_skipped_options")
    snap = parse_portfolio(json.loads(json.dumps(document)))
    assert snap.fetch_error is None
    assert len(snap.positions) == 2
    assert snap.cash == 1234.56
