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
import logging
import pathlib
import re
import sqlite3
import zipfile
from typing import Dict, Iterable, Iterator, List, Optional, Tuple

from .layout import FileSpec, Layout
from .scoring import Weights, compute_signals, delinquent_through_year, score

log = logging.getLogger(__name__)
BATCH = 5000

PARCEL_COLUMNS = [
    "account", "cad_id", "owner_name", "owner_name2",
    "mail_addr1", "mail_addr2", "mail_city", "mail_state", "mail_zip",
    "situs_address", "situs_city", "legal_desc", "state_code",
    "acreage", "land_value", "impr_value", "market_value", "exemptions", "deed_date",
    "years_delinquent", "first_delinquent_year", "last_delinquent_year",
    "delinquent_due", "total_due", "in_suit",
    "flood_zone", "road_access",
    "is_delinquent", "is_out_of_state", "is_estate", "is_long_held", "is_vacant", "is_absentee",
    "score", "score_pct", "reasons",
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


def build(conn: sqlite3.Connection, layout: Layout, *, as_of: Optional[dt.date] = None,
          weights: Weights = Weights(), long_held_years: int = 10) -> int:
    as_of = as_of or dt.date.today()
    through = delinquent_through_year(as_of)

    # Tax units ------------------------------------------------------------
    conn.execute("DROP TABLE IF EXISTS tax_units")
    if _has_table(conn, "raw_tax_unit") and "tax_unit" in layout.canonical:
        conn.execute(f"CREATE TABLE tax_units AS SELECT DISTINCT {_select_canonical(layout, 'tax_unit')} "
                     f"FROM raw_tax_unit")
    else:
        conn.execute("CREATE TABLE tax_units (unit_code TEXT, unit_name TEXT)")

    # Receivables: one row per account/year/unit. The two county zips can
    # overlap, so keep the larger balance rather than double-counting.
    conn.execute("DROP TABLE IF EXISTS receivables")
    rmap = layout.canonical.get("receivable", {})
    if _has_table(conn, "raw_receivable") and rmap:
        cols = [c for c in rmap if c not in ("account", "tax_year", "unit_code")]
        agg = ", ".join(f'MAX("{rmap[c]}") AS {c}' for c in cols)
        conn.execute(f'''CREATE TABLE receivables AS
            SELECT "{rmap["account"]}" AS account, "{rmap["tax_year"]}" AS tax_year,
                   "{rmap.get("unit_code", rmap["account"])}" AS unit_code, {agg}
            FROM raw_receivable GROUP BY 1, 2, 3''')
    else:
        conn.execute("CREATE TABLE receivables (account TEXT, tax_year INT, unit_code TEXT, total_due REAL)")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_recv_account ON receivables(account)")

    has_suit = "suit_flag" in rmap
    delinquent = {
        r["account"]: dict(r) for r in conn.execute(f'''
            SELECT account,
                   COUNT(DISTINCT CASE WHEN tax_year <= :y AND total_due > 0 THEN tax_year END) AS years_delinquent,
                   MIN(CASE WHEN tax_year <= :y AND total_due > 0 THEN tax_year END) AS first_delinquent_year,
                   MAX(CASE WHEN tax_year <= :y AND total_due > 0 THEN tax_year END) AS last_delinquent_year,
                   ROUND(SUM(CASE WHEN tax_year <= :y AND total_due > 0 THEN total_due ELSE 0 END), 2) AS delinquent_due,
                   ROUND(SUM(CASE WHEN total_due > 0 THEN total_due ELSE 0 END), 2) AS total_due,
                   {"MAX(CASE WHEN TRIM(COALESCE(suit_flag,'')) NOT IN ('', 'N', '0') THEN 1 ELSE 0 END)" if has_suit else "0"} AS in_suit
            FROM receivables GROUP BY account''', {"y": through})
    }

    # Optional GIS enrichment (flood zone / road access) ----------------------
    conn.execute("CREATE TABLE IF NOT EXISTS enrichment (account TEXT PRIMARY KEY, flood_zone INTEGER, "
                 "road_access INTEGER, source TEXT)")
    enrich = {r["account"]: dict(r) for r in conn.execute("SELECT * FROM enrichment")}

    # Parcels ------------------------------------------------------------------
    conn.execute("DROP TABLE IF EXISTS parcels")
    conn.execute(f"""CREATE TABLE parcels ({", ".join(PARCEL_COLUMNS)},
                     PRIMARY KEY (account))""")
    mmap = layout.canonical["master"]
    homestead = re.compile(layout.homestead_pattern, re.IGNORECASE)
    # Last-loaded row wins when an account appears in more than one file.
    master_sql = (f"SELECT {_select_canonical(layout, 'master')} FROM raw_master "
                  f"WHERE rowid IN (SELECT MAX(rowid) FROM raw_master GROUP BY \"{mmap['account']}\")")
    rows: List[Dict] = []
    for m in conn.execute(master_sql):
        p = {c: None for c in PARCEL_COLUMNS}
        p.update({k: m[k] for k in m.keys() if k in p})
        situs = " ".join(x for x in (m["situs_num"] if "situs_num" in m.keys() else None,
                                     m["situs_street"] if "situs_street" in m.keys() else None) if x)
        p["situs_address"] = situs or (m["situs_address"] if "situs_address" in m.keys() else None)
        d = delinquent.get(p["account"], {})
        for k in ("years_delinquent", "first_delinquent_year", "last_delinquent_year",
                  "delinquent_due", "total_due", "in_suit"):
            p[k] = d.get(k) or (0 if k in ("years_delinquent", "delinquent_due", "total_due", "in_suit") else None)
        e = enrich.get(p["account"], {})
        p["flood_zone"], p["road_access"] = e.get("flood_zone"), e.get("road_access")

        s = compute_signals(p, as_of=as_of, home_state=layout.state, homestead=homestead,
                            long_held_years=long_held_years)
        p["score"] = score(s, weights)
        p["reasons"] = "; ".join(s.reasons)
        p.update(is_delinquent=int(s.delinquent), is_out_of_state=int(s.out_of_state),
                 is_estate=int(s.estate), is_long_held=int(s.long_held),
                 is_vacant=int(s.vacant), is_absentee=int(s.absentee))
        rows.append(p)

    # score_pct = share of parcels scoring at or below this one (100 = top).
    rows.sort(key=lambda r: r["score"])
    n, i = len(rows), 0
    while i < n:
        j = i
        while j < n and rows[j]["score"] == rows[i]["score"]:
            j += 1
        for r in rows[i:j]:
            r["score_pct"] = round(100.0 * j / n, 1)
        i = j

    placeholders = ", ".join("?" * len(PARCEL_COLUMNS))
    conn.executemany(f"INSERT INTO parcels VALUES ({placeholders})",
                     [[r[c] for c in PARCEL_COLUMNS] for r in rows])
    for col in ("score", "mail_state", "state_code", "owner_name", "situs_city", "years_delinquent", "delinquent_due"):
        conn.execute(f"CREATE INDEX IF NOT EXISTS ix_parcels_{col} ON parcels({col})")

    conn.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
    meta = {"county": layout.county, "state": layout.state, "as_of": as_of.isoformat(),
            "delinquent_through_year": str(through), "built_at": dt.datetime.now().isoformat(timespec="seconds"),
            "layout_notes": layout.notes, "weights": repr(weights.as_dict())}
    conn.executemany("INSERT OR REPLACE INTO meta VALUES (?, ?)", meta.items())
    conn.commit()
    return n


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
