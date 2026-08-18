"""Convert a Fidelity positions CSV export into portfolio.json.

Usage:
    python scripts/fidelity_to_portfolio.py Portfolio_Positions_Aug-17-2026.csv
    python scripts/fidelity_to_portfolio.py positions.csv --output portfolio.json

Fidelity: Accounts & Trade -> Positions -> the download icon exports the
CSV this script reads. Row handling:
- Equities / ETFs / mutual funds / crypto pairs (BTC/USD -> BTC-USD) ->
  positions with quantity and average cost basis.
- Money-market rows (SPAXX, FZFXX, FDRXX, CORE, USD balances) and
  "Pending activity" -> summed into "cash".
- Option rows (symbols like "-SNDK270917C1500") -> listed as excluded;
  the insights service analyzes equities only.
- CUSIP-numbered rows (401K pooled funds, CDs, defunct securities) ->
  excluded: no public quote symbol exists for them.
- Empty-symbol wrapper rows (e.g. 401K BROKERAGELINK, which duplicates an
  itemized account) and footer/disclaimer lines -> ignored.

The output JSON is what the insights service reads (PORTFOLIO_JSON secret
or a local portfolio.json). Nothing is uploaded anywhere by this script.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import re
import sys
from typing import Optional

CASH_SYMBOLS = {"SPAXX", "FDRXX", "FZFXX", "FCASH", "CORE", "USD", "CASH"}
CASH_DESCRIPTIONS = ("MONEY MARKET", "DEPOSIT SWEEP", "US DOLLARS")

# Fidelity option symbols: -SNDK270917C1500 = SNDK, 2027-09-17, call, $1500.
_OPTION_RE = re.compile(r"^-?([A-Z]+)(\d{6})([CP])([\d.]+)$")


def parse_option_symbol(symbol: str) -> Optional[dict]:
    m = _OPTION_RE.match(symbol.strip().lstrip("-"))
    if not m:
        return None
    underlying, yymmdd, right, strike = m.groups()
    try:
        expiry = dt.datetime.strptime(yymmdd, "%y%m%d").date()
        strike_f = float(strike)
    except ValueError:
        return None
    return {
        "underlying": underlying,
        "expiry": expiry.isoformat(),
        "option_type": "call" if right == "C" else "put",
        "strike": strike_f,
    }


def _clean_float(raw) -> Optional[float]:
    if raw is None:
        return None
    text = str(raw).strip().replace("$", "").replace(",", "").replace("+", "")
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


def _norm_row(row: dict) -> dict:
    """Header names vary in casing across Fidelity exports — normalize."""
    return {
        str(k).strip().lower(): v
        for k, v in row.items()
        if k is not None
    }


def convert(csv_path: str) -> dict:
    positions = []
    options = []
    skipped = []
    cash = 0.0
    cash_seen = False

    with open(csv_path, "r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        for raw_row in reader:
            row = _norm_row(raw_row or {})
            symbol = str(row.get("symbol") or "").strip()
            if not symbol:
                continue  # wrapper rows (BROKERAGELINK) and blank lines
            desc = str(row.get("description") or "").strip()
            quantity = _clean_float(row.get("quantity"))
            value = _clean_float(row.get("current value"))

            base = symbol.rstrip("*").upper()
            is_cash = (
                base in CASH_SYMBOLS
                or base == "PENDING ACTIVITY"
                or "PENDING ACTIVITY" in base
                or any(marker in desc.upper() for marker in CASH_DESCRIPTIONS)
            )
            if is_cash:
                if value is not None:
                    cash += value
                    cash_seen = True
                continue
            if symbol.startswith("-"):
                parsed = parse_option_symbol(symbol)
                if parsed is None or quantity is None or quantity == 0:
                    skipped.append(f"option (unparsed): {desc or symbol}")
                    continue
                cost = _clean_float(row.get("average cost basis"))
                entry = {**parsed, "quantity": quantity}
                if cost is not None:
                    entry["cost_basis"] = cost
                options.append(entry)
                continue
            if any(ch.isdigit() for ch in base):
                skipped.append(
                    f"no public quote symbol (CUSIP): {desc or symbol}"
                    + (f" (${value:,.2f})" if value is not None else "")
                )
                continue
            if quantity is None or quantity == 0:
                continue
            ticker = base.replace("/", "-")   # BTC/USD -> BTC-USD (Yahoo)
            cost = _clean_float(row.get("average cost basis"))
            entry = {"ticker": ticker, "quantity": quantity}
            if cost is not None:
                entry["cost_basis"] = cost
            positions.append(entry)

    document = {"positions": positions}
    if options:
        document["options"] = options
    if cash_seen:
        document["cash"] = round(cash, 2)
    document["_skipped"] = skipped  # informational only
    return document


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("csv_file", help="Fidelity positions CSV export")
    p.add_argument("--output", default="portfolio.json",
                   help="where to write the JSON (default portfolio.json)")
    args = p.parse_args()

    document = convert(args.csv_file)
    skipped = document.pop("_skipped")
    with open(args.output, "w", encoding="utf-8") as fh:
        json.dump(document, fh, indent=2)

    print(f"Wrote {len(document['positions'])} position row(s) to {args.output}")
    if "options" in document:
        print(f"Options: {len(document['options'])} position(s) parsed")
    if "cash" in document:
        print(f"Cash (money market + pending): ${document['cash']:,.2f}")
    if skipped:
        print(f"Excluded {len(skipped)} row(s):")
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
