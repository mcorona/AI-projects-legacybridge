"""The ablation switches (ADR-013): `no-semantic` and `no-verify` remove a component and nothing
else, and the default behaviour is exactly what it was."""

import json
import pathlib

import pytest

from evals.golden import load_golden
from legacybridge.agent.backends import McpToolBackend
from legacybridge.agent.digest import digest
from legacybridge.agent.orchestrator import Agent
from legacybridge.agent.prompt_loader import PROMPTS, load_prompt, render_prompt
from legacybridge.agent.roles.sql_writer import tools_for
from legacybridge.agent.roles.verifier import verifier_tools
from legacybridge.agent.schemas import AgentConfig
from legacybridge.llm.base import ToolSpec
from legacybridge.mcp_server.tools import LegacyTools
from legacybridge.semantic.loader import empty_ontology, load_ontology
from tests.support.scripted import ScriptedLLM, call, plan

FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "prompts_phase5"
QUESTION = "¿Cuántas facturas activas hubo en 2004?"
PROMPT_FILES = ("planner.md", "sql_writer.md", "verifier.md", "verifier_write.md", "write_rules.md")


# --------------------------------------------------------------------------- prompts
def test_the_default_prompts_do_not_contain_a_single_marker():
    for name in PROMPT_FILES:
        text = load_prompt(name, semantic=True)
        assert "<!--" not in text and "-->" not in text, name


@pytest.mark.parametrize("name", PROMPT_FILES)
def test_the_default_prompts_are_exactly_what_they_were_before_the_markers(name):
    """The `full` arm must run the prompts of the earlier phases, byte for byte."""
    assert load_prompt(name, semantic=True) == (FIXTURES / name).read_text(encoding="utf-8")


def test_without_the_semantic_layer_no_prompt_refers_to_what_was_removed():
    for name in PROMPT_FILES:
        text = load_prompt(name, semantic=False)
        assert "<!--" not in text, name
        for gone in ("explain_business_term", "{digest}", "What the database contains", "warnings"):
            assert gone not in text, f"{name} still mentions {gone!r}"
    assert "known defects" in load_prompt("sql_writer.md", semantic=False), (
        "the general caution is not part of the ontology and stays"
    )


def test_the_ablation_prompt_stays_readable_where_a_sentence_was_cut():
    verifier = load_prompt("verifier.md", semantic=False)
    assert "the final SQL and its result." in verifier
    assert "says what the result says." in verifier
    assert "3. Call run_query with one SELECT.\n4." in load_prompt("sql_writer.md", semantic=False)


def test_render_handles_blocks_and_inline_passages_and_plain_glue():
    text = "a\n<!--semantic-->\nb\n<!--/semantic-->\nc <!--semantic-->d<!--/semantic--><!--plain-->e<!--/plain-->"
    assert render_prompt(text, True) == "a\nb\nc d"
    assert render_prompt(text, False) == "a\nc e\n"


def test_every_marker_is_closed():
    for name in PROMPT_FILES:
        raw = (PROMPTS / name).read_text(encoding="utf-8")
        for tag in ("semantic", "plain"):
            assert raw.count(f"<!--{tag}-->") == raw.count(f"<!--/{tag}-->"), (name, tag)


# --------------------------------------------------------------------------- tools and digest
def specs(*names):
    return [ToolSpec(n, "d", {"type": "object"}) for n in names]


def test_the_ontology_tool_is_hidden_from_the_writer_and_the_verifier_only_in_the_ablation():
    every = specs("list_tables", "describe_table", "sample_rows", "run_query",
                  "explain_business_term", "propose_write")  # fmt: skip
    assert "explain_business_term" in {s.name for s in tools_for(every, "query")}
    assert "explain_business_term" not in {s.name for s in tools_for(every, "query", False)}
    assert "propose_write" in {s.name for s in tools_for(every, "write", False)}
    assert "explain_business_term" in verifier_tools(True)
    assert verifier_tools(False) == {"describe_table", "run_query"}


def test_an_empty_ontology_has_nothing_to_say_and_the_real_one_does():
    assert digest(empty_ontology()) == ""
    assert "Canonical metrics" in digest(load_ontology())


# --------------------------------------------------------------------------- the agent
@pytest.mark.integration
def test_without_an_ontology_the_tools_raise_no_rule_feedback_on_any_golden_query(db_available):
    """The empty ontology must be safe to run every golden SQL under, naive ones included."""
    if not db_available:
        pytest.skip("database not reachable")
    tools = LegacyTools(ontology=empty_ontology())
    ran = 0
    for case in load_golden():
        for sql in filter(None, (case.expected_sql, case.naive_sql)):
            out = tools.run_query(sql, max_rows=5)
            if out["ok"]:
                ran += 1
                assert out["violations"] == [] and out["defects_touched"] == [], case.id
                assert not any(
                    w.startswith("FAC-") or w.startswith("SIC-") for w in out["warnings"]
                )
    assert ran > 50


@pytest.mark.integration
def test_describe_table_has_no_defect_notes_without_the_ontology(db_available):
    if not db_available:
        pytest.skip("database not reachable")
    with_ontology = LegacyTools().describe_table("faccab")
    without = LegacyTools(ontology=empty_ontology()).describe_table("faccab")
    assert with_ontology["ok"] and without["ok"]
    assert len(json.dumps(without)) < len(json.dumps(with_ontology))
    assert "FAC-" not in json.dumps(without)


@pytest.mark.integration
@pytest.mark.anyio
async def test_the_no_semantic_agent_runs_end_to_end_and_never_sees_the_ontology(db_available):
    if not db_available:
        pytest.skip("database not reachable")
    sql = "SELECT COUNT(DISTINCT numfac) AS n FROM faccab WHERE status = 'V'"
    llm = ScriptedLLM(
        planner=[plan("query")],
        writer=[call("run_query", sql=sql),
                call("submit_answer", answer="Hay algunas.", outcome="answer", evidence_query="q1")],
        verifier=[call("run_query", sql=sql), call("submit_verdict", agrees=True, confidence=0.9)],
    )  # fmt: skip
    async with McpToolBackend.in_memory(LegacyTools(ontology=empty_ontology())) as backend:
        agent = Agent(llm, backend, config=AgentConfig(semantic=False))
        result = await agent.ask(QUESTION)
    assert result.status == "answered" and result.defects_touched == []
    assert not any(s.family == "semantics" for s in result.swar_signals)
    for role in ("planner", "writer", "verifier"):
        messages, _ = llm.sent(role)[0]
        assert "explain_business_term" not in messages[0].content, role
        assert "Canonical metrics" not in messages[0].content, role
    assert "explain_business_term" not in llm.tool_names("writer") | llm.tool_names("verifier")
    assert {"run_query", "describe_table"} <= llm.tool_names("writer") | llm.tool_names("verifier")


@pytest.mark.integration
@pytest.mark.anyio
async def test_the_default_agent_is_untouched_by_the_new_switch(db_available):
    if not db_available:
        pytest.skip("database not reachable")
    agent_default = AgentConfig()
    assert agent_default.semantic is True and agent_default.verify is True
    llm = ScriptedLLM(planner=[plan("clarify", clarification="¿Qué periodo?")])
    async with McpToolBackend.in_memory() as backend:
        agent = Agent(llm, backend)
        assert agent.ontology.metrics and agent._digest
        assert (await agent.ask("¿Cuánto vendimos?")).status == "needs_clarification"
