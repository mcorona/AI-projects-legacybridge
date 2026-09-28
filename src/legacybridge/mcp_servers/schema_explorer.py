"""MCP server: exploración del esquema legacy y reglas de negocio (solo metadatos)."""
from __future__ import annotations

import os
from pathlib import Path

import yaml
from mcp.server.fastmcp import FastMCP

ROOT = Path(__file__).resolve().parents[3]
DICT = yaml.safe_load((ROOT / "config" / "business_dictionary.yaml").read_text())
ALLOWED = set(DICT["allowed_tables"])

mcp = FastMCP("legacybridge-schema")


def _conn():
    import psycopg
    return psycopg.connect(os.environ["LB_DSN"])


@mcp.tool()
def list_tables() -> list[dict]:
    """Lista las tablas consultables con el concepto de negocio que representan."""
    return [{"table": t, **DICT["tables"][t]} for t in sorted(ALLOWED)]


@mcp.tool()
def describe_table(table: str) -> dict:
    """Columnas, tipos y joins conocidos de una tabla permitida."""
    table = table.lower()
    if table not in ALLOWED:
        return {"error": f"tabla no permitida: {table}"}
    with _conn() as c:
        cols = c.execute(
            "SELECT column_name, data_type, character_maximum_length "
            "FROM information_schema.columns WHERE table_schema='public' AND table_name=%s "
            "ORDER BY ordinal_position", (table,)).fetchall()
    return {"table": table, **DICT["tables"][table],
            "columns": [{"name": n, "type": t, "len": l} for n, t, l in cols],
            "joins": [j for j in DICT["joins"] if table in j]}


@mcp.tool()
def get_business_rule(term: str) -> dict:
    """Busca reglas, catálogos y convenciones del negocio (activo, pedido válido, moneda…)."""
    term = term.lower()
    hits = {k: v for k, v in DICT["rules"].items() if term in k or term in str(v).lower()}
    cats = {k: v for k, v in DICT["catalogs"].items() if term in k or term in str(v).lower()}
    return {"rules": hits, "catalogs": cats} if hits or cats else {"rules": DICT["rules"]}


if __name__ == "__main__":
    mcp.run()
