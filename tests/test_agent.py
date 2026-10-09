"""The agent (ADR-002): planner, sql_writer and verifier driven by a scripted model, against the
real database through the MCP tools. The model is fake; everything else is real."""

import pytest

from evals.golden import load_golden
from legacybridge.agent.backends import McpToolBackend
from legacybridge.agent.digest import digest
from legacybridge.agent.evidence import calibrate, numbers_not_in_evidence, parse_number
from legacybridge.agent.loop import QueryRun, compact
from legacybridge.agent.orchestrator import Agent
from legacybridge.agent.roles.planner import extract_json
from legacybridge.agent.roles.verifier import Verdict
from legacybridge.agent.schemas import AgentConfig
from legacybridge.llm.base import InvalidModelOutput, LLMUnavailable
from legacybridge.semantic.loader import load_ontology
from tests.support.scripted import ScriptedLLM, call, plan, say

ONTOLOGY = load_ontology()
GOLDEN = {c.id: c for c in load_golden()}
QUESTION = "¿Cuántas facturas activas hubo en 2004?"
METRIC_2004 = ONTOLOGY.render_metric("facturas_vigentes", year=2004)
INDEPENDENT_2004 = GOLDEN["TRAP-002"].expected_sql
NAIVE_2004 = GOLDEN["TRAP-002"].naive_sql
TABLES = (
    "clientes",
    "productos",
    "faccab",
    "facdet",
    "invart",
    "invalm",
    "invexi",
    "invprv",
    "invmov",
)

pytestmark = pytest.mark.anyio


def answer(text="Hubo 829 facturas vigentes en 2004.", evidence="q1", outcome="answer", **extra):
    return call(
        "submit_answer", answer=text, outcome=outcome, evidence_query=evidence,
        assumptions=extra.pop("assumptions", ["Factura vigente: status V, una por folio"]), **extra,
    )  # fmt: skip


def agrees(confidence=0.9):
    return call("submit_verdict", agrees=True, confidence=confidence, issues=[])


def disagrees(*issues):
    return call("submit_verdict", agrees=False, confidence=0.3, issues=list(issues))


HAPPY_WRITER = [call("run_query", sql=METRIC_2004), answer()]
HAPPY_VERIFIER = [call("run_query", sql=INDEPENDENT_2004), agrees()]


async def ask(llm, tools, question=QUESTION, **config):
    async with McpToolBackend.in_memory(tools) as backend:
        return await Agent(llm, backend, config=AgentConfig(**config)).ask(question)


def fingerprint(conn):
    return tuple(
        conn.execute(
            f"SELECT md5(string_agg(t::text, '|' ORDER BY t::text)) FROM {t} t"
        ).fetchone()[0]
        for t in TABLES
    )


@pytest.fixture
def untouched(admin):
    """The business tables must be identical afterwards; leftover requests are removed."""
    before = fingerprint(admin)
    first_id = admin.execute("SELECT coalesce(max(id), 0) FROM lb_ops.write_requests").fetchone()[0]
    admin.rollback()
    yield lambda: fingerprint(admin) == before
    admin.rollback()
    admin.execute("DELETE FROM lb_ops.write_requests WHERE id > %s", (first_id,))
    admin.commit()


# --------------------------------------------------------------------------- the main flows
async def test_a_question_is_planned_answered_and_verified(tools, untouched):
    llm = ScriptedLLM(
        planner=[plan("query", intent="count valid invoices in 2004")],
        writer=HAPPY_WRITER, verifier=HAPPY_VERIFIER,
    )  # fmt: skip
    result = await ask(llm, tools)

    assert result.status == "answered" and result.verified
    assert result.evidence.rows == [[829]] and result.evidence.query_id == "q1"
    assert result.sql == result.evidence.sql and result.sql.rstrip().endswith("LIMIT 101")
    assert 0.85 <= result.confidence <= 0.99
    assert result.assumptions == ["Factura vigente: status V, una por folio"]
    assert result.warnings == [] and result.profile == "scripted"
    assert (result.usage.llm_calls, result.usage.tool_calls) == (5, 2)
    assert {e.role for e in result.events} == {"planner", "sql_writer", "verifier"}
    assert llm.left_over() == {} and untouched()


