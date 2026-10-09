"""Canonical result-set hash (ADR-011).

Two result sets that mean the same thing must hash the same: columns are compared by position,
strings lose their padding, numbers are rendered at a fixed scale and rows are sorted unless the
query has a top-level ORDER BY. A text value and a number with the same rendering hash alike on
purpose, so a cast difference is not counted as an error.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, time
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

import sqlglot

SCALE = Decimal("0.0001")


def has_top_level_order(sql: str) -> bool:
    """True when the outermost query of ``sql`` has an ORDER BY (inner ones do not count)."""
    return sqlglot.parse_one(sql, read="postgres").args.get("order") is not None


def canonical_value(value: Any) -> Any:
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int | float | Decimal):
        number = Decimal(str(value)) if isinstance(value, float) else Decimal(value)
        rounded = number.quantize(SCALE, ROUND_HALF_UP)
        return "0" if rounded == 0 else format(rounded.normalize(), "f")
    if isinstance(value, datetime | date | time):
        return value.isoformat()
    if isinstance(value, bytes | memoryview):
        return bytes(value).hex()
    if isinstance(value, list | tuple):
        return [canonical_value(item) for item in value]
    return str(value).strip()


def _dump(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def result_hash(rows: list[tuple], *, ordered: bool) -> str:
    canonical = [[canonical_value(v) for v in row] for row in rows]
    if not ordered:
        canonical.sort(key=_dump)
    return "sha256:" + hashlib.sha256(_dump(canonical).encode("utf-8")).hexdigest()


def run_and_hash(conn, sql: str) -> str:
    """Execute ``sql`` on ``conn`` (a ``lb_reader`` connection) and hash its result set."""
    with conn.cursor() as cur:
        cur.execute(sql)
        rows = cur.fetchall()
    return result_hash(rows, ordered=has_top_level_order(sql))
