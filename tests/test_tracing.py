"""OpenTelemetry spans of an agent run (ADR-007), read back from an in-memory exporter."""

import time

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import NoOpTracer, StatusCode

from legacybridge.agent.backends import McpToolBackend
from legacybridge.agent.orchestrator import Agent
from legacybridge.agent.schemas import AgentConfig
from legacybridge.llm.base import LLMUnavailable
from legacybridge.observability import attrs as A
from legacybridge.observability.tracing import (
    build_provider,
    capture_content,
    tracing_enabled,
    truncate,
)
from legacybridge.semantic.loader import load_ontology
from tests.support.scripted import ScriptedLLM, call, plan

pytestmark = pytest.mark.anyio
QUESTION = "¿Cuántas facturas activas hubo en 2004?"
METRIC = load_ontology().render_metric("facturas_vigentes", year=2004)


@pytest.fixture(scope="module")
def exporter():
    exp = InMemorySpanExporter()
    current = trace.get_tracer_provider()
    if not isinstance(current, TracerProvider):
        current = TracerProvider()
        trace.set_tracer_provider(current)
    current.add_span_processor(SimpleSpanProcessor(exp))
    return exp


@pytest.fixture
def spans(exporter):
    exporter.clear()
    return exporter


def happy() -> ScriptedLLM:
    return ScriptedLLM(
        planner=[plan("query")],
        writer=[
            call("run_query", sql=METRIC),
            call("submit_answer", answer="Hubo 829 facturas en 2004.", outcome="answer",
                 evidence_query="q1", assumptions=["una por folio"]),
        ],
        verifier=[call("run_query", sql=METRIC), call("submit_verdict", agrees=True, confidence=0.9)],
    )  # fmt: skip


async def ask(llm, tools, config=None, **kw):
    async with McpToolBackend.in_memory(tools) as backend:
        return await Agent(llm, backend, config=config, **kw).ask(QUESTION)


def named(finished, prefix):
    return [s for s in finished if s.name.startswith(prefix)]


def parent_of(span, finished):
    return next((s for s in finished if s.context.span_id == span.parent.span_id), None)


def ancestors(span, finished):
    out = []
    while (span := parent_of(span, finished) if span.parent else None) is not None:
        out.append(span)
    return out


# --------------------------------------------------------------------------- the tree
@pytest.mark.integration
async def test_a_run_is_one_trace_with_the_expected_tree(tools, spans):
    result = await ask(happy(), tools)
    finished = spans.get_finished_spans()
    root = named(finished, "invoke_agent legacybridge")[0]
    assert root.parent is None and result.trace_id == format(root.context.trace_id, "032x")
    # Everything the question did is one trace. The only span outside it is the MCP connection
    # handshake, which happens when the backend opens, before the question exists.
    outside = {s.name for s in finished if s.context.trace_id != root.context.trace_id}
    assert outside <= {"server/discover"}, outside
    finished = [s for s in finished if s.context.trace_id == root.context.trace_id]
    roles = {s.attributes[A.LB_ROLE]: s for s in finished if A.LB_ROLE in s.attributes
             and s.name.startswith("invoke_agent ")}  # fmt: skip
    assert set(roles) == {"planner", "sql_writer", "verifier"}
    assert all(parent_of(r, finished) is root for r in roles.values())

    chats = named(finished, "chat ")
    tools_ = named(finished, "execute_tool ")
    assert len(chats) == 5 and len(tools_) == 2 == result.usage.tool_calls
    for chat in chats:
        assert parent_of(chat, finished).attributes[A.LB_ROLE] == chat.attributes[A.LB_ROLE]
        assert chat.attributes[A.GEN_AI_OPERATION] == "chat" and chat.kind == trace.SpanKind.CLIENT
    for tool in tools_:
        assert parent_of(tool, finished).attributes[A.LB_ROLE] == tool.attributes[A.LB_ROLE]
        assert tool.attributes[A.GEN_AI_TOOL_NAME] == "run_query"


