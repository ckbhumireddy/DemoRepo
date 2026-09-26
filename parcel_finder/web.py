"""Tiny local web server (stdlib only) for browsing the scored parcels.

GET /                    the single-page browser (static/index.html)
GET /api/summary         counts + filter option lists
GET /api/parcels?...     filtered page of parcels (see queries.build_where)
GET /api/parcel/<acct>   one parcel with its receivable rows
GET /api/parcels.csv?... every matching parcel as CSV (mail-list export)
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

from . import queries

log = logging.getLogger(__name__)
STATIC = pathlib.Path(__file__).resolve().parent / "static"


def make_handler(db_path: str):
    class Handler(BaseHTTPRequestHandler):
        def _conn(self):
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
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

        def log_message(self, fmt, *args):
            log.debug("%s - %s", self.address_string(), fmt % args)

    return Handler


def serve(db_path: str, host: str = "127.0.0.1", port: int = 8765):
    if not pathlib.Path(db_path).exists():
        raise SystemExit(f"database not found: {db_path} (run ingest + build first, or --demo)")
    server = ThreadingHTTPServer((host, port), make_handler(str(pathlib.Path(db_path).resolve())))
    print(f"Browse parcels at http://{host}:{port}/  (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
