"""Parcel locations (from the appraisal districts) and FEMA flood zones.

1. ``locate``  - the tax roll has no coordinates, but each CAD's public
                 property search (True Prodigy, used by Denton and Tarrant CAD)
                 returns a latitude/longitude per property ID. Batched 200 at a time.
2. ``flood``   - asks FEMA's National Flood Hazard Layer which flood zone each
                 parcel is in. A single point would miss a 3-acre lot whose back
                 half is floodplain, so it checks a circle of the parcel's area:

                   in_sfha      the parcel's center is in a Special Flood Hazard
                                Area (the 1%-annual-chance "100-year" zones:
                                A, AE, AH, AO, V, VE ...)
                   near_sfha    an SFHA touches the parcel-sized circle, but not
                                its center: partly in the flood zone

Both results are cached in the database (``locations`` and ``flood`` tables),
so reruns only look up new parcels; ``build`` folds them into the score.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import math
import re
import sqlite3
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Dict, Iterable, List, Optional, Tuple

from .layout import Layout

log = logging.getLogger(__name__)

PRODIGY_API = "https://prod-container.trueprodigyapi.com"
NFHL_ZONES = "https://hazards.fema.gov/arcgis/rest/services/public/NFHL/MapServer/28/query"
BATCH = 200
MAX_RADIUS_M = 150.0      # cap the "parcel-sized circle" for big acreage


# ------------------------------------------------------------------- http

def _request(url: str, data: Optional[dict] = None, headers: Optional[dict] = None,
             retries: int = 3, timeout: int = 60):
    hdrs = {"User-Agent": "parcel-finder/1.0", "Cache-Control": "no-cache", **(headers or {})}
    body = None
    if data is not None:
        body = json.dumps(data).encode()
        hdrs["Content-Type"] = "application/json"
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, body, hdrs), timeout=timeout) as r:
                return json.load(r)
        except (OSError, ValueError) as exc:          # URLError/HTTPError/timeouts/bad JSON
            if attempt == retries - 1:
                raise
            log.debug("retrying %s after %s", url, exc)
            time.sleep(2 ** attempt)


# ---------------------------------------------------------------- locate

def ensure_tables(conn: sqlite3.Connection) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS locations (account TEXT PRIMARY KEY, pid INTEGER,
                    latitude REAL, longitude REAL, source TEXT, fetched_at TEXT)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS flood (account TEXT PRIMARY KEY, fld_zone TEXT,
                    zone_subty TEXT, in_sfha INTEGER, near_sfha INTEGER, radius_m REAL, fetched_at TEXT)""")
    conn.commit()


def _cad_offices(layout: Layout) -> Dict[str, Tuple[str, str]]:
    """District suffix -> (True Prodigy office name, the district site's origin)."""
    out = {}
    for suffix, d in (layout.extras.get("appraisal_districts") or {}).items():
        if d.get("api_office"):
            u = urllib.parse.urlparse(d.get("url") or "")
            out[suffix] = (d["api_office"], f"{u.scheme}://{u.netloc}" if u.netloc else "")
    return out


def _year_candidates(as_of: dt.date) -> List[str]:
    # The certified roll for the prior year always exists; new parcels may only
    # be in the current one.
    return [str(as_of.year), str(as_of.year - 1), str(as_of.year - 2)]


def locate(conn: sqlite3.Connection, layout: Layout, *, where: str = "is_real_property = 1",
           refresh: bool = False, as_of: Optional[dt.date] = None,
           post: Callable = None) -> Dict[str, int]:
    """Fill ``locations`` for parcels matching ``where``. Returns counts per district."""
    post = post or (lambda url, data, headers: _request(url, data, headers))
    ensure_tables(conn)
    as_of = as_of or dt.date.today()
    pattern = re.compile(layout.extras.get("cad_account_pattern") or r"^(?P<id>\d+)(?P<cad>[A-Z]{3})$")
    skip = "" if refresh else "AND account NOT IN (SELECT account FROM locations)"
    by_cad: Dict[str, Dict[int, str]] = {}
    offices = _cad_offices(layout)
    for (account,) in conn.execute(f"SELECT account FROM parcels WHERE {where} {skip}"):
        m = pattern.match(account)
        if m and m.group("cad") in offices:
            by_cad.setdefault(m.group("cad"), {})[int(m.group("id"))] = account

    counts: Dict[str, int] = {}
    now = dt.datetime.now().isoformat(timespec="seconds")
    for cad, pid_to_account in by_cad.items():
        office, origin = offices[cad]
        base = {"Origin": origin} if origin else {}
        token = post(f"{PRODIGY_API}/trueprodigy/cadpublic/auth/token", {"office": office}, base)["user"]["token"]
        headers = {**base, "Authorization": token}
        pending = dict(pid_to_account)
        found: Dict[int, Tuple[float, float]] = {}
        for year in _year_candidates(as_of):
            pids = [p for p in pending if p not in found]
            for i in range(0, len(pids), BATCH):
                chunk = pids[i:i + BATCH]
                res = post(f"{PRODIGY_API}/public/property/search?page=1&pageSize={BATCH}",
                           {"pYear": {"operator": "=", "value": year}, "pid": {"operator": "in", "value": chunk}},
                           headers)
                for x in res.get("results") or []:
                    try:
                        lat, lon = float(x.get("latitude") or 0), float(x.get("longitude") or 0)
                    except ValueError:
                        continue
                    if lat and lon:
                        found[int(x["pid"])] = (lat, lon)
            log.info("%s: %s of %s located after tax year %s", office, len(found), len(pending), year)
        rows = [(acct, pid, *(found.get(pid) or (None, None)), f"{office} CAD", now)
                for pid, acct in pending.items()]
        conn.executemany("INSERT OR REPLACE INTO locations VALUES (?, ?, ?, ?, ?, ?)", rows)
        conn.commit()
        counts[office] = len(found)
    return counts