@pytest.mark.integration
async def test_the_mcp_servers_own_spans_nest_under_our_tool_span(tools, spans):
    await ask(happy(), tools)
    finished = spans.get_finished_spans()
    server = named(finished, "tools/call run_query")
    assert server, "the MCP SDK creates a server span for each tool call"
    for s in server:
        chain = [a.name for a in ancestors(s, finished)]
        assert "execute_tool run_query" in chain and "invoke_agent legacybridge" in chain
        assert s.attributes.get("gen_ai.tool.name") == "run_query"


@pytest.mark.integration
async def test_attributes_for_genai_phoenix_and_the_project(tools, spans):
    result = await ask(happy(), tools)
    finished = spans.get_finished_spans()
    chat = named(finished, "chat ")[1]  # a writer call
    a = chat.attributes
    assert a[A.GEN_AI_INPUT_TOKENS] == 10 and a[A.GEN_AI_OUTPUT_TOKENS] == 5
    assert a[A.OI_TOKENS_TOTAL] == 15 and a[A.OI_SPAN_KIND] == "LLM" and a[A.OI_MODEL] == "scripted"
    assert a[A.GEN_AI_FINISH_REASONS] == ("tool_calls",) or a[A.GEN_AI_FINISH_REASONS] == [
        "tool_calls"
    ]

    tool = named(finished, "execute_tool run_query")[0].attributes
    assert (
        tool[A.OI_SPAN_KIND] == "TOOL" and tool[A.LB_TOOL_OK] is True and tool[A.LB_ROW_COUNT] == 1
    )

    root = named(finished, "invoke_agent legacybridge")[0].attributes
    assert root[A.OI_SPAN_KIND] == "AGENT" and root[A.LB_STATUS] == "answered"
    assert root[A.LB_CONFIDENCE] == result.confidence and root[A.LB_VERIFIER_AGREEMENT] is True
    assert root[A.LB_SWAR_RISK] == result.swar_risk and root[A.LB_SWAR_LEVEL] == result.swar_level
    assert root[A.LB_ROW_COUNT] == 1 and root[A.LB_TRUNCATED] is False
    assert "FAC-03" in root[A.LB_DEFECTS_TOUCHED] and root[A.LB_PROFILE] == "scripted"


@pytest.mark.integration
async def test_a_rejected_query_is_a_normal_outcome_not_a_red_span(tools, spans):
    llm = ScriptedLLM(
        planner=[plan("query")],
        writer=[call("run_query", sql="SELECT clicve FROM clientes"), call("list_tables")],
    )
    await ask(llm, tools, config=AgentConfig(max_steps=2))
    rejected = named(spans.get_finished_spans(), "execute_tool run_query")[0]
    assert (
        rejected.attributes[A.LB_TOOL_OK] is False
        and rejected.attributes[A.LB_TOOL_STAGE] == "guard"
    )
    assert rejected.attributes["error.type"] == "unknown_column"
    assert rejected.status.status_code != StatusCode.ERROR, (
        "a guard rejection is expected, not a fault"
    )


async def test_an_infrastructure_failure_does_turn_the_tool_span_red(spans):
    class Broken:
        async def list_tools(self):
            from legacybridge.llm.base import ToolSpec

            return [ToolSpec("run_query", "d", {"type": "object"})]

        async def call(self, name, arguments):
            return {"ok": False, "stage": "mcp", "reason": "call_failed", "detail": "boom"}

    llm = ScriptedLLM(
        planner=[plan("query")], writer=[call("run_query", sql="SELECT 1"), call("list_tables")]
    )
    await Agent(llm, Broken(), config=AgentConfig(max_steps=2)).ask(QUESTION)
    tool = named(spans.get_finished_spans(), "execute_tool run_query")[0]
    assert tool.status.status_code == StatusCode.ERROR


