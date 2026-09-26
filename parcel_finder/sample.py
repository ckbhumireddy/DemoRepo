"""Synthetic county download for demos and tests (no real owners).

Writes a zip shaped like the county's: MM/MR/MS/TU files encoded with the
active layout, so the whole pipeline (unzip -> parse -> build -> browse) runs
without the real data.
"""

from __future__ import annotations

import datetime as dt
import io
import pathlib
import random
import zipfile

from .layout import Layout

UNITS = [("G01", "DENTON COUNTY"), ("C08", "CITY OF DENTON"), ("S07", "DENTON ISD"),
         ("C11", "CITY OF LEWISVILLE"), ("S09", "LEWISVILLE ISD"), ("C19", "CITY OF SANGER"),
         ("S12", "SANGER ISD"), ("C05", "TOWN OF PILOT POINT"), ("S14", "PILOT POINT ISD")]
CITIES = [("DENTON", "C08", "S07"), ("LEWISVILLE", "C11", "S09"), ("SANGER", "C19", "S12"),
          ("PILOT POINT", "C05", "S14"), ("AUBREY", None, "S07"), ("KRUGERVILLE", None, "S07")]
FIRST = ["JOHN", "MARY", "ROBERT", "LINDA", "JAMES", "PATRICIA", "DAVID", "BARBARA", "CARLOS", "MARIA",
         "WILLIAM", "SUSAN", "THOMAS", "KAREN", "JOSE", "NANCY", "DANIEL", "BETTY"]
LAST = ["SMITH", "JOHNSON", "WILLIAMS", "BROWN", "JONES", "GARCIA", "MILLER", "DAVIS", "MARTINEZ",
        "WILSON", "ANDERSON", "TAYLOR", "THOMAS", "MOORE", "JACKSON", "WHITE", "HARRIS", "CLARK"]
STREETS = ["MAIN ST", "FM 428", "FM 455", "OAK ST", "HICKORY ST", "LOOP 288", "MILAM RD",
           "BOLIVAR ST", "US HWY 377", "JIM CHRISTAL RD", "BARTHOLD RD", "ROCKHILL RD"]
OTHER_STATES = [("OK", "OKLAHOMA CITY"), ("CA", "FRESNO"), ("FL", "TAMPA"), ("CO", "DENVER"),
                ("AZ", "PHOENIX"), ("NM", "ALBUQUERQUE"), ("LA", "SHREVEPORT"), ("IL", "CHICAGO")]
CODES = [("A1", 0.62), ("C1", 0.12), ("D1", 0.08), ("E1", 0.06), ("E", 0.04), ("F1", 0.05), ("B1", 0.03)]
ESTATE_SUFFIX = ["ESTATE", "EST OF", "HEIRS", "ET AL", "DECD", "LIFE ESTATE"]


def _pick_code(rng):
    x, acc = rng.random(), 0.0
    for code, w in CODES:
        acc += w
        if x < acc:
            return code
    return "A1"