# ------------------------------------------------------------------ flood

def parcel_radius_m(acres: Optional[float]) -> float:
    """Radius of a circle with the parcel's area (capped); 10 m when unknown."""
    if not acres or acres <= 0:
        return 10.0
    return min(MAX_RADIUS_M, math.sqrt(acres * 4046.86 / math.pi))


def _zones_at(lon: float, lat: float, radius_m: float, get: Callable) -> List[dict]:
    q = {"geometry": f"{lon},{lat}", "geometryType": "esriGeometryPoint", "inSR": 4326,
         "spatialRel": "esriSpatialRelIntersects", "outFields": "FLD_ZONE,ZONE_SUBTY,SFHA_TF",
         "returnGeometry": "false", "f": "json"}
    if radius_m:
        q.update(distance=round(radius_m, 1), units="esriSRUnit_Meter")
    d = get(NFHL_ZONES + "?" + urllib.parse.urlencode(q))
    if "error" in d:
        raise OSError(f"FEMA NFHL error: {d['error']}")
    return [f["attributes"] for f in d.get("features") or []]


def classify(center: List[dict], around: List[dict]) -> Dict[str, object]:
    """Summarise FEMA features at the parcel center and within its circle."""
    def sfha(zs):
        return [z for z in zs if (z.get("SFHA_TF") or "").upper() == "T"]

    main = (sfha(center) or center or sfha(around) or around or [{}])[0]
    in_sfha = bool(sfha(center))
    return {"fld_zone": main.get("FLD_ZONE"), "zone_subty": main.get("ZONE_SUBTY"),
            "in_sfha": int(in_sfha), "near_sfha": int(not in_sfha and bool(sfha(around)))}


def flood(conn: sqlite3.Connection, *, where: str = "is_real_property = 1", refresh: bool = False,
          workers: int = 6, get: Callable = None, progress: Callable[[int, int], None] = None) -> int:
    """Fill ``flood`` for located parcels matching ``where``. Returns parcels looked up."""
    get = get or (lambda url: _request(url))
    ensure_tables(conn)
    skip = "" if refresh else "AND l.account NOT IN (SELECT account FROM flood)"
    todo = conn.execute(f"""SELECT l.account, l.latitude, l.longitude, p.acreage
                            FROM locations l JOIN parcels p USING (account)
                            WHERE l.latitude IS NOT NULL AND {where} {skip}""").fetchall()

    def one(row):
        account, lat, lon, acres = row
        r = parcel_radius_m(acres)
        around = _zones_at(lon, lat, r, get)
        # Only when the circle touches an SFHA does it matter whether the center does.
        center = _zones_at(lon, lat, 0, get) if any((z.get("SFHA_TF") or "") == "T" for z in around) else \
            [z for z in around if (z.get("SFHA_TF") or "") != "T"][:1] or around[:1]
        return account, classify(center, around), r

    done, now = 0, dt.datetime.now().isoformat(timespec="seconds")
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        for i in range(0, len(todo), 200):          # commit as we go: safe to Ctrl+C and resume
            results = list(pool.map(one, todo[i:i + 200]))
            conn.executemany("INSERT OR REPLACE INTO flood VALUES (?, ?, ?, ?, ?, ?, ?)",
                             [(a, c["fld_zone"], c["zone_subty"], c["in_sfha"], c["near_sfha"], r, now)
                              for a, c, r in results])
            conn.commit()
            done += len(results)
            if progress:
                progress(done, len(todo))
    return done


def fema_zone_label(fld_zone: Optional[str], zone_subty: Optional[str]) -> Optional[str]:
    if not fld_zone:
        return None
    sub = (zone_subty or "").strip()
    if "FLOODWAY" in sub.upper():
        return f"{fld_zone} (floodway)"
    if "0.2 PCT" in sub.upper():
        return f"{fld_zone} (0.2% annual chance)"
    return fld_zone