@pytest.mark.integration
async def test_a_failing_model_call_is_recorded_and_marks_its_span(tools, spans):
    llm = ScriptedLLM(planner=[LLMUnavailable("omniroute: gave up after 4 attempts")])
    result = await ask(llm, tools)
    finished = spans.get_finished_spans()
    chat = named(finished, "chat ")[0]
    assert chat.status.status_code == StatusCode.ERROR and "gave up" in chat.status.description
    root = named(finished, "invoke_agent legacybridge")[0]
    assert (
        root.attributes[A.LB_ABSTAIN_REASON] == "llm_error"
        and root.status.status_code == StatusCode.ERROR
    )
    assert result.abstain_reason == "llm_error"


# --------------------------------------------------------------------------- content and cost
@pytest.mark.integration
async def test_prompts_and_rows_are_not_captured_unless_asked(tools, spans, monkeypatch):
    monkeypatch.delenv("LB_TRACE_CONTENT", raising=False)
    assert capture_content() is False
    await ask(happy(), tools)
    for s in spans.get_finished_spans():
        assert A.OI_INPUT not in s.attributes and A.OI_OUTPUT not in s.attributes, s.name
        assert not any(k.startswith("llm.input_messages") for k in s.attributes), s.name


@pytest.mark.integration
async def test_content_capture_is_opt_in_and_truncated(tools, spans, monkeypatch):
    monkeypatch.setenv("LB_TRACE_CONTENT", "1")
    await ask(happy(), tools)
    finished = spans.get_finished_spans()
    chat = named(finished, "chat ")[1].attributes
    assert chat["llm.input_messages.0.message.role"] == "system"
    assert all(len(v) <= 2000 for k, v in chat.items() if k.endswith(".content"))
    assert "SELECT" in named(finished, "execute_tool run_query")[0].attributes[A.OI_TOOL_PARAMETERS]
    assert named(finished, "invoke_agent legacybridge")[0].attributes[A.OI_INPUT] == QUESTION


def test_truncate_and_the_environment_switches(monkeypatch):
    assert truncate("x" * 3000) == "x" * 1999 + "…" and truncate("short") == "short"
    for value, expected in (("1", True), ("true", True), ("OTLP", True), ("0", False), ("", False)):
        monkeypatch.setenv("LB_TRACING", value)
        assert tracing_enabled() is expected
    monkeypatch.delenv("LB_TRACING")
    assert tracing_enabled() is False


@pytest.mark.integration
async def test_tracing_off_changes_nothing_about_the_result(tools, spans):
    traced = await ask(happy(), tools)
    spans.clear()
    plain = await ask(happy(), tools, tracer=NoOpTracer())
    assert plain.trace_id is None and traced.trace_id is not None
    assert not named(spans.get_finished_spans(), "invoke_agent")
    for field in ("status", "answer", "sql", "confidence", "verified", "swar_risk"):
        assert getattr(plain, field) == getattr(traced, field), field
    assert [(e.role, e.kind, e.name) for e in plain.events] == [
        (e.role, e.kind, e.name) for e in traced.events
    ]


@pytest.mark.integration
async def test_an_unreachable_collector_never_slows_or_breaks_a_question(tools):
    provider = build_provider("legacybridge-test")  # default exporter: OTLP/HTTP to a dead port
    provider.shutdown()  # (a clean provider for the real test below)
    import os

    old = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
    os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"] = "http://127.0.0.1:9"  # nothing listens here
    try:
        dead = build_provider("legacybridge-test")
        started = time.perf_counter()
        result = await ask(happy(), tools, tracer=dead.get_tracer("t"))
        elapsed = time.perf_counter() - started
    finally:
        if old is None:
            os.environ.pop("OTEL_EXPORTER_OTLP_ENDPOINT")
        else:
            os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"] = old
    assert result.status == "answered" and result.trace_id
    assert elapsed < 10, f"export must not block the agent ({elapsed:.1f}s)"
    dead.shutdown()
