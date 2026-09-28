"""MCP server: exploración del esquema legacy y reglas de negocio (solo metadatos).

Arranque por stdio: `python -m legacybridge.mcp_servers.schema_explorer`.
"""
from __future__ import annotations

import os
from pathlib import Path

import yaml
from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

ROOT = Path(__file__).resolve().parents[3]
DICT = yaml.safe_load((ROOT / "config" / "business_dictionary.yaml").read_text())
ALLOWED = set(DICT["allowed_tables"])

INSTRUCTIONS = (
    "Metadatos del ERP legacy (nombres crípticos, sin llaves foráneas, fechas como texto). "
    "Usa list_tables para orientarte, describe_table antes de escribir SQL y "
    "get_business_rule para convenciones (activo, pedido válido, moneda, fechas)."
)
READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False,
                            idempotent_hint=True, open_world_hint=False)


def _conn():
    import psycopg
    return psycopg.connect(os.environ["LB_DSN"])


def list_tables() -> dict:
    """Lista las tablas consultables con el concepto de negocio que representan."""
    return {"tables": [{"table": t, **DICT["tables"][t]} for t in sorted(ALLOWED)]}


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


def get_business_rule(term: str) -> dict:
    """Busca reglas, catálogos y convenciones del negocio (activo, pedido válido, moneda…)."""
    term = term.lower()
    hits = {k: v for k, v in DICT["rules"].items() if term in k or term in str(v).lower()}
    cats = {k: v for k, v in DICT["catalogs"].items() if term in k or term in str(v).lower()}
    return {"rules": hits, "catalogs": cats} if hits or cats else {"rules": DICT["rules"]}


def build_server() -> MCPServer:
    server = MCPServer(name="legacybridge-schema", instructions=INSTRUCTIONS)
    for fn in (list_tables, describe_table, get_business_rule):
        server.tool(annotations=READ_ONLY)(fn)
    return server


if __name__ == "__main__":
    build_server().run("stdio")
