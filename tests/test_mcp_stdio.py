"""Extremo a extremo: arranca cada server de `.mcp.json` por stdio, como lo hace Claude Code."""
import json
import os
from pathlib import Path

import anyio
import pytest
from mcp import Client, StdioServerParameters

ROOT = Path(__file__).resolve().parents[1]
CONFIG = json.loads((ROOT / ".mcp.json").read_text())["mcpServers"]
EXPECTED_TOOLS = {
    "legacybridge-schema": {"list_tables", "describe_table", "find_columns", "get_business_rule",
                            "search_knowledge"},
    "legacybridge-sql": {"run_query"},
}
PROBE = {
    "legacybridge-schema": ("describe_table", {"table": "pedenc"}),
    "legacybridge-sql": ("run_query", {"sql": "SELECT COUNT(*) FROM cliemae WHERE cliact = 'S' "
                                              "AND clicve IN ('C00001','C00002','C00003')"}),
}


def test_mcp_json_registers_both_servers():
    assert set(CONFIG) == set(EXPECTED_TOOLS)
    for cfg in CONFIG.values():
        assert cfg["args"][0] == "-m" and cfg["env"]["LB_DSN"].startswith("postgresql://lb_ro:")


@pytest.mark.integration
@pytest.mark.parametrize("name", sorted(EXPECTED_TOOLS))
def test_server_over_stdio(name):
    cfg = CONFIG[name]
    params = StdioServerParameters(command=str(ROOT / cfg["command"]), args=cfg["args"],
                                   env={**os.environ, **cfg["env"]}, cwd=str(ROOT))
    tool, args = PROBE[name]

    async def main():
        async with Client(params, read_timeout_seconds=30) as c:
            tools = {t.name for t in (await c.list_tools()).tools}
            return tools, await c.call_tool(tool, args)

    tools, res = anyio.run(main)
    assert tools == EXPECTED_TOOLS[name]
    out = json.loads(res.content[0].text)
    if out.get("stage") == "connection" or "db_error" in out:
        pytest.skip("Postgres legacy no disponible (make db)")
    if name == "legacybridge-sql":
        assert out["ok"] and out["rows"] == [[1]]
    else:
        assert out["columns"][2]["type"] == "character varying(8)"