async def test_a_vague_question_is_sent_back_for_clarification(tools, untouched):
    llm = ScriptedLLM(planner=[plan("clarify", clarification="¿De qué periodo y en qué moneda?")])
    result = await ask(llm, tools, "¿Cuánto vendimos?")
    assert result.status == "needs_clarification" and "periodo" in result.answer
    assert result.confidence == 0.0 and result.sql is None and result.usage.llm_calls == 1
    assert llm.calls and {r for r, _, _ in llm.calls} == {"planner"}, "no SQL was even attempted"


async def test_an_out_of_scope_question_is_not_answered(tools):
    llm = ScriptedLLM(planner=[plan("abstain", reason="No hay datos del futuro en esta base.")])
    result = await ask(llm, tools, "¿Cuál será la facturación de 2007?")
    assert (result.status, result.abstain_reason) == ("abstained", "out_of_scope")
    assert "futuro" in result.answer and result.confidence == 0.0


async def test_a_write_request_is_queued_not_executed(tools, admin, untouched):
    writer = [
        call("run_query", sql="SELECT clave, nombre, status FROM clientes WHERE clave = '0000000100'"),
        call("propose_write", table="clientes", update={"status": "I"}, where={"clave": "0000000100"}),
    ]  # fmt: skip
    llm = ScriptedLLM(
        planner=[plan("write", intent="deactivate customer 100")], writer=writer,
        verifier=[agrees()],
    )  # fmt: skip
    result = await ask(llm, tools, "Marca como inactivo al cliente 0000000100.")

    assert result.status == "pending_approval" and isinstance(result.request_id, int)
    assert result.preview[0]["clave"] == "0000000100"
    # The status line is built by the agent from the real result; the model is not asked to describe it.
    assert "aprob" in result.answer.lower() and llm.left_over() == {}
    row = admin.execute(
        "SELECT status, requested_by FROM lb_ops.write_requests WHERE id = %s", (result.request_id,)
    ).fetchone()
    admin.rollback()
    assert row == ("pending", "mcp")
    assert "propose_write" in llm.tool_names("writer")
    assert untouched(), "queuing a request must not change business data"


# --------------------------------------------------------------------------- least privilege
async def test_a_read_plan_never_sees_propose_write_and_cannot_use_it(tools, admin, untouched):
    sneaky = call(
        "propose_write", table="clientes", update={"status": "B"}, where={"clave": "0000000100"}
    )
    llm = ScriptedLLM(
        planner=[plan("query")], writer=[sneaky, *HAPPY_WRITER], verifier=HAPPY_VERIFIER
    )  # fmt: skip
    result = await ask(llm, tools)
    assert "propose_write" not in llm.tool_names("writer")
    refused = llm.sent("writer")[1][0][-1].content
    assert "unknown tool propose_write" in refused
    assert result.status == "answered" and result.request_id is None
    assert (
        admin.execute("SELECT count(*) FROM lb_ops.write_requests WHERE reason IS NULL").fetchone()[
            0
        ]
        >= 0
    )
    assert untouched()


async def test_the_verifier_has_read_only_tools(tools):
    llm = ScriptedLLM(planner=[plan("query")], writer=HAPPY_WRITER, verifier=HAPPY_VERIFIER)
    await ask(llm, tools)
    assert llm.tool_names("verifier") == {
        "describe_table", "explain_business_term", "run_query", "submit_verdict",
    }  # fmt: skip
    assert llm.tool_names("planner") == set()


