"""Filtered, sorted, paged reads of the ``parcels`` table for the web UI / CSV."""

from __future__ import annotations

import sqlite3
from typing import Dict, List, Mapping, Tuple

from .db import PARCEL_COLUMNS

# Parcels with their favorite flag/note. USING keeps "account" unambiguous.
FROM = "FROM parcels LEFT JOIN favorites f USING (account)"
FAV_COLS = "f.account IS NOT NULL AS is_favorite, f.note AS fav_note"

SORTABLE = {"score", "delinquent_due", "total_due", "years_delinquent", "market_value",
            "land_value", "impr_value", "acreage", "owner_name", "account", "deed_date",
            "mail_state", "situs_city", "situs_address", "state_code"}

FLAG_FILTERS = {  # query param -> column
    "delinquent": "is_delinquent", "out_of_state": "is_out_of_state", "estate": "is_estate",
    "long_held": "is_long_held", "vacant": "is_vacant", "absentee": "is_absentee",
    "in_suit": "in_suit", "judgment": "in_judgment", "bankruptcy": "in_bankruptcy", "deferral": "in_deferral",
    "real_property": "is_real_property",
}

# Owners a letter can't reach: no mailing address, or a withheld/unknown name.
UNMAILABLE_OWNERS = ("UNKNOWN", "CONFIDENTIAL OWNER", "CONFIDENTIAL")


def _num(params: Mapping[str, str], key: str):
    v = (params.get(key) or "").strip()
    if not v:
        return None
    try:
        return float(v)
    except ValueError:
        return None


def build_where(params: Mapping[str, str]) -> Tuple[str, List]:
    clauses, args = [], []
    for key, col in FLAG_FILTERS.items():
        v = (params.get(key) or "").strip().lower()
        if v in ("1", "true", "yes"):
            clauses.append(f"{col} = 1")
        elif v in ("0", "false", "no"):
            clauses.append(f"COALESCE({col}, 0) = 0")
    for key, col, op in (("min_score", "score", ">="), ("min_years", "years_delinquent", ">="),
                         ("min_due", "delinquent_due", ">="), ("max_due", "delinquent_due", "<="),
                         ("min_value", "market_value", ">="), ("max_value", "market_value", "<="),
                         ("min_acres", "acreage", ">="), ("max_acres", "acreage", "<="),
                         ("top_pct", "score_pct", ">=")):
        n = _num(params, key)
        if n is not None:
            if key == "top_pct":           # "top 25%" -> score_pct >= 75
                n = 100 - n
            clauses.append(f"{col} {op} ?")
            args.append(n)
    if (params.get("mailable") or "") in ("1", "true"):
        clauses.append("TRIM(COALESCE(mail_addr1, '')) <> '' AND UPPER(COALESCE(owner_name, '')) NOT IN (%s)"
                       % ", ".join("?" * len(UNMAILABLE_OWNERS)))
        args += list(UNMAILABLE_OWNERS)
    if (params.get("favorites") or "") in ("1", "true"):
        clauses.append("f.account IS NOT NULL")
    if (params.get("exclude_flood") or "") in ("1", "true"):
        clauses.append("COALESCE(flood_zone, 0) = 0")
    if (params.get("require_road") or "") in ("1", "true"):
        clauses.append("COALESCE(road_access, 1) = 1")
    for key, col in (("mail_state", "mail_state"), ("state_code", "state_code"), ("city", "situs_city"),
                     ("roll", "roll")):
        v = (params.get(key) or "").strip().upper()
        if v:
            vals = [x.strip() for x in v.split(",") if x.strip()]
            if col == "state_code":      # prefix match: "C1" hits C1, C1A...
                clauses.append("(" + " OR ".join(f"UPPER({col}) LIKE ?" for _ in vals) + ")")
                args += [x + "%" for x in vals]
            else:
                clauses.append(f"UPPER({col}) IN ({', '.join('?' * len(vals))})")
                args += vals
    q = (params.get("q") or "").strip()
    if q:
        like = f"%{q.upper()}%"
        clauses.append("(UPPER(owner_name) LIKE ? OR UPPER(COALESCE(owner_name2,'')) LIKE ? "
                       "OR UPPER(COALESCE(situs_address,'')) LIKE ? OR account LIKE ? "
                       "OR UPPER(COALESCE(legal_desc,'')) LIKE ?)")
        args += [like] * 5
    return ("WHERE " + " AND ".join(clauses)) if clauses else "", args