def generate(layout: Layout, out_zip: str | pathlib.Path, *, n: int = 2000, seed: int = 7,
             as_of: dt.date = dt.date(2026, 9, 22)) -> pathlib.Path:
    rng = random.Random(seed)
    masters, recvs, stats = [], [], []
    cur = as_of.year  # current-year bills exist but are not yet delinquent
    for i in range(n):
        acct = f"{rng.randint(10000, 99999)}{i:06d}"
        code = _pick_code(rng)
        city, city_unit, isd = rng.choice(CITIES)
        vacant = code in ("C1", "D1", "E") and rng.random() < 0.85
        acres = round(rng.uniform(0.12, 0.5) if code.startswith(("A", "C", "B", "F")) else rng.uniform(2, 160), 4)
        land = int(acres * rng.uniform(15000, 90000)) if acres < 1 else int(acres * rng.uniform(4000, 14000))
        impr = 0 if vacant else rng.randint(90, 520) * 1000
        owner = f"{rng.choice(LAST)} {rng.choice(FIRST)}"
        if rng.random() < 0.06:
            owner += " " + rng.choice(ESTATE_SUFFIX)
        out_state = rng.random() < (0.28 if vacant else 0.07)
        if out_state:
            st, mcity = rng.choice(OTHER_STATES)
            mail = (f"{rng.randint(100, 9999)} {rng.choice(['ELM', 'PINE', 'CEDAR'])} AVE", mcity, st,
                    f"{rng.randint(10000, 99999)}")
        else:
            mail = (f"{rng.randint(100, 9999)} {rng.choice(STREETS)}", city, "TX", f"76{rng.randint(200, 299)}")
        homestead = (not vacant) and (not out_state) and code.startswith(("A", "E1")) and rng.random() < 0.75
        deed = as_of - dt.timedelta(days=rng.randint(100, 365 * 40))
        masters.append({
            "ACCOUNT": acct, "CAD_ID": f"R{rng.randint(100000, 999999)}", "OWNER_NAME": owner,
            "OWNER_NAME2": f"{rng.choice(FIRST)} {owner.split()[0]}" if rng.random() < 0.25 else "",
            "MAIL_ADDR1": mail[0], "MAIL_ADDR2": "", "MAIL_CITY": mail[1], "MAIL_STATE": mail[2], "MAIL_ZIP": mail[3],
            "SITUS_NUM": "" if code in ("D1", "E") else str(rng.randint(100, 9999)),
            "SITUS_STREET": rng.choice(STREETS), "SITUS_CITY": city,
            "LEGAL_DESC": f"{rng.choice(['A0', 'A1'])}{rng.randint(100, 999)}A {rng.choice(LAST)} SURVEY, TR {rng.randint(1, 60)}",
            "STATE_CODE": code, "ACREAGE": acres, "LAND_VALUE": land, "IMPR_VALUE": impr,
            "MARKET_VALUE": land + impr, "EXEMPTIONS": "HS" if homestead else "",
            "DEED_DATE": deed.isoformat(),
        })
        units = [u for u in ("G01", city_unit, isd) if u]
        p_delinq = 0.04 + 0.12 * vacant + 0.10 * out_state + 0.15 * ("EST" in owner or "HEIR" in owner or "DECD" in owner)
        behind = 0
        if rng.random() < p_delinq:
            behind = min(8, int(rng.expovariate(0.55)) + 1)
        for year in range(cur - max(behind, 0), cur + 1):
            for u in units:
                rate = {"G": 0.0022, "C": 0.0055, "S": 0.011}[u[0]]
                levy = round((land + impr) * rate * (0.9 + 0.02 * (year - cur + 10)), 2)
                unpaid = year == cur and rng.random() < 0.4 or year < cur
                base = levy if unpaid else 0.0
                pen = round(base * min(0.47, 0.12 + 0.12 * (cur - year - 1)), 2) if year < cur else 0.0
                recvs.append({"ACCOUNT": acct, "TAX_YEAR": year, "UNIT_CODE": u, "LEVY": levy,
                              "BASE_DUE": base, "PEN_INT_DUE": pen, "TOTAL_DUE": round(base + pen, 2),
                              "SUIT_FLAG": "Y" if behind >= 3 and year < cur else ""})
                stats.append({"ACCOUNT": acct, "TAX_YEAR": year, "UNIT_CODE": u,
                              "MARKET_VALUE": land + impr, "TAXABLE_VALUE": land + impr})

    out_zip = pathlib.Path(out_zip)
    out_zip.parent.mkdir(parents=True, exist_ok=True)
    stamp = as_of.strftime("%Y%m%d")
    files = {"master": (f"MM{stamp}.TXT", masters), "receivable": (f"MR{stamp}.TXT", recvs),
             "statistic": (f"MS{stamp}.TXT", stats),
             "tax_unit": (f"TU{stamp}.TXT", [{"UNIT_CODE": c, "UNIT_ALPHA": c, "UNIT_NAME": nme} for c, nme in UNITS])}
    with zipfile.ZipFile(out_zip, "w", zipfile.ZIP_DEFLATED) as zf:
        for kind, (name, rows) in files.items():
            spec = layout.files.get(kind)
            if spec is None:
                continue
            buf = io.StringIO()
            for r in rows:
                buf.write(spec.format_record(r) + "\r\n")
            zf.writestr(name, buf.getvalue().encode(spec.encoding, errors="replace"))
    return out_zip
