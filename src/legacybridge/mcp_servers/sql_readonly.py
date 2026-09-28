"""MCP server: ejecución de SQL de solo lectura, siempre validada por sql_guard."""
from __future__ import annotations

import os
import time
from decimal import Decimal
from pathlib import Path

import yaml
from mcp.server.fastmcp import FastMCP

from legacybridge.guard.sql_guard import validate

ROOT = Path(__file__).resolve().parents[3]
ALLOWED = set(yaml.safe_load((ROOT / "config" / "business_dictionary.yaml").read_text())["allowed_tables"])

mcp = FastMCP("legacybridge-sql")


@mcp.tool()
def run_query(sql: str, max_rows: int = 100) -> dict:
    """Valida y ejecuta una consulta SELECT sobre el sistema legacy (rol de solo lectura).

    Devuelve la SQL normalizada que realmente se ejecutó, columnas, filas y tablas usadas.
    Si el guard la rechaza, devuelve `rejected` con la razón para que el agente se corrija.
    """
    g = validate(sql, ALLOWED, max_limit=min(max_rows, 200))
    if not g.ok:
        return {"rejected": True, "reason": g.reason}
    import psycopg

    t0 = time.perf_counter()
    with psycopg.connect(os.environ["LB_DSN"]) as c:
        c.read_only = True
        cur = c.execute(g.sql)
        cols = [d.name for d in cur.description]
        rows = [[float(v) if isinstance(v, Decimal) else v for v in r] for r in cur.fetchall()]
    return {"rejected": False, "sql": g.sql, "tables": list(g.tables), "columns": cols,
            "rows": rows, "row_count": len(rows), "ms": round((time.perf_counter() - t0) * 1000, 1)}


if __name__ == "__main__":
    mcp.run()
