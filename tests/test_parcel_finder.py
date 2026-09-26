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
    assert is_vacant(0, "A1")
    assert is_vacant(None, "C1")
    assert not is_vacant(250000, "A1")
    assert not is_vacant(None, "A1")


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
                         "deed_date": "2001-05-01", "impr_value": 0, "exemptions": "HS OV65"},
                        as_of=AS_OF, homestead=re.compile(r"\bHS\b"))
    assert (s.delinquent, s.years_behind, s.out_of_state, s.estate, s.long_held, s.vacant, s.absentee) == \
        (True, 2, True, True, True, True, False)


# ----------------------------------------------------------------- layout

def test_fixed_width_round_trip(layout):
    spec = layout.files["receivable"]
    rec = {"ACCOUNT": "123", "TAX_YEAR": 2021, "UNIT_CODE": "S07", "LEVY": 1234.56,
           "BASE_DUE": 1234.56, "PEN_INT_DUE": 10.0, "TOTAL_DUE": 1244.56, "SUIT_FLAG": "Y"}
    parsed = next(spec.parse_lines([spec.format_record(rec)]))
    assert parsed == rec


def test_delimited_with_header():
    spec = FileSpec(kind="master", prefixes=["MM"], format="delimited", delimiter="|", header=True,
                    fields=[Field("ACCOUNT"), Field("LAND", type="int"), Field("DUE", type="decimal")])
    rows = list(spec.parse_lines(["DUE|ACCOUNT|LAND\n", "12.50|A1|1,000\n", "\n"]))
    assert rows == [{"ACCOUNT": "A1", "LAND": 1000, "DUE": 12.5}]


def test_file_kind_by_prefix(layout):
    assert layout.kind_for("TaxRoll_V1/MM20260922.TXT") == "master"
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
    assert counts["master"] == 400 and counts["tax_unit"] == 9
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
    top = queries.search(conn, {"top_pct": "25"}, limit=1000)
    floor = min(r["score"] for r in top["rows"])
    below = conn.execute("SELECT MAX(score) FROM parcels WHERE score_pct < 75").fetchone()[0]
    assert below is None or below < floor
    assert queries.search(conn, {"state_code": "C1"}, limit=1000)["rows"][0]["state_code"].startswith("C1")


def test_sort_column_is_whitelisted(built):
    conn, _ = built
    assert "score" in queries.order_by({"sort": "score; DROP TABLE parcels"})


def test_detail_joins_unit_names(built):
    conn, _ = built
    acct = conn.execute("SELECT account FROM parcels WHERE is_delinquent=1 LIMIT 1").fetchone()[0]
    d = queries.detail(conn, acct)
    assert d["parcel"]["account"] == acct and d["receivables"][0]["unit_name"]
