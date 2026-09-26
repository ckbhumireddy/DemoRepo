"""Command-line entrypoint for the tax-delinquent parcel finder.

Examples
--------
Look inside a county download (file kinds, record lengths, sample lines):
    python -m parcel_finder inspect TaxRoll_V1_20260922_085056.zip

Load both Denton zips, score, and browse:
    python -m parcel_finder ingest TaxRoll_V1_20260922_085056.zip TaxRoll_V1_20260922_083833.zip
    python -m parcel_finder build
    python -m parcel_finder serve          # http://127.0.0.1:8765/

Offline demo on synthetic data:
    python -m parcel_finder demo
"""

from __future__ import annotations

import argparse
import collections
import csv
import datetime as dt
import logging
import sys
from typing import List, Optional

from . import db, queries, sample
from .layout import load_layout
from .scoring import Weights

DEFAULT_DB = "parcels.db"


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="parcel_finder",
                                description="Build a local, scored database of motivated-seller parcels "
                                            "from county tax-roll downloads and browse it in a web page.")
    p.add_argument("--db", default=DEFAULT_DB, help=f"SQLite path (default {DEFAULT_DB})")
    p.add_argument("--layout", default="denton", help="layout name under parcel_finder/layouts or a JSON path")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("inspect", help="show what is inside county zip(s)/files, to check the layout")
    s.add_argument("paths", nargs="+")
    s.add_argument("--lines", type=int, default=3, help="sample lines per file")

    s = sub.add_parser("ingest", help="load county zip(s)/dirs/files into raw tables")
    s.add_argument("paths", nargs="+")
    s.add_argument("--append", action="store_true", help="keep previously loaded raw rows")

    s = sub.add_parser("build", help="roll up delinquency, apply enrichment, score every parcel")
    s.add_argument("--as-of", type=dt.date.fromisoformat, help="YYYY-MM-DD (default today)")
    s.add_argument("--long-held-years", type=int, default=10)
    for name, default in Weights().as_dict().items():  # --w-out-of-state 3, --w-deferral 0, ...
        s.add_argument(f"--w-{name.replace('_', '-')}", type=int, default=default, dest=f"w_{name}",
                       help=f"score weight (default {default:+d})")

    s = sub.add_parser("enrich", help="merge a CSV of account,flood_zone,road_access (then re-run build)")
    s.add_argument("csv")

    s = sub.add_parser("export", help="write matching parcels to CSV (same filters as the web page)")
    s.add_argument("out")
    s.add_argument("--top-pct", type=float, help="keep only the top N%% by score, e.g. 25")
    s.add_argument("--min-score", type=float)
    s.add_argument("--delinquent", action="store_true")
    s.add_argument("--favorites", action="store_true", help="only parcels you starred on the page")
    s.add_argument("--all-types", action="store_true",
                   help="include minerals / business personal property (default: real property only)")
    s.add_argument("--include-unmailable", action="store_true",
                   help="keep owners with no mailing address or a withheld name")

    s = sub.add_parser("serve", help="run the local web page")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8765)
    s.add_argument("--open", action="store_true", help="open the page in your browser")

    s = sub.add_parser("demo", help="generate synthetic data, build it, and serve")
    s.add_argument("--n", type=int, default=3000, help="number of synthetic parcels")
    s.add_argument("--no-serve", action="store_true")
    s.add_argument("--port", type=int, default=8765)
    return p


def cmd_inspect(layout, paths, lines: int):
    for name, data in db.iter_source_files(paths):
        kind = layout.kind_for(name) or "UNKNOWN"
        text = data.decode(layout.files[kind].encoding if kind in layout.files else "latin-1", errors="replace")
        recs = text.splitlines()
        lengths = collections.Counter(len(r) for r in recs if r.strip())
        delims = {d: sum(r.count(d) for r in recs[:50]) for d in ("|", ",", "\t", ";")}
        print(f"\n== {name}  [{kind}]  {len(data):,} bytes, {len(recs):,} lines")
        print(f"   record lengths (top): {lengths.most_common(4)}")
        print(f"   delimiter counts in first 50 lines: {delims}")
        if kind in layout.files:
            spec = layout.files[kind]
            want = max((f.start - 1 + f.length for f in spec.fields), default=0) if spec.format == "fixed" else None
            if want:
                print(f"   layout expects fixed width >= {want}")
        for r in recs[:lines]:
            print("   |" + r + "|")
        if recs and kind in layout.files:
            print("   parsed first record:")
            for k, v in next(iter(layout.files[kind].parse_lines(recs[:1])), {}).items():
                print(f"     {k:<16} {v!r}")
    if layout.notes:
        print(f"\nLayout note: {layout.notes}")


def cmd_build(conn, layout, args):
    w = Weights(**{k: getattr(args, f"w_{k}") for k in Weights().as_dict()})
    n = db.build(conn, layout, as_of=args.as_of, weights=w, long_held_years=args.long_held_years)
    s = queries.summary(conn)
    print(f"Built {n:,} accounts; real property: {s['parcels']:,} parcels, {s['delinquent']:,} delinquent "
          f"(${s['delinquent_due']:,.0f}), {s['out_of_state']:,} out-of-state, {s['estate']:,} estate/heirs, "
          f"{s['vacant']:,} vacant.")


def main(argv: Optional[List[str]] = None) -> int:
    args = _parser().parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(levelname)s %(message)s")
    layout = load_layout(args.layout)

    if args.cmd == "inspect":
        cmd_inspect(layout, args.paths, args.lines)
        return 0
    if args.cmd == "serve":
        from .web import serve
        serve(args.db, args.host, args.port, open_browser=args.open)
        return 0

    conn = db.connect(args.db)
    if args.cmd == "ingest":
        counts = db.ingest(conn, layout, args.paths, append=args.append)
        print("Loaded rows:", ", ".join(f"{k}={v:,}" for k, v in counts.items()) or "none")
        if "master" not in counts:
            print("WARNING: no master (MM/AM) file found; build needs one.", file=sys.stderr)
    elif args.cmd == "build":
        cmd_build(conn, layout, args)
    elif args.cmd == "enrich":
        print(f"Merged {db.load_enrichment(conn, args.csv):,} enrichment rows; now re-run `build`.")
    elif args.cmd == "export":
        db.ensure_favorites(conn)
        params = {"top_pct": args.top_pct, "min_score": args.min_score,
                  "delinquent": "1" if args.delinquent else None,
                  "favorites": "1" if args.favorites else None,
                  "real_property": None if args.all_types else "1",
                  "mailable": None if args.include_unmailable else "1"}
        params = {k: str(v) for k, v in params.items() if v is not None}
        with open(args.out, "w", newline="") as fh:
            w = csv.writer(fh)
            rows = 0
            for row in queries.iter_all(conn, params):
                w.writerow(row)
                rows += 1
        print(f"Wrote {rows - 1:,} parcels to {args.out}")
    elif args.cmd == "demo":
        z = sample.generate(layout, "demo_data/TaxRoll_DEMO.zip", n=args.n)
        db.ingest(conn, layout, [z])
        args.as_of, args.long_held_years = dt.date(2026, 9, 22), 10
        for k, v in Weights().as_dict().items():
            setattr(args, f"w_{k}", v)
        cmd_build(conn, layout, args)
        conn.close()
        if not args.no_serve:
            from .web import serve
            serve(args.db, "127.0.0.1", args.port, open_browser=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