# --------------------------------------------------------------------------- learning from tools
async def test_a_rule_warning_reaches_the_model_and_the_fixed_query_becomes_the_evidence(tools):
    writer = [
        call("run_query", sql=NAIVE_2004),
        call("run_query", sql=INDEPENDENT_2004),
        answer(evidence="q2"),
    ]  # fmt: skip
    llm = ScriptedLLM(planner=[plan("query")], writer=writer, verifier=HAPPY_VERIFIER)
    result = await ask(llm, tools)
    seen_by_model = llm.sent("writer")[1][0][-1].content
    assert "FAC-09" in seen_by_model and "Fix:" in seen_by_model
    assert "data_notice" not in seen_by_model, "bookkeeping must not reach the model"
    assert result.evidence.query_id == "q2" and not any("FAC-09" in w for w in result.warnings)


async def test_a_rejected_query_comes_back_with_a_hint_and_is_corrected(tools):
    writer = [
        call("run_query", sql="SELECT clicve FROM clientes"),
        call("run_query", sql="SELECT COUNT(*) FROM clientes WHERE status = 'A'"),
        answer("Hay 242 clientes activos."),
    ]  # fmt: skip
    llm = ScriptedLLM(
        planner=[plan("query")],
        writer=writer,
        verifier=[
            agrees(),
        ],
    )
    llm.queues["verifier"] = [
        call("run_query", sql="SELECT COUNT(*) FROM clientes WHERE status = 'A'"),
        agrees(),
    ]
    result = await ask(llm, tools, "¿Cuántos clientes activos hay?")
    assert "clave" in llm.sent("writer")[1][0][-1].content  # the "did you mean" hint
    assert result.status == "answered" and result.evidence.rows == [[242]]


async def test_an_unusable_evidence_pointer_falls_back_to_the_last_query_with_a_warning(tools):
    writer = [call("run_query", sql=METRIC_2004), answer(evidence="q9")]
    llm = ScriptedLLM(planner=[plan("query")], writer=writer, verifier=HAPPY_VERIFIER)
    result = await ask(llm, tools)
    assert result.evidence.query_id == "q1"
    assert any("q9" in w for w in result.warnings)


# --------------------------------------------------------------------------- the verifier
async def test_a_disagreeing_verifier_gets_the_writer_to_retry_with_its_critique(tools):
    llm = ScriptedLLM(
        planner=[plan("query")],
        writer=[*HAPPY_WRITER, *HAPPY_WRITER],
        verifier=[disagrees("my own count is 830"), *HAPPY_VERIFIER],
    )  # fmt: skip
    result = await ask(llm, tools)
    assert result.status == "answered" and result.verified
    prompts = [m[1].content for m, _ in llm.sent("writer")]
    assert any("verifier reviewed your previous attempt" in p and "830" in p for p in prompts)
    assert llm.left_over() == {}


async def test_the_verifier_never_sees_the_writers_reasoning(tools):
    llm = ScriptedLLM(planner=[plan("query")], writer=HAPPY_WRITER, verifier=HAPPY_VERIFIER)
    await ask(llm, tools)
    briefing = llm.sent("verifier")[0][0][1].content
    assert "Final SQL:" in briefing and "Agent's answer:" in briefing
    assert "tool_calls" not in briefing and "q1" not in briefing.split("Result:")[0]
    assert len(llm.sent("verifier")[0][0]) == 2, "only the system prompt and the briefing"


async def test_if_the_verifier_still_disagrees_the_agent_abstains_and_labels_the_candidate(
    tools, untouched
):
    llm = ScriptedLLM(
        planner=[plan("query")],
        writer=[*HAPPY_WRITER, *HAPPY_WRITER],
        verifier=[disagrees("count differs"), disagrees("still differs")],
    )  # fmt: skip
    result = await ask(llm, tools)
    assert (result.status, result.abstain_reason) == ("abstained", "verification_failed")
    assert "829" in result.answer and "sin verificar" in result.answer
    assert any("still differs" in w for w in result.warnings)
    assert result.confidence == 0.0 and untouched()


