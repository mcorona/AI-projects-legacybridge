"""Convert database values to JSON-friendly ones."""

from __future__ import annotations

import base64
import datetime as dt
import uuid
from decimal import Decimal
from typing import Any


def to_json_value(v: Any) -> Any:
    """Convert psycopg types to JSON without losing what matters."""
    if isinstance(v, Decimal):
        return int(v) if v == v.to_integral_value() and v.is_finite() else float(v)
    if isinstance(v, dt.date | dt.datetime | dt.time):
        return v.isoformat()
    if isinstance(v, dt.timedelta | uuid.UUID):
        return str(v)
    if isinstance(v, bytes | bytearray | memoryview):
        return {"base64": base64.b64encode(bytes(v)).decode()}
    if isinstance(v, list | tuple):
        return [to_json_value(x) for x in v]
    if isinstance(v, dict):
        return {k: to_json_value(x) for k, x in v.items()}
    return v
