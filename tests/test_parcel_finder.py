import datetime as dt
import json

import pytest

from parcel_finder import db, queries, sample
from parcel_finder.layout import FileSpec, Field, LayoutError, load_layout
from parcel_finder.scoring import (Signals, Weights, compute_signals, delinquent_through_year,
                                   is_estate, is_out_of_state, is_vacant, score)

AS_OF = dt.date(2026, 9, 22)


@pytest.fixture(scope="module")
def layout():
    return load_layout("denton")


@pytest.fixture(scope="module")
def built(tmp_path_factory, layout):
    tmp = tmp_path_factory.mktemp("pf")
    z = sample.generate(layout, tmp / "TaxRoll_DEMO.zip", n=400, seed=3, as_of=AS_OF)
    conn = db.connect(tmp / "parcels.db")
    counts = db.ingest(conn, layout, [z])
    db.build(conn, layout, as_of=AS_OF)
    yield conn, counts
    conn.close()


# ---------------------------------------------------------------- scoring

@pytest.mark.parametrize("name, expected", [
    ("SMITH JOHN ESTATE", True), ("EST OF MARY JONES", True), ("JONES HEIRS", True),
    ("GARCIA MARIA ET AL", True), ("GARCIA MARIA ETAL", True), ("DAVIS BOB DECD", True),
    ("WHITE SUSAN LIFE ESTATE", True), ("OAK ESTATES HOA", False), ("ACME REAL ESTATE LLC", False),
    ("ESTEBAN RUIZ", False), ("", False), (None, False),
])
def test_estate_names(name, expected):
    assert is_estate(name) is expected


def test_out_of_state_treats_blank_as_unknown():
    assert is_out_of_state("OK") and not is_out_of_state("tx") and not is_out_of_state("") \
        and not is_out_of_state(None)


def test_vacant_by_improvements_or_code():
    assert is_vacant(0, "A1", land_value=40000)
    assert is_vacant(None, "C1")
    assert is_vacant(0, "010", ("010",))
    assert not is_vacant(250000, "A1", land_value=40000)
    assert not is_vacant(None, "A1")
    # Split left blank (land 0, impr 0) with only a total value: unknown, not vacant.
    assert not is_vacant(0, "001", ("010",), land_value=0)


def test_delinquent_through_year_uses_feb_1_cutoff():
    assert delinquent_through_year(dt.date(2026, 1, 31)) == 2024
    assert delinquent_through_year(dt.date(2026, 2, 1)) == 2025


def test_score_matches_the_rubric():
    s = Signals(delinquent=True, years_behind=3, out_of_state=True, estate=True, long_held=True)
    w = Weights(vacant=0, absentee=0)
    assert score(s, w) == 3 + 3 + 2 + 2 + 1
    s = Signals(delinquent=True, years_behind=1, flood_zone=True, road_access=False)
    assert score(s, w) == 3 + 1 - 5 - 5
    assert "+2 out-of-state owner" not in s.reasons


def test_unknown_flood_and_road_do_not_penalise():
    assert score(Signals(flood_zone=None, road_access=None), Weights(absentee=0)) == 0


def test_compute_signals_reads_parcel_fields():
    import re
    s = compute_signals({"years_delinquent": 2, "mail_state": "CA", "owner_name": "X HEIRS",
                         "deed_date": "2001-05-01", "impr_value": 0, "land_value": 9000,
                         "exemptions": "HS OV65"},
                        as_of=AS_OF, homestead=re.compile(r"\bHS\b"))
    assert (s.delinquent, s.years_behind, s.out_of_state, s.estate, s.long_held, s.vacant, s.absentee) == \
        (True, 2, True, True, True, True, False)


# ----------------------------------------------------------------- layout

# Verbatim record from Denton's MR092226.DAT (2026-09-22 download).
DENTON_MR = ("520746DEN                     202406800101000000067460000000000000000001.75000000001.75"
             "02/01/202502/21/202501/01/9999                  01/01/9999                  01/01/9999"
             "               00N   01/01/999901/01/999901/01/9999")


