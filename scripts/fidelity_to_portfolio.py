"""Convert a Fidelity positions CSV export into portfolio.json.

Usage:
    python scripts/fidelity_to_portfolio.py Portfolio_Positions_Aug-17-2026.csv
    python scripts/fidelity_to_portfolio.py positions.csv --output portfolio.json

Fidelity: Accounts & Trade -> Positions -> the download icon exports the
CSV this script reads. Rows it handles:
- Equities/ETFs -> positions with quantity and average cost basis.
- SPAXX / core money market / "CASH" rows -> summed into "cash".
- Option rows (symbols like "-SNDK260917C1500") -> listed on stdout as
  excluded; the insights service analyzes equities only.
- Disclaimer/footer lines -> ignored.

The output JSON is what the insights service reads (PORTFOLIO_JSON secret
or a local portfolio.json). Nothing is uploaded anywhere by this script.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from typing import Optional

CASH_SYMBOLS = {"SPAXX", "SPAXX**", "FDRXX", "FDRXX**", "CASH", "FCASH",
                "PENDING ACTIVITY", "PENDING_ACTIVITY"}


def _clean_float(raw: str) -> Optional[float]:
    if raw is None:
        return None
    text = str(raw).strip().replace("$", "").replace(",", "")
    if text in ("", "--", "n/a", "N/A"):
        return None
    negative = text.startswith("(") and text.endswith(")")
    if negative:
        text = text[1:-1]
    try:
        value = float(text)
    except ValueError:
        return None
    return -value if negative else value


def convert(csv_path: str) -> dict:
    positions = []
    skipped_options = []
    cash = 0.0
    cash_seen = False

    with open(csv_path, "r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            if not row:
                continue
            symbol = (row.get("Symbol") or "").strip()
            if not symbol:
                continue
            upper = symbol.upper().rstrip("*") + ("**" if symbol.endswith("**") else "")
            quantity = _clean_float(row.get("Quantity"))

            if symbol.upper().rstrip("*") in {s.rstrip("*") for s in CASH_SYMBOLS}:
                value = _clean_float(row.get("Current Value"))
                if value is not None:
                    cash += value
                    cash_seen = True
                continue
            if symbol.startswith("-") or " " in symbol:
                # Option or non-standard instrument — excluded from analysis.
                desc = (row.get("Description") or symbol).strip()
                skipped_options.append(f"{desc} ({symbol}) x{quantity or 0:g}")
                continue
            if quantity is None or quantity == 0:
                continue
            cost = _clean_float(row.get("Average Cost Basis"))
            entry = {"ticker": symbol.upper(), "quantity": quantity}
            if cost is not None:
                entry["cost_basis"] = cost
            positions.append(entry)

    document = {"positions": positions}
    if cash_seen:
        document["cash"] = round(cash, 2)
    document["_skipped_options"] = skipped_options  # informational only
    return document


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("csv_file", help="Fidelity positions CSV export")
    p.add_argument("--output", default="portfolio.json",
                   help="where to write the JSON (default portfolio.json)")
    args = p.parse_args()

    document = convert(args.csv_file)
    skipped = document.pop("_skipped_options")
    with open(args.output, "w", encoding="utf-8") as fh:
        json.dump(document, fh, indent=2)

    print(f"Wrote {len(document['positions'])} position(s) to {args.output}")
    if "cash" in document:
        print(f"Cash: ${document['cash']:,.2f}")
    if skipped:
        print(f"Excluded {len(skipped)} option/other row(s):")
        for s in skipped:
            print(f"  {s}")
    print(
        "\nFor GitHub Actions, store the file's content as the "
        "PORTFOLIO_JSON secret:\n"
        f"  gh secret set PORTFOLIO_JSON < {args.output}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