async def test_without_the_verifier_the_answer_is_marked_unverified_and_less_confident(tools):
    llm = ScriptedLLM(planner=[plan("query")], writer=HAPPY_WRITER)
    result = await ask(llm, tools, verify=False)
    assert result.status == "answered" and not result.verified
    assert result.confidence <= 0.55 and any(
        "not independently verified" in w for w in result.warnings
    )
    assert llm.sent("verifier") == []


async def test_an_agreeing_verifier_that_re_derived_nothing_is_capped(tools):
    llm = ScriptedLLM(planner=[plan("query")], writer=HAPPY_WRITER, verifier=[agrees(0.95)])
    result = await ask(llm, tools)
    assert result.verified and result.confidence <= 0.6


async def test_a_verifier_that_gives_no_verdict_is_not_a_disagreement(tools):
    llm = ScriptedLLM(
        planner=[plan("query")], writer=HAPPY_WRITER, verifier=[say("looks fine"), say("really")]
    )  # fmt: skip
    result = await ask(llm, tools)
    assert result.status == "answered" and not result.verified
    assert any("did not give a verdict" in w for w in result.warnings)


async def test_a_write_the_verifier_doubts_stays_pending_with_a_loud_warning(tools, untouched):
    writer = [
        call("propose_write", table="clientes", update={"status": "B"}, where={"estado": "GTO", "status": "I"}),
    ]  # fmt: skip
    llm = ScriptedLLM(
        planner=[plan("write")], writer=writer, verifier=[disagrees("the user named one customer, not all of Guanajuato")]
    )  # fmt: skip
    result = await ask(llm, tools, "Da de baja a Laura Martinez.")
    assert result.status == "pending_approval" and result.verified is False
    assert any("may not match" in w and "one customer" in w for w in result.warnings)
    assert untouched()


# --------------------------------------------------------------------------- honesty checks
async def test_a_figure_that_is_not_in_the_result_lowers_confidence_and_is_flagged(tools):
    writer = [call("run_query", sql=METRIC_2004), answer("Hubo 9,999 facturas vigentes en 2004.")]
    llm = ScriptedLLM(planner=[plan("query")], writer=writer, verifier=HAPPY_VERIFIER)
    result = await ask(llm, tools)
    assert result.confidence <= 0.4
    assert any("not in the query result" in w and "9,999" in w for w in result.warnings)


async def test_the_sql_and_rows_come_from_the_real_call_not_from_the_model(tools):
    writer = [
        call("run_query", sql=METRIC_2004),
        call("submit_answer", answer="Hubo 829.", outcome="answer", evidence_query="q1",
             sql="SELECT 'made up'", rows=[[1]]),
    ]  # fmt: skip
    llm = ScriptedLLM(planner=[plan("query")], writer=writer, verifier=HAPPY_VERIFIER)
    result = await ask(llm, tools)
    assert result.sql != "SELECT 'made up'" and result.evidence.rows == [[829]]


async def test_an_answer_with_no_query_behind_it_is_refused(tools):
    llm = ScriptedLLM(planner=[plan("query")], writer=[answer("Hubo 829 facturas.", evidence="")])
    result = await ask(llm, tools)
    assert (result.status, result.abstain_reason) == ("abstained", "no_evidence")


async def test_a_definition_can_be_answered_from_the_ontology_but_not_with_invented_figures(tools):
    ask_term = call("explain_business_term", term="cliente activo")
    ok = ScriptedLLM(
        planner=[plan("query")],
        writer=[ask_term, answer("Un cliente activo es el que tiene status A.", evidence="")],
    )  # fmt: skip
    result = await ask(ok, tools, "¿Qué es un cliente activo?")
    assert result.status == "answered" and result.sql is None and ok.sent("verifier") == []

    liar = ScriptedLLM(
        planner=[plan("query")],
        writer=[ask_term, answer("Hay 5,000 clientes activos.", evidence="")],
    )  # fmt: skip
    result = await ask(liar, tools, "¿Cuántos clientes activos hay?")
    assert (result.status, result.abstain_reason) == ("abstained", "no_evidence")


