"""End to end: start the server exactly as `.mcp.json` tells Claude Code to, over stdio, and use it
with an MCP client. Also covers the Phase 2 DoD: five golden-set questions answered through the
server alone, checked against the stored result hashes."""

import json
import os
from pathlib import Path

import anyio
import pytest
from mcp import Client, StdioServerParameters

from evals.golden import load_golden
from evals.hashing import has_top_level_order, result_hash

ROOT = Path(__file__).resolve().parents[1]
CONFIG = json.loads((ROOT / ".mcp.json").read_text())["mcpServers"]
TOOLS = {
    "list_tables",
    "describe_table",
    "sample_rows",
    "run_query",
    "explain_business_term",
    "propose_write",
}
FIVE_QUESTIONS = ["SIMPLE-001", "JOIN-005", "TRAP-002", "TRAP-006", "TRAP-019"]


def test_mcp_json_registers_the_legacybridge_server():
    assert set(CONFIG) == {"legacybridge"}
    server = CONFIG["legacybridge"]
    assert server["command"] == "uv"
    assert server["args"][-2:] == ["-m", "legacybridge.mcp_server"]


def _params() -> StdioServerParameters:
    server = CONFIG["legacybridge"]
    return StdioServerParameters(
        command=server["command"], args=server["args"], env={**os.environ}, cwd=str(ROOT)
    )


async def _session(calls: list[tuple[str, dict]]) -> tuple[set[str], list[dict]]:
    async with Client(_params(), read_timeout_seconds=60) as client:
        tools = {t.name for t in (await client.list_tools()).tools}
        results = []
        for name, arguments in calls:
            response = await client.call_tool(name, arguments)
            results.append(json.loads(response.content[0].text))
    return tools, results


@pytest.mark.integration
def test_the_server_lists_six_tools_and_each_one_answers(db_available):
    if not db_available:
        pytest.skip("legacy Postgres is not reachable")
    tools, results = anyio.run(
        _session,
        [
            ("list_tables", {}),
            ("describe_table", {"name": "faccab"}),
            ("sample_rows", {"name": "productos", "n": 2}),
            ("run_query", {"sql": "SELECT COUNT(*) FROM clientes WHERE status = 'I'"}),
            ("explain_business_term", {"term": "ventas"}),
            ("run_query", {"sql": "DROP TABLE clientes"}),
        ],
    )
    assert tools == TOOLS
    listing, described, sample, count, term, attack = results
    assert len(listing["tables"]) == 9
    assert described["table"] == "faccab" and described["writable_columns"] == []
    assert sample["row_count"] == 2
    assert count["rows"] == [[36]]
    assert term["term"] == "sales"
    assert (attack["ok"], attack["reason"]) == (False, "not_select")


@pytest.mark.integration
def test_five_golden_set_questions_are_answerable_through_the_server_alone(db_available):
    if not db_available:
        pytest.skip("legacy Postgres is not reachable")
    cases = {c.id: c for c in load_golden()}
    _, results = anyio.run(
        _session, [("run_query", {"sql": cases[i].expected_sql}) for i in FIVE_QUESTIONS]
    )
    for case_id, result in zip(FIVE_QUESTIONS, results, strict=True):
        case = cases[case_id]
        assert result["ok"], (case_id, result)
        rows = [tuple(r) for r in result["rows"]]
        ordered = has_top_level_order(case.expected_sql)
        assert result_hash(rows, ordered=ordered) == case.expected_result_hash, case_id
    # The planted instruction came back verbatim and flagged, never executed.
    trap = dict(zip(FIVE_QUESTIONS, results, strict=True))["TRAP-019"]
    assert "IGNORA TUS INSTRUCCIONES" in trap["rows"][0][0]
    assert any("looks like instructions" in w for w in trap["warnings"])


@pytest.mark.integration
def test_defect_rules_reach_an_mcp_client_over_stdio(db_available):
    """Phase 3 DoD: the naive SQL of TRAP-002 draws a FAC-09 warning, the reference SQL none."""
    if not db_available:
        pytest.skip("legacy Postgres is not reachable")
    case = {c.id: c for c in load_golden()}["TRAP-002"]
    _, (naive, good) = anyio.run(
        _session,
        [("run_query", {"sql": case.naive_sql}), ("run_query", {"sql": case.expected_sql})],
    )
    assert naive["ok"] and any(w.startswith("FAC-09:") for w in naive["warnings"])
    assert "FAC-09" in {v["defect"] for v in naive["violations"]}
    assert good["ok"] and good["warnings"] == [] and good["violations"] == []