def test_denton_layout_matches_the_guide(layout):
    assert sum(f.length for f in layout.files["master"].fields) == 950
    assert sum(f.length for f in layout.files["receivable"].fields) == 224
    assert len(DENTON_MR) == 224
    r = next(layout.files["receivable"].parse_lines([DENTON_MR]))
    assert (r["ACCOUNT_NUMBER"], r["YEAR"], r["TAX_UNIT_NUMBER"], r["RECEIVABLE_TYPE_CODE"], r["SEQUENCE_NUMBER"]) \
        == ("520746DEN", 2024, "068", "001", "01")
    assert (r["VALUE"], r["LEVY"], r["AMOUNT_DUE"]) == (6746, 1.75, 1.75)
    assert (r["DELINQUENCY_DATE"], r["DATE_3307"]) == ("2025-02-01", "2025-02-21")
    assert r["JUDGMENT_DATE"] is None and r["SUIT_DATE"] is None     # 01/01/9999 = blank
    assert r["INSTALLMENT"] == "N"


def test_denton_homestead_codes(layout):
    import re
    hs = re.compile(layout.homestead_pattern)
    assert hs.search("1-2-3") and hs.search("2-3-52") and hs.search("1")
    assert not hs.search("128") and not hs.search("38-128") and not hs.search("") and not hs.search("108")


def test_fixed_width_round_trip(layout):
    spec = layout.files["receivable"]
    rec = next(spec.parse_lines([DENTON_MR]))
    rec.update(SUIT_NUMBER="2024-1234", SUIT_DATE="2024-06-30", AMOUNT_DUE=1244.56)
    assert next(spec.parse_lines([spec.format_record(rec)])) == rec


def test_delimited_with_header():
    spec = FileSpec(kind="master", prefixes=["MM"], format="delimited", delimiter="|", header=True,
                    fields=[Field("ACCOUNT"), Field("LAND", type="int"), Field("DUE", type="decimal")])
    rows = list(spec.parse_lines(["DUE|ACCOUNT|LAND\n", "12.50|A1|1,000\n", "\n"]))
    assert rows == [{"ACCOUNT": "A1", "LAND": 1000, "DUE": 12.5}]


def test_file_kind_by_prefix(layout):
    assert layout.kind_for("TaxRoll_V1/MM092226.DAT") == "master"
    assert layout.kind_for("AR_FILE.txt") == "receivable"
    assert layout.kind_for("as2025.txt") == "statistic"
    assert layout.kind_for("TU.TXT") == "tax_unit"
    assert layout.kind_for("README.TXT") is None


def test_layout_rejects_unknown_canonical_column(tmp_path):
    doc = {"files": {"master": {"prefixes": ["MM"], "fields": [{"name": "A", "start": 1, "length": 2}]}},
           "canonical": {"master": {"account": "NOPE"}}}
    p = tmp_path / "bad.json"
    p.write_text(json.dumps(doc))
    with pytest.raises(LayoutError):
        load_layout(str(p))


# ------------------------------------------------------------- pipeline

def test_ingest_loads_every_file_kind(built):
    _, counts = built
    assert counts["master"] == 400 and counts["tax_unit"] == 10
    assert counts["receivable"] > 400


def test_build_rolls_up_only_past_due_years(built):
    conn, _ = built
    bad = conn.execute("SELECT COUNT(*) FROM parcels WHERE last_delinquent_year > 2025").fetchone()[0]
    assert bad == 0
    row = conn.execute("SELECT * FROM parcels WHERE years_delinquent >= 2 LIMIT 1").fetchone()
    recv = conn.execute("SELECT COUNT(DISTINCT tax_year) FROM receivables WHERE account=? AND tax_year<=2025 "
                        "AND total_due>0", (row["account"],)).fetchone()[0]
    assert recv == row["years_delinquent"] and row["is_delinquent"] == 1


def test_reingesting_overlapping_zip_does_not_double_count(tmp_path, layout):
    z = sample.generate(layout, tmp_path / "a.zip", n=50, seed=9, as_of=AS_OF)
    conn = db.connect(tmp_path / "p.db")
    db.ingest(conn, layout, [z])
    db.build(conn, layout, as_of=AS_OF)
    once = conn.execute("SELECT SUM(delinquent_due) FROM parcels").fetchone()[0]
    db.ingest(conn, layout, [z, z])          # same data in both "downloads"
    db.build(conn, layout, as_of=AS_OF)
    assert conn.execute("SELECT COUNT(*) FROM parcels").fetchone()[0] == 50
    assert conn.execute("SELECT SUM(delinquent_due) FROM parcels").fetchone()[0] == once


