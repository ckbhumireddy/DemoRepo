"""Tiny local web server (stdlib only) for browsing the scored parcels.

GET /                    the single-page browser (static/index.html)
GET /api/summary         counts + filter option lists
GET /api/parcels?...     filtered page of parcels (see queries.build_where)
GET /api/parcel/<acct>   one parcel with its receivable rows
GET /api/parcels.csv?... every matching parcel as CSV (mail-list export)
POST /api/favorite/<acct> {"favorite": true|false, "note": "..."}
"""

from __future__ import annotations

import csv
from contextlib import closing
import io
import json
import logging
import pathlib
import sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qsl, unquote, urlparse

from . import db, queries

log = logging.getLogger(__name__)
STATIC = pathlib.Path(__file__).resolve().parent / "static"


def make_handler(db_path: str):
    # A proper file: URI so Windows paths (drive letters, spaces, "#") open read-only.
    db_file = str(pathlib.Path(db_path).resolve())
    db_uri = pathlib.Path(db_file).as_uri() + "?mode=ro"     # reads; favorites write via db_file

    class Handler(BaseHTTPRequestHandler):
        def _conn(self):
            conn = sqlite3.connect(db_uri, uri=True)
            conn.row_factory = sqlite3.Row
            return conn

        def _send(self, status: int, body: bytes, ctype: str, extra=None):
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj, status=200):
            self._send(status, json.dumps(obj, default=str).encode(), "application/json")

        def do_GET(self):  # noqa: N802
            url = urlparse(self.path)
            params = dict(parse_qsl(url.query))
            try:
                if url.path in ("/", "/index.html"):
                    self._send(200, (STATIC / "index.html").read_bytes(), "text/html; charset=utf-8")
                elif url.path == "/api/summary":
                    with closing(self._conn()) as c:
                        self._json(queries.summary(c))
                elif url.path == "/api/parcels":
                    limit = max(1, min(int(params.get("limit", 100)), 1000))
                    offset = max(0, int(params.get("offset", 0)))
                    with closing(self._conn()) as c:
                        self._json(queries.search(c, params, limit=limit, offset=offset))
                elif url.path.startswith("/api/parcel/"):
                    with closing(self._conn()) as c:
                        d = queries.detail(c, unquote(url.path[len("/api/parcel/"):]))
                    self._json(d, 200 if d else 404)
                elif url.path == "/api/parcels.csv":
                    buf = io.StringIO()
                    w = csv.writer(buf)
                    with closing(self._conn()) as c:
                        for row in queries.iter_all(c, params):
                            w.writerow(row)
                    self._send(200, buf.getvalue().encode(), "text/csv; charset=utf-8",
                               {"Content-Disposition": 'attachment; filename="parcels.csv"'})
                else:
                    self._json({"error": "not found"}, 404)
            except (ValueError, sqlite3.Error) as exc:
                log.exception("request failed: %s", self.path)
                self._json({"error": str(exc)}, 400)

        def do_POST(self):  # noqa: N802
            url = urlparse(self.path)
            if not url.path.startswith("/api/favorite/"):
                return self._json({"error": "not found"}, 404)
            # JSON only: a cross-site form can't send it without a CORS
            # preflight, which this server never approves.
            if not (self.headers.get("Content-Type") or "").startswith("application/json"):
                return self._json({"error": "expected application/json"}, 415)
            try:
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
                account = unquote(url.path[len("/api/favorite/"):])
                note = body.get("note")
                with closing(sqlite3.connect(db_file)) as c:
                    if not c.execute("SELECT 1 FROM parcels WHERE account = ?", (account,)).fetchone():
                        return self._json({"error": "unknown account"}, 404)
                    db.set_favorite(c, account, bool(body.get("favorite", True)),
                                    note.strip() if isinstance(note, str) else None)
                    count = c.execute("SELECT COUNT(*) FROM favorites").fetchone()[0]
                self._json({"account": account, "favorite": bool(body.get("favorite", True)), "favorites": count})
            except (ValueError, sqlite3.Error) as exc:
                log.exception("request failed: %s", self.path)
                self._json({"error": str(exc)}, 400)

        def log_message(self, fmt, *args):
            log.debug("%s - %s", self.address_string(), fmt % args)

    return Handler


def serve(db_path: str, host: str = "127.0.0.1", port: int = 8765, open_browser: bool = False):
    if not pathlib.Path(db_path).exists():
        raise SystemExit(f"database not found: {db_path} (run ingest + build first, or --demo)")
    with closing(sqlite3.connect(db_path)) as c:      # databases built before favorites existed
        db.ensure_favorites(c)
    server = ThreadingHTTPServer((host, port), make_handler(str(pathlib.Path(db_path).resolve())))
    url = f"http://{host}:{port}/"
    print(f"Browse parcels at {url}  (keep this window open; Ctrl+C to stop)")
    if open_browser:
        import webbrowser
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
