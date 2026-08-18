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

# Mirrors the real export: lowercase headers, trailing comma, money-market
# variants, options, CUSIPs, crypto pair, wrapper row, pending activity.
CSV = """\
Account number,Account name,Symbol,Description,Quantity,Last price,Last price change,Current value,Today's gain/loss dollar,Today's gain/loss percent,Total gain/loss dollar,Total gain/loss percent,Percent of account,Cost basis total,Average cost basis,Type
X1,Primary,FZFXX**,HELD IN MONEY MARKET,,,,$29643.76,,,,,9.30%,,,Cash,
X1,Primary, -SNDK270917C1500,"SNDK SEP 17 2027 $1,500 CALL",1,$788.00,+$117.70,$78800.00,+$11770.00,+17.55%,+$9209.34,+13.23%,24.71%,$69590.66,$695.91,Margin,
X1,Primary,MSFT,MICROSOFT CORP,516.6465,$479.63,-$15.77,$247799.16,-$8147.52,-3.19%,+$47774.67,+23.88%,77.70%,$200024.49,$387.16,Margin,
Z1,Kid,SPAXX**,HELD IN MONEY MARKET,,,,$1578.91,,,,,5.38%,,,Cash,
Z1,Kid,NVDA,NVIDIA CORPORATION COM,3,$227.065,+$1.905,$681.19,+$5.71,+0.84%,+$321.97,+89.63%,2.32%,$359.22,$119.74,Cash,
Z2,NoCost,PLTR,PALANTIR TECHNOLOGIES INC CL A,58,$173.84,-$0.20,$10082.72,-$11.60,-0.12%,--,--,41.98%,--,--,Margin,
C1,Fidelity Crypto®,USD***,US DOLLARS,,,,$0.00,,,,,0.00%,,,Cash,
C1,Fidelity Crypto®,BTC/USD,BITCOIN,0.00087990,$64046.20,+$681.70,$56.35,+$0.59,+1.05%,-$43.65,-43.65%,100.00%,$100.00,$113649.28,Cash,
R1,ROTH IRA,Pending activity,,,,,-$26186.57,,,,,,,,,
K1,401K,59515R401,VANG 500 INDEX TRUST,418.259,$113.68,-$0.18,$47547.67,$0.00,0.00%,+$5238.66,+12.38%,41.30%,$42309.01,$101.16,,
K1,401K,,BROKERAGELINK,478885.81,$1.00,$0.00,$478885.81,$0.00,0.00%,$0.00,0.00%,--,$478885.81,$1.00,,
B1,BrokerageLink,95763PK59,WESTERN ALLIANCE BK PHOENIX CD 4.20000% 02/17/2028,10000,$99.905,-$0.1663,$9990.50,$0.00,0.00%,-$9.50,-0.10%,2.09%,$10000.00,--,Cash,
X2,Checking,CORE**,FDIC-INSURED DEPOSIT SWEEP,,,,$0.37,,,,,100.00%,,,Cash,

"Brokerage services are provided by Fidelity Brokerage Services LLC"
"""


def test_convert_real_format(tmp_path):
    csv_path = tmp_path / "positions.csv"
    csv_path.write_text(CSV, encoding="utf-8")
    document = fidelity.convert(str(csv_path))
    skipped = document.pop("_skipped")

    tickers = {p["ticker"]: p for p in document["positions"]}
    assert set(tickers) == {"MSFT", "NVDA", "PLTR", "BTC-USD"}
    assert tickers["MSFT"]["quantity"] == 516.6465
    assert tickers["MSFT"]["cost_basis"] == 387.16
    assert "cost_basis" not in tickers["PLTR"]          # "--" cost
    assert tickers["BTC-USD"]["quantity"] == 0.0008799  # crypto pair mapped

    # Cash: FZFXX + SPAXX + USD + CORE + pending activity (negative).
    assert document["cash"] == round(
        29643.76 + 1578.91 + 0.0 + 0.37 - 26186.57, 2
    )

    options = document.get("options", [])
    assert len(options) == 1
    opt = options[0]
    assert opt["underlying"] == "SNDK"
    assert opt["expiry"] == "2027-09-17"
    assert opt["option_type"] == "call"
    assert opt["strike"] == 1500.0
    assert opt["quantity"] == 1
    assert opt["cost_basis"] == 695.91

    joined = "\n".join(skipped)
    assert "SNDK" not in joined                         # parsed, not skipped
    assert "VANG 500 INDEX TRUST" in joined             # CUSIP excluded
    assert "WESTERN ALLIANCE" in joined                 # CD excluded
    assert "BROKERAGELINK" not in joined                # wrapper silently dropped
    assert len(document["positions"]) == 4


def test_convert_output_loads_in_service(tmp_path):
    from portfolio_insights.portfolio import parse_portfolio

    csv_path = tmp_path / "positions.csv"
    csv_path.write_text(CSV, encoding="utf-8")
    document = fidelity.convert(str(csv_path))
    document.pop("_skipped")
    snap = parse_portfolio(json.loads(json.dumps(document)))
    assert snap.fetch_error is None
    assert len(snap.positions) == 4
    assert snap.cash == document["cash"]