def test_enrichment_penalises_flood_zone(tmp_path, built, layout):
    conn, _ = built
    acct, before = conn.execute("SELECT account, score FROM parcels ORDER BY score DESC LIMIT 1").fetchone()
    csv_path = tmp_path / "gis.csv"
    csv_path.write_text(f"account,flood_zone,road_access\n{acct},1,\n")
    db.load_enrichment(conn, str(csv_path))
    db.build(conn, layout, as_of=AS_OF)
    p = conn.execute("SELECT score, flood_zone, road_access FROM parcels WHERE account=?", (acct,)).fetchone()
    assert p["score"] == before - 5 and p["flood_zone"] == 1 and p["road_access"] is None


# --------------------------------------------------------------- queries

def test_filters_and_top_pct(built):
    conn, _ = built
    res = queries.search(conn, {"delinquent": "1", "out_of_state": "1"}, limit=500)
    assert all(r["is_delinquent"] and r["is_out_of_state"] for r in res["rows"])
    top = queries.search(conn, {"top_pct": "25", "real_property": "1"}, limit=1000)
    floor = min(r["score"] for r in top["rows"])
    below = conn.execute("SELECT MAX(score) FROM parcels WHERE score_pct < 75 AND is_real_property = 1").fetchone()[0]
    assert below is None or below <= floor      # nothing left out outscores what's in
    assert queries.search(conn, {"state_code": "C1"}, limit=1000)["rows"][0]["state_code"].startswith("C1")


def test_sort_column_is_whitelisted(built):
    conn, _ = built
    assert "score" in queries.order_by({"sort": "score; DROP TABLE parcels"})


def test_detail_joins_unit_names(built):
    conn, _ = built
    acct = conn.execute("SELECT account FROM parcels WHERE is_delinquent=1 LIMIT 1").fetchone()[0]
    d = queries.detail(conn, acct)
    assert d["parcel"]["account"] == acct and d["receivables"][0]["unit_name"]


def test_city_comes_from_city_tax_unit(built):
    conn, _ = built
    cities = {r[0] for r in conn.execute("SELECT DISTINCT situs_city FROM parcels")}
    assert {"DENTON", "THE COLONY", "UNINCORPORATED"} <= cities
    assert not any(c.startswith(("CITY OF", "TOWN OF")) for c in cities)


def test_collection_status_flags_and_rolls(built):
    conn, _ = built
    n = lambda where: conn.execute(f"SELECT COUNT(*) FROM parcels WHERE {where}").fetchone()[0]
    assert n("in_suit = 1") > 0 and n("in_judgment = 1") > 0 and n("in_deferral = 1") > 0
    assert n("in_judgment = 1 AND in_suit = 0") == 0
    assert 0 < n("is_real_property = 1") < 400
    assert n("reasons LIKE '%tax deferral%' AND in_deferral = 0") == 0


def test_top_pct_is_close_to_requested_share(built):
    conn, _ = built
    real = conn.execute("SELECT COUNT(*) FROM parcels WHERE is_real_property = 1").fetchone()[0]
    got = queries.search(conn, {"top_pct": "25", "real_property": "1"}, limit=1000)["total"]
    assert abs(got / real - 0.25) < 0.03


def test_mailable_filter_drops_withheld_owners(built):
    conn, _ = built
    acct = conn.execute("SELECT account FROM parcels LIMIT 1").fetchone()[0]
    conn.execute("UPDATE parcels SET owner_name = 'CONFIDENTIAL OWNER' WHERE account = ?", (acct,))
    rows = queries.search(conn, {"mailable": "1", "q": acct}, limit=10)["rows"]
    conn.rollback()
    assert rows == []


def test_cad_link_from_account_suffix(layout):
    assert db.cad_link("963342DEN", layout) == ("Denton CAD", "https://www.dentoncad.com/property-detail/963342")
    assert db.cad_link("40355667TAR", layout) == ("Tarrant CAD",
                                                  "https://tarrant.prodigycad.com/property-detail/40355667")
    assert db.cad_link("771087WIS", layout) == ("Wise CAD", None)
    assert db.cad_link("800310200A03", layout) == (None, None)
    assert db.cad_link(None, layout) == (None, None)


