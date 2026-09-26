"""County tax-roll file layouts: which file is which, and how to split a record.

A layout is a JSON file (see ``layouts/denton.json``) that describes, for each
file kind (master / receivable / statistic / tax_unit):

* ``prefixes``  - file-name prefixes that identify the kind (``MM``/``AM`` ...)
* ``format``    - ``fixed`` (start/length columns, 1-based) or ``delimited``
* ``fields``    - column names, plus position and type for fixed-width

and a ``canonical`` map from the names the rest of the app uses
(``owner_name``, ``total_due`` ...) to that county's raw column names. Adding a
county means writing one of these files, not changing code.
"""

from __future__ import annotations

import csv
import datetime as dt
import json
import pathlib
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Dict, Iterable, Iterator, List, Optional

LAYOUT_DIR = pathlib.Path(__file__).resolve().parent / "layouts"
FILE_KINDS = ("master", "receivable", "statistic", "tax_unit")


class LayoutError(ValueError):
    pass


@dataclass(frozen=True)
class Field:
    name: str
    start: int = 0              # 1-based; fixed-width only
    length: int = 0
    type: str = "str"           # str | int | decimal | date
    implied_decimals: int = 0
    date_format: str = "%Y%m%d"

    def convert(self, raw: str):
        text = raw.strip()
        if self.type == "str":
            return text
        if not text:
            return None
        try:
            if self.type == "int":
                return int(Decimal(text.replace(",", "")))
            if self.type == "decimal":
                value = Decimal(text.replace(",", "").replace("$", ""))
                if self.implied_decimals and "." not in text:
                    value = value.scaleb(-self.implied_decimals)
                return float(value)
            if self.type == "date":
                if text.strip("0") == "":
                    return None
                d = dt.datetime.strptime(text, self.date_format).date()
                # Placeholder dates: Denton uses 01/01/9999 for blank, and
                # 1899/1900 show up as junk deed dates.
                return None if d.year >= 9999 or d.year <= 1900 else d.isoformat()
        except (InvalidOperation, ValueError):
            return None
        raise LayoutError(f"unknown field type {self.type!r} for {self.name}")


@dataclass
class FileSpec:
    kind: str
    prefixes: List[str]
    format: str
    fields: List[Field]
    delimiter: str = "|"
    header: bool = False
    encoding: str = "latin-1"

    @property
    def names(self) -> List[str]:
        return [f.name for f in self.fields]

    def parse_lines(self, lines: Iterable[str]) -> Iterator[Dict[str, object]]:
        if self.format == "fixed":
            yield from self._parse_fixed(lines)
        elif self.format == "delimited":
            yield from self._parse_delimited(lines)
        else:
            raise LayoutError(f"{self.kind}: unknown format {self.format!r}")

    def _parse_fixed(self, lines):
        for line in lines:
            line = line.rstrip("\r\n")
            if not line.strip():
                continue
            yield {f.name: f.convert(line[f.start - 1:f.start - 1 + f.length])
                   for f in self.fields}

    def _parse_delimited(self, lines):
        reader = csv.reader(lines, delimiter=self.delimiter)
        names = self.names
        by_name = {f.name: f for f in self.fields}
        if self.header:
            header = [h.strip() for h in next(reader, [])]
            names = header
        for row in reader:
            if not any(cell.strip() for cell in row):
                continue
            rec = {}
            for name, cell in zip(names, row):
                spec = by_name.get(name)
                rec[name] = spec.convert(cell) if spec else cell.strip()
            for name in self.names:          # keep a stable column set
                rec.setdefault(name, None)
            yield {k: rec[k] for k in self.names}

    def format_record(self, values: Dict[str, object]) -> str:
        """Inverse of parsing for fixed-width; used to write demo fixtures."""
        if self.format != "fixed":
            return self.delimiter.join("" if values.get(n) is None else str(values[n])
                                       for n in self.names)
        width = max(f.start - 1 + f.length for f in self.fields)
        buf = [" "] * width
        for f in self.fields:
            text = _render(f, values.get(f.name))[:f.length]
            if f.type in ("int", "decimal") and text:
                text = text.rjust(f.length, "0")
            buf[f.start - 1:f.start - 1 + f.length] = list(text.ljust(f.length))
        return "".join(buf)


def _render(f: Field, value) -> str:
    if value is None:
        return ""
    if f.type == "decimal" and f.implied_decimals:
        return str(int(round(float(value) * 10 ** f.implied_decimals)))
    if f.type == "date":
        return dt.date.fromisoformat(str(value)).strftime(f.date_format)
    return str(value)


@dataclass
class Layout:
    county: str
    state: str
    files: Dict[str, FileSpec]
    canonical: Dict[str, Dict[str, str]]
    homestead_pattern: str = r"\bHS\b"
    extras: Dict[str, object] = field(default_factory=dict)   # code tables, city rules...
    path: Optional[pathlib.Path] = None
    notes: str = field(default="", repr=False)

    def kind_for(self, filename: str) -> Optional[str]:
        """Classify an extracted file by its name prefix (MM, AR, TU ...)."""
        base = pathlib.PurePath(filename).name.upper()
        # Longest prefix first so a hypothetical "MSX" beats "MS".
        candidates = sorted(((p.upper(), k) for k, s in self.files.items() for p in s.prefixes),
                            key=lambda pk: -len(pk[0]))
        for prefix, kind in candidates:
            if base.startswith(prefix):
                return kind
        return None


def load_layout(name_or_path: str = "denton") -> Layout:
    path = pathlib.Path(name_or_path)
    if not path.suffix:
        path = LAYOUT_DIR / f"{name_or_path}.json"
    try:
        doc = json.loads(path.read_text())
    except FileNotFoundError as exc:
        raise LayoutError(f"layout not found: {path}") from exc
    encoding = doc.get("encoding", "latin-1")
    files = {}
    for kind, spec in doc.get("files", {}).items():
        if kind not in FILE_KINDS:
            raise LayoutError(f"unknown file kind {kind!r} (expected one of {FILE_KINDS})")
        fields = [Field(**f) for f in spec["fields"]]
        fmt = spec.get("format", "fixed")
        if fmt == "fixed":
            for f in fields:
                if f.start < 1 or f.length < 1:
                    raise LayoutError(f"{kind}.{f.name}: fixed-width fields need start>=1, length>=1")
        files[kind] = FileSpec(kind=kind, prefixes=spec["prefixes"], format=fmt, fields=fields,
                               delimiter=spec.get("delimiter", "|"), header=spec.get("header", False),
                               encoding=encoding)
    canonical = doc.get("canonical", {})
    for kind, mapping in canonical.items():
        known = set(files[kind].names) if kind in files else set()
        missing = [raw for raw in mapping.values() if raw not in known]
        if missing:
            raise LayoutError(f"canonical.{kind} refers to unknown fields: {missing}")
    return Layout(county=doc.get("county", path.stem), state=doc.get("state", "TX"),
                  files=files, canonical=canonical,
                  homestead_pattern=doc.get("homestead_exemption_pattern", r"\bHS\b"),
                  extras={k: v for k, v in doc.items() if k not in ("files", "canonical")},
                  path=path, notes=doc.get("_status", ""))
