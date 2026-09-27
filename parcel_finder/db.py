"""Local SQLite database: raw county files in, scored ``parcels`` table out.

Two stages so the slow part runs once:

* ``ingest``  - unzip the county download(s), classify each file by prefix
                (MM/AM master, MR/AR receivable, MS/AS statistic, TU units)
                and load it verbatim into ``raw_<kind>`` tables.
* ``build``   - map raw columns to canonical names (per the layout), roll the
                receivables up per account, join GIS enrichment, and score.
                Re-run it any time with different weights; no re-ingest.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import json
import logging
import pathlib
import re
import sqlite3
import zipfile
from typing import Dict, Iterable, Iterator, List, Optional, Tuple

from . import geo
from .layout import FileSpec, Layout
from .scoring import VACANT_CODES, Targets, Weights, compute_signals, delinquent_through_year, score

log = logging.getLogger(__name__)
BATCH = 5000

PARCEL_COLUMNS = [
    "account", "cad_id", "owner_name", "owner_name2",
    "mail_addr1", "mail_addr2", "mail_city", "mail_state", "mail_zip",
    "situs_address", "situs_city", "legal_desc", "state_code", "roll_code", "roll", "cad", "cad_url",
    "acreage", "land_value", "impr_value", "market_value", "exemptions", "deed_date", "year_built",
    "years_delinquent", "first_delinquent_year", "last_delinquent_year",
    "delinquent_due", "total_due", "in_suit", "in_judgment", "in_bankruptcy", "in_deferral",
    "latitude", "longitude", "fema_zone", "flood_zone", "flood_partial", "road_access",
    "is_real_property", "is_target_acreage", "is_under_price",
    "is_delinquent", "is_out_of_state", "is_estate", "is_long_held", "is_vacant",
    "is_absentee", "score", "score_pct", "reasons",
]


def connect(path: str | pathlib.Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


# --------------------------------------------------------------------- ingest

def iter_source_files(paths: Iterable[str | pathlib.Path]) -> Iterator[Tuple[str, bytes]]:
    """Yield (name, bytes) for every data file in the given zips/dirs/files."""
    for p in map(pathlib.Path, paths):
        if p.is_dir():
            for child in sorted(p.iterdir()):
                yield from iter_source_files([child])
        elif zipfile.is_zipfile(p):
            with zipfile.ZipFile(p) as zf:
                for info in zf.infolist():
                    if info.is_dir():
                        continue
                    data = zf.read(info)
                    if zipfile.is_zipfile(io.BytesIO(data)):   # zip in a zip
                        with zipfile.ZipFile(io.BytesIO(data)) as inner:
                            for n in inner.namelist():
                                yield f"{p.name}/{info.filename}/{n}", inner.read(n)
                    else:
                        yield f"{p.name}/{info.filename}", data
        elif p.is_file():
            yield str(p), p.read_bytes()


def _ensure_raw_table(conn, spec: FileSpec):
    cols = ", ".join(f'"{n}"' for n in spec.names)
    conn.execute(f'CREATE TABLE IF NOT EXISTS raw_{spec.kind} ({cols}, source_file TEXT)')


def ingest(conn: sqlite3.Connection, layout: Layout, paths: Iterable[str],
           append: bool = False) -> Dict[str, int]:
    if not append:
        for kind in layout.files:
            conn.execute(f"DROP TABLE IF EXISTS raw_{kind}")
        conn.execute("DROP TABLE IF EXISTS source_files")
    conn.execute("CREATE TABLE IF NOT EXISTS source_files (name TEXT, kind TEXT, rows INTEGER, loaded_at TEXT)")
    for spec in layout.files.values():
        _ensure_raw_table(conn, spec)

    counts: Dict[str, int] = {}
    for name, data in iter_source_files(paths):
        kind = layout.kind_for(name)
        if kind is None:
            log.warning("skipping %s: name matches no known prefix", name)
            continue
        spec = layout.files[kind]
        text = io.TextIOWrapper(io.BytesIO(data), encoding=spec.encoding, newline="")
        cols = spec.names + ["source_file"]
        sql = f'INSERT INTO raw_{kind} ({", ".join(chr(34) + c + chr(34) for c in cols)}) ' \
              f'VALUES ({", ".join("?" * len(cols))})'
        n, batch = 0, []
        for rec in spec.parse_lines(text):
            batch.append([rec[c] for c in spec.names] + [name])
            if len(batch) >= BATCH:
                conn.executemany(sql, batch)
                n += len(batch)
                batch = []
        conn.executemany(sql, batch)
        n += len(batch)
        conn.execute("INSERT INTO source_files VALUES (?, ?, ?, ?)",
                     (name, kind, n, dt.datetime.now().isoformat(timespec="seconds")))
        counts[kind] = counts.get(kind, 0) + n
        log.info("loaded %s: %s rows -> raw_%s", name, n, kind)
    conn.commit()
    return counts


# ---------------------------------------------------------------------- build

def _select_canonical(layout: Layout, kind: str) -> str:
    mapping = layout.canonical.get(kind, {})
    return ", ".join(f'"{raw}" AS {canon}' for canon, raw in mapping.items())


def _has_table(conn, name: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def _receivables(conn, layout: Layout):
    """One row per receivable. The county's two zips overlap, so identical keys
    (account/year/unit/type/sequence) are collapsed, keeping the larger balance."""
    conn.execute("DROP TABLE IF EXISTS receivables")
    rmap = layout.canonical.get("receivable", {})
    if not (_has_table(conn, "raw_receivable") and rmap):
        conn.execute("CREATE TABLE receivables (account TEXT, tax_year INT, unit_code TEXT, total_due REAL)")
        return rmap
    keys = [k for k in ("account", "tax_year", "unit_code", "recv_type", "sequence") if k in rmap]
    others = [k for k in rmap if k not in keys]
    cols = [f'"{rmap[k]}" AS {k}' for k in keys] + [f'MAX("{rmap[k]}") AS {k}' for k in others]
    conn.execute(f"CREATE TABLE receivables AS SELECT {', '.join(cols)} FROM raw_receivable "
                 f"GROUP BY {', '.join(str(i + 1) for i in range(len(keys)))}")
    conn.execute("CREATE INDEX ix_recv_account ON receivables(account)")
    return rmap


def _account_rollup(conn, rmap: Dict[str, str], as_of: dt.date) -> Dict[str, Dict]:
    """Per-account delinquency summary from the receivables.

    A receivable is delinquent once its delinquency date has passed; without
    one, once its tax year's Feb 1 deadline has (see delinquent_through_year).
    """
    through = delinquent_through_year(as_of)
    past_due = (f"(total_due > 0 AND COALESCE(delinquent_date <= :d, tax_year <= :y))"
                if "delinquent_date" in rmap else "(total_due > 0 AND tax_year <= :y)")

    def flag(col, cond):
        return f"MAX(CASE WHEN {cond} THEN 1 ELSE 0 END)" if col in rmap else "0"

    blank = "TRIM(COALESCE({c}, '')) NOT IN ('', 'N', '0')"
    suit = " OR ".join(x for x in (
        "suit_date IS NOT NULL" if "suit_date" in rmap else "",
        blank.format(c="suit_number") if "suit_number" in rmap else "",
        blank.format(c="suit_flag") if "suit_flag" in rmap else "") if x) or "0"
    sql = f"""
        SELECT account,
               COUNT(DISTINCT CASE WHEN {past_due} THEN tax_year END) AS years_delinquent,
               MIN(CASE WHEN {past_due} THEN tax_year END) AS first_delinquent_year,
               MAX(CASE WHEN {past_due} THEN tax_year END) AS last_delinquent_year,
               ROUND(SUM(CASE WHEN {past_due} THEN total_due ELSE 0 END), 2) AS delinquent_due,
               ROUND(SUM(CASE WHEN total_due > 0 THEN total_due ELSE 0 END), 2) AS total_due,
               MAX(CASE WHEN {suit} THEN 1 ELSE 0 END) AS in_suit,
               {flag("judgment_date", "judgment_date IS NOT NULL")} AS in_judgment,
               {flag("bankruptcy_date", "bankruptcy_date IS NOT NULL AND total_due > 0")} AS in_bankruptcy
        FROM receivables GROUP BY account"""
    return {r["account"]: dict(r) for r in conn.execute(sql, {"y": through, "d": as_of.isoformat()})}


def _account_cities(conn, layout: Layout) -> Dict[str, str]:
    """Situs city from the account's city taxing unit (e.g. C05 CITY OF DENTON).

    Tax-office rolls often carry no situs city, but every parcel inside a city
    is billed by it; parcels with no city unit are unincorporated.
    """
    pattern = layout.extras.get("city_unit_pattern")
    if not pattern or not _has_table(conn, "tax_units"):
        return {}
    strip = re.compile(layout.extras.get("city_name_strip", "^(CITY|TOWN) OF "), re.IGNORECASE)
    alpha = re.compile(pattern)
    names = {}
    for u in conn.execute("SELECT * FROM tax_units"):
        u = dict(u)
        if alpha.search(u.get("unit_alpha") or u.get("unit_code") or ""):
            names[u["unit_code"]] = strip.sub("", u.get("unit_name") or "").split("-")[0].strip()
    if not names:
        return {}
    out: Dict[str, str] = {}
    for acct, unit in conn.execute("SELECT account, unit_code FROM receivables WHERE unit_code IN (%s) "
                                   "ORDER BY tax_year" % ",".join("?" * len(names)), list(names)):
        out[acct] = names[unit]           # latest year wins (annexations)
    return out


def cad_link(account: Optional[str], layout: Layout) -> Tuple[Optional[str], Optional[str]]:
    """(appraisal district name, its property page URL) for a tax-office account.

    Denton's account is the CAD property ID plus a district suffix
    (963342DEN is Denton CAD property 963342); parcels straddling the county
    line carry another district's suffix (TAR, WIS, ...).
    """
    pattern = layout.extras.get("cad_account_pattern")
    m = re.match(pattern, account or "") if pattern else None
    if not m:
        return None, None
    cad = (layout.extras.get("appraisal_districts") or {}).get(m.group("cad"), {})
    url = cad.get("url")
    return cad.get("name", m.group("cad")), url.format(id=m.group("id")) if url else None


def build(conn: sqlite3.Connection, layout: Layout, *, as_of: Optional[dt.date] = None,
          weights: Weights = Weights(), long_held_years: int = 10, targets: Targets = Targets()) -> int:
    as_of = as_of or dt.date.today()
    through = delinquent_through_year(as_of)

    conn.execute("DROP TABLE IF EXISTS tax_units")
    if _has_table(conn, "raw_tax_unit") and "tax_unit" in layout.canonical:
        conn.execute(f"CREATE TABLE tax_units AS SELECT DISTINCT {_select_canonical(layout, 'tax_unit')} "
                     f"FROM raw_tax_unit")
    else:
        conn.execute("CREATE TABLE tax_units (unit_code TEXT, unit_name TEXT)")

    rmap = _receivables(conn, layout)
    rollup = _account_rollup(conn, rmap, as_of)
    cities = _account_cities(conn, layout)

    conn.execute("CREATE TABLE IF NOT EXISTS enrichment (account TEXT PRIMARY KEY, flood_zone INTEGER, "
                 "road_access INTEGER, source TEXT)")
    enrich = {r["account"]: dict(r) for r in conn.execute("SELECT * FROM enrichment")}
    geo.ensure_tables(conn)
    located = {r["account"]: dict(r) for r in conn.execute("SELECT * FROM locations WHERE latitude IS NOT NULL")}
    floods = {r["account"]: dict(r) for r in conn.execute("SELECT * FROM flood")}

    conn.execute("DROP TABLE IF EXISTS parcels")
    conn.execute(f"CREATE TABLE parcels ({', '.join(PARCEL_COLUMNS)}, PRIMARY KEY (account))")
    mmap = layout.canonical["master"]
    homestead = re.compile(layout.homestead_pattern, re.IGNORECASE)
    vacant_codes = tuple(layout.extras.get("vacant_codes") or VACANT_CODES)
    real_rolls = set(layout.extras.get("real_property_rolls") or [])
    roll_names = layout.extras.get("roll_codes") or {}
    # Last-loaded row wins when an account appears in more than one file.
    master_sql = (f"SELECT {_select_canonical(layout, 'master')} FROM raw_master "
                  f"WHERE rowid IN (SELECT MAX(rowid) FROM raw_master GROUP BY \"{mmap['account']}\")")
    rows: List[Dict] = []
    for m in conn.execute(master_sql):
        m = dict(m)
        p = {c: None for c in PARCEL_COLUMNS}
        p.update({k: v for k, v in m.items() if k in p})
        num = (m.get("situs_num") or "").lstrip("0")
        p["situs_address"] = " ".join(x for x in (num, m.get("situs_street")) if x) or m.get("situs_address")
        p["situs_city"] = m.get("situs_city") or cities.get(p["account"]) or ("UNINCORPORATED" if cities else None)
        p["roll"] = roll_names.get(p["roll_code"] or "", p["roll_code"])
        p["cad"], p["cad_url"] = cad_link(p["account"], layout)
        p["is_real_property"] = int(not real_rolls or p["roll_code"] in real_rolls)
        # Guide: a deferral start with no end date means the account is in deferral.
        p["in_deferral"] = int(bool(m.get("deferral_start")) and not m.get("deferral_end"))
        d = rollup.get(p["account"], {})
        for k in ("years_delinquent", "delinquent_due", "total_due", "in_suit", "in_judgment", "in_bankruptcy"):
            p[k] = d.get(k) or 0
        p["first_delinquent_year"] = d.get("first_delinquent_year")
        p["last_delinquent_year"] = d.get("last_delinquent_year")
        e = enrich.get(p["account"], {})
        p["flood_zone"], p["road_access"] = e.get("flood_zone"), e.get("road_access")
        loc = located.get(p["account"], {})
        p["latitude"], p["longitude"] = loc.get("latitude"), loc.get("longitude")
        f = floods.get(p["account"])
        if f:   # FEMA lookup wins over a hand-made enrichment CSV
            p["flood_zone"], p["flood_partial"] = f["in_sfha"], f["near_sfha"]
            p["fema_zone"] = geo.fema_zone_label(f["fld_zone"], f["zone_subty"])

        s = compute_signals(p, as_of=as_of, home_state=layout.state, homestead=homestead,
                            long_held_years=long_held_years, vacant_codes=vacant_codes, targets=targets)
        p["score"] = score(s, weights)
        p["reasons"] = "; ".join(s.reasons)
        p.update(is_target_acreage=int(s.target_acreage), is_under_price=int(s.under_price),
                 is_delinquent=int(s.delinquent), is_out_of_state=int(s.out_of_state),
                 is_estate=int(s.estate), is_long_held=int(s.long_held),
                 is_vacant=int(s.vacant), is_absentee=int(s.absentee))
        rows.append(p)

    _rank(rows)
    placeholders = ", ".join("?" * len(PARCEL_COLUMNS))
    conn.executemany(f"INSERT INTO parcels VALUES ({placeholders})",
                     [[r[c] for c in PARCEL_COLUMNS] for r in rows])
    for col in ("score", "mail_state", "state_code", "owner_name", "situs_city", "years_delinquent",
                "delinquent_due", "roll_code"):
        conn.execute(f"CREATE INDEX IF NOT EXISTS ix_parcels_{col} ON parcels({col})")

    ensure_favorites(conn)
    conn.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
    meta = {"county": layout.county, "state": layout.state, "as_of": as_of.isoformat(),
            "delinquent_through_year": str(through), "built_at": dt.datetime.now().isoformat(timespec="seconds"),
            "layout_notes": layout.notes, "weights": repr(weights.as_dict()),
            "targets": json.dumps(targets.__dict__),
            "receivable_types": json.dumps(layout.extras.get("receivable_types") or {})}
    conn.executemany("INSERT OR REPLACE INTO meta VALUES (?, ?)", meta.items())
    conn.commit()
    return len(rows)


def _rank(rows: List[Dict]) -> None:
    """score_pct = share of comparable parcels ranked at or below this one
    (100 = top). Ties on score break by delinquent amount, then value, so "top 25%" is
    close to 25% rather than every parcel sharing the cut-off score. Real
    property is ranked among real property only, so mineral interests and
    business personal property don't dilute the mail list."""
    groups: Dict[int, List[Dict]] = {}
    for r in rows:
        groups.setdefault(r["is_real_property"], []).append(r)
    for group in groups.values():
        key = lambda r: (r["score"], r["delinquent_due"] or 0, r["market_value"] or 0)
        group.sort(key=key)
        n, i = len(group), 0
        while i < n:
            j = i
            while j < n and key(group[j]) == key(group[i]):
                j += 1
            for r in group[i:j]:
                r["score_pct"] = round(100.0 * j / n, 1)
            i = j