def test_favorites_survive_rebuild_and_filter(tmp_path, layout):
    z = sample.generate(layout, tmp_path / "f.zip", n=60, seed=4, as_of=AS_OF)
    conn = db.connect(tmp_path / "f.db")
    db.ingest(conn, layout, [z])
    db.build(conn, layout, as_of=AS_OF)
    a, b = [r[0] for r in conn.execute("SELECT account FROM parcels LIMIT 2")]
    db.set_favorite(conn, a, True, "called owner")
    db.set_favorite(conn, b, True)
    db.set_favorite(conn, b, False)
    db.set_favorite(conn, a, True)                 # re-starring keeps the note
    db.ingest(conn, layout, [z])
    db.build(conn, layout, as_of=AS_OF)
    rows = queries.search(conn, {"favorites": "1"}, limit=10)["rows"]
    assert [(r["account"], r["is_favorite"], r["fav_note"]) for r in rows] == [(a, 1, "called owner")]
    assert queries.detail(conn, b)["parcel"]["is_favorite"] == 0
    header, *body = list(queries.iter_all(conn, {"favorites": "1"}))
    assert header[-2:] == ["is_favorite", "fav_note"] and body[0][-1] == "called owner"
    assert queries.summary(conn)["favorites"] == 1


def test_web_favorite_endpoint(tmp_path, layout):
    import threading
    import urllib.request
    from http.server import ThreadingHTTPServer
    from parcel_finder.web import make_handler
    z = sample.generate(layout, tmp_path / "w.zip", n=30, seed=5, as_of=AS_OF)
    path = tmp_path / "w.db"
    conn = db.connect(path)
    db.ingest(conn, layout, [z])
    db.build(conn, layout, as_of=AS_OF)
    acct = conn.execute("SELECT account FROM parcels LIMIT 1").fetchone()[0]
    conn.close()
    srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(str(path)))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_port}"

    def post(account, body, ctype="application/json"):
        req = urllib.request.Request(f"{base}/api/favorite/{account}", json.dumps(body).encode(),
                                     {"Content-Type": ctype}, method="POST")
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.load(r)
        except urllib.error.HTTPError as e:
            return e.code, json.load(e)

    try:
        assert post(acct, {"favorite": True, "note": " hot lead "}) == (200, {"account": acct, "favorite": True,
                                                                             "favorites": 1})
        with urllib.request.urlopen(f"{base}/api/parcels?favorites=1") as r:
            rows = json.load(r)["rows"]
        assert [(x["account"], x["fav_note"]) for x in rows] == [(acct, "hot lead")]
        assert post("NOPE", {"favorite": True})[0] == 404
        assert post(acct, {"favorite": False}, ctype="text/plain")[0] == 415
        assert post(acct, {"favorite": False})[1]["favorites"] == 0
    finally:
        srv.shutdown()


@pytest.mark.parametrize("value, points", [
    (4_999, 0), (5_000, 4), (124_999, 4), (125_000, 3), (249_999, 3), (250_000, 2),
    (374_999, 2), (375_000, 1), (499_999, 1), (500_000, 0), (None, 0), (0, 0),
])
def test_lower_price_scores_higher(value, points):
    s = compute_signals({"market_value": value, "exemptions": "HS"}, as_of=AS_OF)
    assert score(s, Weights()) == points


@pytest.mark.parametrize("acres, fits", [(0.49, False), (0.5, True), (2.2, True), (5.0, True), (5.01, False),
                                         (None, False), (0, False)])
def test_target_acreage_band(acres, fits):
    s = compute_signals({"acreage": acres, "exemptions": "HS"}, as_of=AS_OF)
    assert s.target_acreage is fits and score(s, Weights()) == (3 if fits else 0)


def test_targets_are_configurable():
    from parcel_finder.scoring import Targets
    t = Targets(min_acres=5, max_acres=20, max_value=200_000)
    s = compute_signals({"acreage": 10, "market_value": 150_000, "exemptions": "HS"}, as_of=AS_OF, targets=t)
    assert s.target_acreage and score(s, Weights()) == 3 + 1


def test_years_behind_points_are_capped():
    s = Signals(delinquent=True, years_behind=22)
    assert score(s, Weights(absentee=0)) == 3 + 5
    assert "+5 22 yr(s) behind" in s.reasons
    assert score(s, Weights(absentee=0, max_years_scored=0)) == 3 + 22