def order_by(params: Mapping[str, str]) -> str:
    col = params.get("sort") or "score"
    if col not in SORTABLE:
        col = "score"
    direction = "ASC" if (params.get("dir") or "").lower() == "asc" else "DESC"
    return f"ORDER BY {col} {direction} NULLS LAST, delinquent_due DESC, account"


def search(conn: sqlite3.Connection, params: Mapping[str, str], *, limit: int = 100,
           offset: int = 0) -> Dict:
    where, args = build_where(params)
    total = conn.execute(f"SELECT COUNT(*) {FROM} {where}", args).fetchone()[0]
    rows = conn.execute(f"SELECT parcels.*, {FAV_COLS} {FROM} {where} {order_by(params)} LIMIT ? OFFSET ?",
                        args + [limit, offset]).fetchall()
    return {"total": total, "rows": [dict(r) for r in rows]}


def iter_all(conn: sqlite3.Connection, params: Mapping[str, str]):
    where, args = build_where(params)
    yield PARCEL_COLUMNS + ["is_favorite", "fav_note"]
    for r in conn.execute(f"SELECT {', '.join(PARCEL_COLUMNS)}, {FAV_COLS} {FROM} {where} {order_by(params)}",
                          args):
        yield list(r)


def detail(conn: sqlite3.Connection, account: str) -> Dict:
    p = conn.execute(f"SELECT parcels.*, {FAV_COLS} {FROM} WHERE account = ?", (account,)).fetchone()
    if p is None:
        return {}
    recv = conn.execute("""SELECT r.*, u.unit_name FROM receivables r
                           LEFT JOIN tax_units u ON u.unit_code = r.unit_code
                           WHERE r.account = ? ORDER BY r.tax_year DESC, r.unit_code""", (account,)).fetchall()
    return {"parcel": dict(p), "receivables": [dict(r) for r in recv]}


def summary(conn: sqlite3.Connection) -> Dict:
    def real(expr: str = "COUNT(*)", where: str = "1"):
        """Headline numbers cover real property (land) only."""
        return conn.execute(f"SELECT {expr} FROM parcels WHERE is_real_property = 1 AND {where}").fetchone()[0]

    return {
        "meta": {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM meta")},
        "parcels": real(),
        "delinquent": real(where="is_delinquent = 1"),
        "delinquent_due": real("ROUND(COALESCE(SUM(delinquent_due), 0), 2)"),
        "out_of_state": real(where="is_out_of_state = 1"),
        "estate": real(where="is_estate = 1"),
        "vacant": real(where="is_vacant = 1"),
        "all_accounts": conn.execute("SELECT COUNT(*) FROM parcels").fetchone()[0],
        "favorites": conn.execute("SELECT COUNT(*) FROM favorites").fetchone()[0],
        "rolls": [dict(r) for r in conn.execute(
            "SELECT roll, COUNT(*) AS n, MAX(is_real_property) AS real FROM parcels "
            "GROUP BY roll ORDER BY real DESC, n DESC")],
        "mail_states": [r[0] for r in conn.execute(
            "SELECT mail_state FROM parcels WHERE COALESCE(mail_state,'') <> '' "
            "GROUP BY mail_state ORDER BY COUNT(*) DESC")],
        "cities": [r[0] for r in conn.execute(
            "SELECT situs_city FROM parcels WHERE COALESCE(situs_city,'') <> '' "
            "GROUP BY situs_city ORDER BY COUNT(*) DESC LIMIT 200")],
        "state_codes": [dict(r) for r in conn.execute(
            "SELECT state_code, COUNT(*) AS n FROM parcels WHERE COALESCE(state_code,'') <> '' "
            "GROUP BY state_code ORDER BY n DESC LIMIT 60")],
        "score_histogram": [dict(r) for r in conn.execute(
            "SELECT score, COUNT(*) AS n FROM parcels GROUP BY score ORDER BY score")],
    }
