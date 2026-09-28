"""Contrato MCP de ambos servers, probado en memoria con `mcp.Client` (sin stdio ni BD)."""
import json

import anyio
from mcp import Client

from legacybridge.mcp_servers import schema_explorer, sql_readonly


def _run(server, fn):
    async def main():
        async with Client(server) as c:
            return await fn(c)
    return anyio.run(main)


def _tools(server) -> dict:
    return {t.name: t for t in _run(server, lambda c: c.list_tools()).tools}


def _call(server, name: str, args: dict) -> dict:
    res = _run(server, lambda c: c.call_tool(name, args))
    assert not res.is_error, res.content
    assert len(res.content) == 1, "cada tool debe devolver un solo objeto JSON"
    return json.loads(res.content[0].text)


def _assert_read_only(tools: dict):
    for t in tools.values():
        a = t.annotations
        assert a.read_only_hint and not a.destructive_hint and not a.open_world_hint, t.name
        assert t.description, f"{t.name} sin descripción"


def test_schema_server_exposes_read_only_tools():
    tools = _tools(schema_explorer.build_server())
    assert set(tools) == {"list_tables", "describe_table", "find_columns", "get_business_rule",
                          "search_knowledge"}
    _assert_read_only(tools)
    assert tools["describe_table"].input_schema["required"] == ["table"]
    fc = tools["find_columns"].input_schema
    assert fc["required"] == ["concept"] and fc["properties"]["limit"]["default"] == 8
    assert fc["properties"]["limit"]["maximum"] == 25
    assert fc["properties"]["concept"]["description"]
    sk = tools["search_knowledge"].input_schema
    assert sk["required"] == ["query"] and sk["properties"]["k"]["maximum"] == 10


def test_schema_server_tools_delegate_to_explorer():
    server = schema_explorer.build_server(schema_explorer.SchemaExplorer(db_columns=None))
    out = _call(server, "find_columns", {"concept": "moneda", "limit": 1})
    assert [(m["table"], m["column"]) for m in out["matches"]] == [("pedenc", "pedmon")]
    assert _call(server, "get_business_rule", {"term": "pedido válido"})["matched"] is True
    assert _call(server, "describe_table", {"table": "usupwd"})["error"] == "table_not_allowed"


def test_search_knowledge_rejects_unknown_kind_at_schema_level():
    server = schema_explorer.build_server(schema_explorer.SchemaExplorer(search_fn=lambda *a: []))
    res = _run(server, lambda c: c.call_tool("search_knowledge", {"query": "x", "kinds": ["usupwd"]}))
    assert res.is_error


def test_sql_server_exposes_only_run_query():
    tools = _tools(sql_readonly.build_server())
    assert set(tools) == {"run_query"}
    _assert_read_only(tools)
    assert tools["run_query"].input_schema["required"] == ["sql"]


def test_list_tables_never_exposes_sensitive_tables():
    out = _call(schema_explorer.build_server(), "list_tables", {})
    names = {t["table"] for t in out["tables"]}
    assert "cliemae" in names
    assert not names & {"usupwd", "ctrlhis"}


def test_run_query_rejects_without_touching_db():
    out = _call(sql_readonly.build_server(), "run_query", {"sql": "DELETE FROM cliemae"})
    assert out["rejected"] is True and out["reason"].startswith("not_select")