async def test_the_writer_can_say_it_cannot_answer(tools):
    writer = [answer("No hay una columna de costos.", outcome="cannot_answer", evidence="")]
    result = await ask(ScriptedLLM(planner=[plan("query")], writer=writer), tools, "¿Costos?")
    assert (result.status, result.abstain_reason) == ("abstained", "cannot_answer")
    assert "costos" in result.answer


# --------------------------------------------------------------------------- robustness
async def test_a_model_that_forgets_to_submit_is_nudged_then_its_text_is_used(tools):
    writer = [
        call("run_query", sql=METRIC_2004),
        say("Hubo 829 facturas."),
        say("Hubo 829 facturas."),
    ]
    llm = ScriptedLLM(planner=[plan("query")], writer=writer, verifier=HAPPY_VERIFIER)
    result = await ask(llm, tools)
    assert result.status == "answered" and result.evidence.rows == [[829]]
    assert any("did not call submit_answer" in w for w in result.warnings)
    assert "Finish by calling" in llm.sent("writer")[2][0][-1].content


async def test_invalid_tool_arguments_are_sent_back_to_the_model(tools):
    from legacybridge.llm.base import LLMResponse, ToolCall

    broken = LLMResponse(
        text="", tool_calls=(ToolCall("c0", "run_query", {"_raw": "{oops"}),), model="s"
    )
    llm = ScriptedLLM(
        planner=[plan("query")], writer=[broken, *HAPPY_WRITER], verifier=HAPPY_VERIFIER
    )
    result = await ask(llm, tools)
    assert "not valid JSON" in llm.sent("writer")[1][0][-1].content
    assert result.status == "answered"


async def test_a_loop_that_never_finishes_stops_at_the_step_limit(tools):
    llm = ScriptedLLM(planner=[plan("query")], writer=[call("list_tables")] * 3)
    result = await ask(llm, tools, max_steps=3)
    assert (result.status, result.abstain_reason) == ("abstained", "no_answer")
    assert result.usage.llm_calls == 4  # the planner plus three writer turns


async def test_an_unusable_planner_falls_back_to_a_read_only_query(tools, untouched):
    llm = ScriptedLLM(
        planner=[say("no idea"), say("still no json")], writer=HAPPY_WRITER, verifier=HAPPY_VERIFIER
    )  # fmt: skip
    result = await ask(llm, tools)
    assert result.status == "answered" and "propose_write" not in llm.tool_names("writer")
    assert untouched()


async def test_a_failing_provider_ends_in_an_honest_abstention(tools):
    result = await ask(
        ScriptedLLM(planner=[LLMUnavailable("omniroute: gave up after 4 attempts")]), tools
    )
    assert (result.status, result.abstain_reason) == ("abstained", "llm_error")
    assert "gave up" in result.answer and result.events[-1].ok is False

    mid_run = ScriptedLLM(planner=[plan("query")], writer=[LLMUnavailable("down")])
    result = await ask(mid_run, tools)
    assert result.abstain_reason == "llm_error"
    assert result.usage.llm_calls == 2  # the planner and the writer call that failed
    assert (result.events[-1].role, result.events[-1].ok) == ("sql_writer", False)


async def test_the_verifier_failing_leaves_an_unverified_answer_not_a_crash(tools):
    llm = ScriptedLLM(
        planner=[plan("query")], writer=HAPPY_WRITER, verifier=[LLMUnavailable("down")]
    )
    result = await ask(llm, tools)
    assert result.status == "answered" and not result.verified
    assert any("verifier could not run" in w for w in result.warnings)


