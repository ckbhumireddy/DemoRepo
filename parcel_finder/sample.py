"""Synthetic county download for demos and tests (no real owners).

Writes a zip shaped like the county's (master, receivable and tax-unit files
encoded with the active layout, by canonical field name), so the whole
pipeline (unzip -> parse -> build -> browse) runs without the real data.
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
         ("S12", "SANGER ISD"), ("C05", "TOWN OF PILOT POINT"), ("S14", "PILOT POINT ISD"),
         ("C20", "CITY OF THE COLONY")]
CITIES = [("DENTON", "C08", "S07"), ("LEWISVILLE", "C11", "S09"), ("SANGER", "C19", "S12"),
          ("PILOT POINT", "C05", "S14"), ("THE COLONY", "C20", "S09"), ("AUBREY", None, "S07"),
          ("KRUGERVILLE", None, "S07")]
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


def _raw(layout: Layout, kind: str, canon: dict) -> dict:
    """Canonical names -> this layout's raw column names."""
    mapping = layout.canonical.get(kind, {})
    return {mapping[k]: v for k, v in canon.items() if k in mapping}


def generate(layout: Layout, out_zip: str | pathlib.Path, *, n: int = 2000, seed: int = 7,
             as_of: dt.date = dt.date(2026, 9, 22)) -> pathlib.Path:
    rng = random.Random(seed)
    masters, recvs = [], []
    cur = as_of.year  # current-year bills exist but are not yet delinquent
    vacant_codes = tuple(layout.extras.get("vacant_codes") or ("C1", "D1", "E"))
    real_roll = (layout.extras.get("real_property_rolls") or ["001"])[0]
    other_rolls = [c for c in (layout.extras.get("roll_codes") or {}) if c != real_roll][:3]
    for i in range(n):
        acct = f"{rng.randint(10000, 99999)}{i:06d}"
        code = _pick_code(rng)
        vacant = code in ("C1", "D1", "E") and rng.random() < 0.85
        code = rng.choice(vacant_codes) if vacant else code
        city, city_unit, isd = rng.choice(CITIES)
        acres = round(rng.uniform(0.12, 0.5) if not vacant or rng.random() < 0.5 else rng.uniform(2, 160), 4)
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
        homestead = (not vacant) and (not out_state) and rng.random() < 0.75
        deed = as_of - dt.timedelta(days=rng.randint(100, 365 * 40))
        deferral = homestead and rng.random() < 0.03
        masters.append(_raw(layout, "master", {
            "account": acct, "cad_id": f"R{rng.randint(100000, 999999)}", "owner_name": owner,
            "owner_name2": f"{rng.choice(FIRST)} {owner.split()[0]}" if rng.random() < 0.25 else "",
            "mail_addr1": mail[0], "mail_addr2": "", "mail_city": mail[1], "mail_state": mail[2], "mail_zip": mail[3],
            "situs_num": "" if vacant and acres > 1 else str(rng.randint(100, 9999)),
            "situs_street": rng.choice(STREETS), "situs_city": city,
            "legal_desc": f"{rng.choice(['A0', 'A1'])}{rng.randint(100, 999)}A {rng.choice(LAST)} SURVEY, TR {rng.randint(1, 60)}",
            "state_code": code, "roll_code": real_roll if rng.random() < 0.8 or not other_rolls else rng.choice(other_rolls),
            "acreage": acres, "land_value": land, "impr_value": impr,
            "market_value": land + impr, "exemptions": "1-2-3" if homestead else "",
            "deed_date": deed.isoformat(), "year_built": 0 if vacant else rng.randint(1950, 2024),
            "deferral_start": (as_of - dt.timedelta(days=900)).isoformat() if deferral else None,
        }))
        units = [u for u in ("G01", city_unit, isd) if u]
        p_delinq = 0.04 + 0.12 * vacant + 0.10 * out_state + 0.15 * ("EST" in owner or "HEIR" in owner or "DECD" in owner)
        behind = min(8, int(rng.expovariate(0.55)) + 1) if rng.random() < p_delinq else 0
        suit = behind >= 3 and rng.random() < 0.6
        for year in range(cur - behind, cur + 1):
            for u in units:
                rate = {"G": 0.0022, "C": 0.0055, "S": 0.011}[u[0]]
                levy = round((land + impr) * rate * (0.9 + 0.02 * (year - cur + 10)), 2)
                unpaid = year < cur or rng.random() < 0.4
                base = levy if unpaid else 0.0
                pen = round(base * min(0.47, 0.12 + 0.12 * (cur - year - 1)), 2) if year < cur else 0.0
                recvs.append(_raw(layout, "receivable", {
                    "account": acct, "tax_year": year, "unit_code": u, "recv_type": "001", "sequence": "01",
                    "levy": levy, "base_due": base, "total_due": round(base + pen, 2),
                    "delinquent_date": dt.date(year + 1, 2, 1).isoformat(),
                    "suit_flag": "Y" if suit and year < cur else "",
                    "suit_date": (as_of - dt.timedelta(days=200)).isoformat() if suit and year < cur else None,
                    "judgment_date": (as_of - dt.timedelta(days=60)).isoformat() if suit and behind >= 5 and year < cur else None,
                }))

    out_zip = pathlib.Path(out_zip)
    out_zip.parent.mkdir(parents=True, exist_ok=True)
    stamp = as_of.strftime("%m%d%y")
    units = [_raw(layout, "tax_unit", {"unit_code": c, "unit_alpha": c, "unit_name": nme}) for c, nme in UNITS]
    files = {"master": masters, "receivable": recvs, "tax_unit": units}
    with zipfile.ZipFile(out_zip, "w", zipfile.ZIP_DEFLATED) as zf:
        for kind, rows in files.items():
            spec = layout.files.get(kind)
            if spec is None:
                continue
            buf = io.StringIO()
            if spec.format == "delimited" and spec.header:
                buf.write(spec.delimiter.join(spec.names) + "\r\n")
            for r in rows:
                buf.write(spec.format_record(r) + "\r\n")
            zf.writestr(f"{spec.prefixes[0]}{stamp}.DAT", buf.getvalue().encode(spec.encoding, errors="replace"))
    return out_zip