# ------------------------------------------------------------------ favorites
# Kept in their own table: ingest/build replace the data tables, never this one.

def ensure_favorites(conn: sqlite3.Connection) -> None:
    conn.execute("CREATE TABLE IF NOT EXISTS favorites (account TEXT PRIMARY KEY, note TEXT, created_at TEXT)")
    conn.commit()


def set_favorite(conn: sqlite3.Connection, account: str, favorite: bool, note: Optional[str] = None) -> None:
    ensure_favorites(conn)
    if favorite:
        conn.execute("""INSERT INTO favorites VALUES (?, ?, ?)
                        ON CONFLICT(account) DO UPDATE SET note = COALESCE(excluded.note, note)""",
                     (account, note, dt.datetime.now().isoformat(timespec="seconds")))
    else:
        conn.execute("DELETE FROM favorites WHERE account = ?", (account,))
    conn.commit()


# ----------------------------------------------------------------- enrichment

_TRUE = {"1", "y", "yes", "true", "t"}


def load_enrichment(conn: sqlite3.Connection, csv_path: str) -> int:
    """CSV with ``account`` plus ``flood_zone`` and/or ``road_access`` (1/0/blank)."""
    conn.execute("CREATE TABLE IF NOT EXISTS enrichment (account TEXT PRIMARY KEY, flood_zone INTEGER, "
                 "road_access INTEGER, source TEXT)")

    def flag(v):
        v = (v or "").strip().lower()
        return None if v == "" else int(v in _TRUE)

    n = 0
    with open(csv_path, newline="") as fh:
        for row in csv.DictReader(fh):
            conn.execute("""INSERT INTO enrichment VALUES (?, ?, ?, ?)
                            ON CONFLICT(account) DO UPDATE SET
                              flood_zone=COALESCE(excluded.flood_zone, flood_zone),
                              road_access=COALESCE(excluded.road_access, road_access),
                              source=excluded.source""",
                         (row["account"].strip(), flag(row.get("flood_zone")), flag(row.get("road_access")),
                          pathlib.Path(csv_path).name))
            n += 1
    conn.commit()
    return n