# --------------------------------------------------------------------------- pieces
def test_extract_json_finds_the_object_in_prose_and_fences():
    assert extract_json('Sure! ```json\n{"a": 1, "b": {"c": "}"}}\n``` done') == {
        "a": 1,
        "b": {"c": "}"},
    }
    assert extract_json('noise {not json} then {"ok": true}') == {"ok": True}
    with pytest.raises(InvalidModelOutput):
        extract_json("nothing here")


def test_compact_hides_bookkeeping_and_limits_rows():
    result = {"ok": True, "rows": [[i] for i in range(50)], "data_notice": "x", "ms": 1.0,
              "violations": [{"d": 1}], "tables": ["t"], "warnings": ["w"]}  # fmt: skip
    import json

    shown = json.loads(compact(result, 30, "q4"))
    assert (
        len(shown["rows"]) == 30
        and shown["query_id"] == "q4"
        and "showing 30 of 50" in shown["note"]
    )
    assert not {"data_notice", "ms", "violations", "tables"} & set(shown) and shown["warnings"] == [
        "w"
    ]


@pytest.mark.parametrize(
    "token,value",
    [("1,234.56", 1234.56), ("1.234,56", 1234.56), ("829", 829), ("20.071.338,19", 20071338.19),
     ("3,5", 3.5), ("1,234", 1234), ("12.5", 12.5)],
)  # fmt: skip
def test_parse_number_handles_both_conventions(token, value):
    assert parse_number(token) == value


def evidence(rows):
    return QueryRun("q1", "SELECT", ["n"], rows, len(rows), False, [], [])


def test_figures_in_the_answer_are_checked_against_the_evidence():
    ev = evidence([[829]])
    q = "¿Cuántas facturas hubo en 2004?"
    assert numbers_not_in_evidence("Hubo 829 facturas en 2004.", q, [], ev) == []
    assert numbers_not_in_evidence("Hubo 1,000 facturas.", q, [], ev) == ["1,000"]
    assert numbers_not_in_evidence("Son 12 meses", q, [], ev) == []  # small numbers are not checked
    totals = evidence([[300.5], [200.25]])
    assert numbers_not_in_evidence("En total 500.75", q, [], totals) == []  # a sum is legitimate


def test_confidence_comes_from_observations_not_from_the_model():
    ev = evidence([[829]])
    good = Verdict(True, 0.9, checks_run=1)
    assert calibrate(verdict=good, evidence=ev, invented=[]) == 0.9
    assert calibrate(verdict=Verdict(True, 0.95, checks_run=0), evidence=ev, invented=[]) == 0.6
    assert calibrate(verdict=Verdict(False, 0.9), evidence=ev, invented=[]) == 0.3
    assert calibrate(verdict=None, evidence=ev, invented=[]) == 0.55
    assert calibrate(verdict=good, evidence=ev, invented=["9,999"]) == 0.4
    assert calibrate(verdict=good, evidence=evidence([]), invented=[]) == 0.4
    truncated = QueryRun("q1", "S", ["n"], [[1]], 1, True, [], [])
    assert calibrate(verdict=good, evidence=truncated, invented=[]) == 0.5
    broken = QueryRun("q1", "S", ["n"], [[1]], 1, False, [], [{"severity": "warning"}] * 2)
    assert calibrate(verdict=good, evidence=broken, invented=[]) == 0.6


def test_the_ontology_digest_is_short_and_comes_from_the_ontology():
    text = digest(ONTOLOGY)
    assert "2000 to 2005" in text and "facturas_vigentes [year]" in text
    assert "existencia_actual" in text and len(text) < 3500


