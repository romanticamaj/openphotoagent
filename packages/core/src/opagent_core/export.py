"""CSV export (OUT-EXE-021): RFC 4180 quoting, formula-injection guard, optional UTF-8 BOM."""

from __future__ import annotations

import csv
import io

_FORMULA_PREFIX = ("=", "+", "-", "@")


def _safe(v: object) -> str:
    s = "" if v is None else str(v)
    return "'" + s if s.startswith(_FORMULA_PREFIX) else s


def to_csv(rows: list[dict[str, object]], columns: list[str], bom: bool = False) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\r\n", quoting=csv.QUOTE_MINIMAL)
    w.writerow(columns)
    for r in rows:
        w.writerow([_safe(r.get(c)) for c in columns])
    return ("﻿" if bom else "") + buf.getvalue()
