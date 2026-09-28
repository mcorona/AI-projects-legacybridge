"""Herramientas del agente: mismas clases que los MCP servers, ejecutadas in-process (ADR-004).

Las especificaciones (descripción + JSON Schema) se obtienen de los propios MCP servers en
memoria, así el agente ve exactamente el mismo contrato que Claude Code o MCP Inspector.
"""
from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass

import anyio
from mcp import Client

from legacybridge.mcp_servers.schema_explorer import SchemaExplorer
from legacybridge.mcp_servers.schema_explorer import build_server as build_schema_server
from legacybridge.mcp_servers.sql_readonly import ReadOnlyExecutor
from legacybridge.mcp_servers.sql_readonly import build_server as build_sql_server

SUBMIT_ANSWER = {
    "name": "submit_answer",
    "description": (
        "Entrega la respuesta final al usuario. Llámala SIEMPRE para terminar. "
        "outcome='answer' si respondiste con datos de run_query; 'refusal' si la petición "
        "está fuera de lo permitido (modificar datos, credenciales, tablas restringidas); "
        "'cannot_answer' si los datos o el esquema no permiten responder."),
    "parameters": {
        "type": "object",
        "properties": {
            "answer": {"type": "string", "description": "Respuesta en español, con las cifras de la evidencia"},
            "outcome": {"type": "string", "enum": ["answer", "refusal", "cannot_answer"]},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1,
                           "description": "Confianza en que la respuesta es correcta y completa"},
            "caveats": {"type": "array", "items": {"type": "string"},
                        "description": "Advertencias: huérfanos, monedas mezcladas, datos truncados, supuestos"},
        },
        "required": ["answer", "outcome", "confidence"],
    },
}


@dataclass
class ToolBox:
    """Especificaciones neutras + funciones in-process que las ejecutan."""
    specs: list[dict]
    handlers: dict[str, Callable[[dict], dict]]

    def names(self) -> list[str]:
        return [s["name"] for s in self.specs]


def mcp_tool_specs(*servers) -> list[dict]:
    """Lee `tools/list` de cada MCP server en memoria y lo pasa al formato neutro del router."""
    async def collect():
        specs = []
        for server in servers:
            async with Client(server) as c:
                for t in (await c.list_tools()).tools:
                    specs.append({"name": t.name, "description": (t.description or "").strip(),
                                  "parameters": t.input_schema})
        return specs
    return anyio.run(collect)


def build_toolbox(explorer: SchemaExplorer | None = None,
                  executor: ReadOnlyExecutor | None = None) -> ToolBox:
    ex = explorer or SchemaExplorer()
    sql = executor or ReadOnlyExecutor()
    handlers: dict[str, Callable[[dict], dict]] = {
        "list_tables": lambda a: ex.list_tables(),
        "describe_table": lambda a: ex.describe_table(a["table"]),
        "find_columns": lambda a: ex.find_columns(a["concept"], int(a.get("limit", 8))),
        "get_business_rule": lambda a: ex.get_business_rule(a["term"]),
        "search_knowledge": lambda a: ex.search_knowledge(a["query"], int(a.get("k", 5)), a.get("kinds")),
        "run_query": lambda a: sql.run(a["sql"], int(a.get("max_rows", 100))),
    }
    specs = mcp_tool_specs(build_schema_server(ex), build_sql_server(sql)) + [SUBMIT_ANSWER]
    missing = {s["name"] for s in specs} - set(handlers) - {"submit_answer"}
    if missing:   # un tool nuevo en un MCP server sin handler en el agente
        raise RuntimeError(f"tools sin handler en el agente: {sorted(missing)}")
    return ToolBox(specs, handlers)


def to_json(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)