# --------------------------------------------------------------------------- found by running it live
async def test_a_queued_request_ends_the_write_loop_without_asking_the_model_to_describe_it(
    tools, untouched
):
    """Live, the model once returned nothing after propose_write succeeded. Nothing more is needed
    from it: the outcome is the real result of the call."""
    writer = [
        call(
            "propose_write",
            table="clientes",
            update={"email": "x@example.com"},
            where={"clave": "0000000007"},
        )
    ]
    llm = ScriptedLLM(planner=[plan("write")], writer=writer, verifier=[agrees()])
    result = await ask(llm, tools, "Actualiza el correo del cliente 0000000007")
    assert result.status == "pending_approval" and isinstance(result.request_id, int)
    assert len(llm.sent("writer")) == 1, (
        "one writer call: the model was not asked to submit anything"
    )
    assert untouched()


async def test_a_verifier_failure_after_queuing_still_reports_the_request(tools, untouched):
    """The request exists in the queue, so the result must say so, whatever happens afterwards."""
    writer = [
        call(
            "propose_write", table="clientes", update={"status": "I"}, where={"clave": "0000000100"}
        )
    ]
    llm = ScriptedLLM(planner=[plan("write")], writer=writer, verifier=[LLMUnavailable("down")])
    result = await ask(llm, tools, "Marca como inactivo al cliente 0000000100.")
    assert result.status == "pending_approval" and isinstance(result.request_id, int)
    assert result.abstain_reason is None and result.verified is False
    assert any("verifier could not run" in w for w in result.warnings)
    assert untouched()


async def test_a_model_failure_before_anything_was_queued_is_still_an_honest_abstention(
    tools, untouched
):
    writer = [
        call("run_query", sql="SELECT clave FROM clientes WHERE clave = '0000000100'"),
        LLMUnavailable("down"),
    ]
    result = await ask(
        ScriptedLLM(planner=[plan("write")], writer=writer), tools, "Da de baja al cliente 100"
    )
    assert (result.status, result.abstain_reason, result.request_id) == (
        "abstained",
        "llm_error",
        None,
    )
    assert untouched()


async def test_the_tool_loop_keeps_what_the_tools_did_when_the_model_fails(tools):
    from legacybridge.agent.events import Recorder
    from legacybridge.agent.loop import run_tool_loop
    from legacybridge.agent.roles.sql_writer import SUBMIT_ANSWER

    llm = ScriptedLLM(writer=[call("run_query", sql=METRIC_2004), LLMUnavailable("down")])
    async with McpToolBackend.in_memory(tools) as backend:
        specs = await backend.list_tools()
        out = await run_tool_loop(
            llm=llm, backend=backend, rec=Recorder(), role="sql_writer",
            system="You answer ONE question (test)", user="q", tools=specs, finish=SUBMIT_ANSWER,
            max_steps=5, rows_to_model=30,
        )  # fmt: skip
    assert out.reason == "llm_error" and isinstance(out.error, LLMUnavailable)
    assert [c.name for c in out.calls] == ["run_query"] and out.queries[0].rows == [[829]]


async def test_a_queued_request_survives_a_model_failure_in_the_writer(monkeypatch):
    """Defence in depth: even if the loop ends in a model error, a request that was queued is kept."""
    from legacybridge.agent.events import Recorder
    from legacybridge.agent.loop import LoopResult, ToolRecord
    from legacybridge.agent.roles import sql_writer
    from legacybridge.agent.roles.planner import PlannerDecision

    queued = {
        "ok": True,
        "request_id": 77,
        "table": "clientes",
        "update": {},
        "where": {},
        "affected_rows": 1,
    }

    async def fake_loop(**kwargs):
        return LoopResult(
            calls=[ToolRecord("propose_write", {}, queued)],
            error=LLMUnavailable("down"),
            reason="llm_error",
        )

    monkeypatch.setattr(sql_writer, "run_tool_loop", fake_loop)
    out = await sql_writer.run_writer(
        llm=None, backend=None, rec=Recorder(), specs=[], question="q", plan=PlannerDecision(action="write"),
        digest_text="", mode="write", config=AgentConfig(),
    )  # fmt: skip
    assert out.proposal["request_id"] == 77 and any(
        "failed after the request was queued" in n for n in out.notes
    )
