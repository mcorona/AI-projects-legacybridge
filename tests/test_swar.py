"""The SWAR detector and the early warning (ADR-012)."""

import pytest

from evals.golden import load_golden
from legacybridge.agent.backends import McpToolBackend
from legacybridge.agent.orchestrator import Agent
from legacybridge.agent.schemas import AgentConfig, AgentResult, Event, Evidence, VerdictRecord
from legacybridge.observability.swar_config import SwarConfig, load_swar_config
from legacybridge.observability.swar_detector import (
    RunFacts,
    assess,
    combine,
    dominant_family,
    risk_at,
)
from legacybridge.semantic.loader import load_ontology
from tests.support.scripted import ScriptedLLM, call, plan

ONTOLOGY = load_ontology()
CFG = load_swar_config()
GOLDEN = {c.id: c for c in load_golden()}
QUESTION = "¿Cuántas facturas activas hubo en 2004?"
CLEAN_SQL = GOLDEN["TRAP-002"].expected_sql
NAIVE_SQL = GOLDEN["TRAP-002"].naive_sql
METRIC = ONTOLOGY.render_metric("facturas_vigentes", year=2004)


def tool(ok=True, name="run_query", role="sql_writer", h="a", **kw):
    return Event(role=role, kind="tool", name=name, ok=ok, ms=1, args_hash=h, **kw)


def llm_event(role="sql_writer", ok=True, retries=(), **kw):
    return Event(role=role, kind="llm", name="m", ok=ok, ms=1, retries=list(retries), **kw)


def evidence(rows=((829,),), count=None, truncated=False, violations=(), defects=()):
    rows = [list(r) for r in rows]
    return Evidence(
        query_id="q1", sql="SELECT", columns=["n"], rows=rows,
        row_count=len(rows) if count is None else count, truncated=truncated,
        violations=list(violations), defects_touched=list(defects),
    )  # fmt: skip


def facts(**kw):
    base = dict(question=QUESTION, status="answered", answer="Hubo 829 facturas.",
                verdicts=[VerdictRecord(agrees=True, confidence=0.9, checks_run=1)],
                evidence=evidence())  # fmt: skip
    return RunFacts(**{**base, **kw})


def names(facts_, phase="final"):
    return {s.name for s in assess(facts_, ONTOLOGY, CFG, phase).signals}


WARNING = {"defect": "FAC-09", "severity": "warning"}


# --------------------------------------------------------------------------- a clean run
def test_a_clean_run_has_no_signals_and_no_risk():
    a = assess(facts(), ONTOLOGY, CFG)
    assert (a.risk, a.level, a.signals) == (0.0, "low", [])


# --------------------------------------------------------------------------- trajectory
def test_identical_repeated_tool_calls():
    assert "repeated_tool_call" in names(facts(events=[tool(), tool(), tool()]))
    assert "repeated_tool_call" not in names(facts(events=[tool(), tool()]))
    assert "repeated_tool_call" not in names(facts(events=[tool(h="a"), tool(h="b"), tool(h="c")]))
    # the verifier re-deriving with the same arguments is not the writer going in circles
    mixed = [tool(role="sql_writer"), tool(role="verifier"), tool(role="verifier", h="b")]
    assert "repeated_tool_call" not in names(facts(events=mixed))


def test_a_cascade_of_failures_needs_consecutive_failures():
    bad = [tool(ok=False, h=str(i)) for i in range(3)]
    assert "error_cascade" in names(facts(events=bad))
    broken_up = [bad[0], bad[1], tool(ok=True, h="x"), bad[2]]
    assert "error_cascade" not in names(facts(events=broken_up))


def test_sql_rejections_have_two_exclusive_levels():
    two = [tool(ok=False, h="1"), tool(ok=False, h="2")]
    assert names(facts(events=two)) >= {"sql_rejections"}
    four = [tool(ok=False, h=str(i)) for i in range(4)]
    found = names(facts(events=four))
    assert "sql_rejections_many" in found and "sql_rejections" not in found
    other_tools = [tool(ok=False, name="describe_table", h=str(i)) for i in range(4)]
    assert not {"sql_rejections", "sql_rejections_many"} & names(facts(events=other_tools))


def test_retries_and_the_step_limit():
    assert "writer_retry" in names(facts(events=[tool(attempt=1)]))
    assert "model_retries" in names(facts(events=[llm_event(retries=["RateLimitError 429"])]))
    assert "model_retries" in names(facts(events=[llm_event(ok=False)]))
    assert "model_retries" not in names(facts(events=[llm_event()]))
    assert "step_limit" in names(facts(status="abstained", abstain_reason="no_answer"))


# --------------------------------------------------------------------------- result
def test_unresolved_violations_only_count_warnings():
    assert "unresolved_violations" in names(facts(evidence=evidence(violations=[WARNING])))
    info = {"defect": "FAC-02", "severity": "info"}
    assert "unresolved_violations" not in names(facts(evidence=evidence(violations=[info])))


def test_a_confident_answer_over_zero_rows():
    assert "zero_rows" in names(facts(evidence=evidence(rows=[], count=0)))
    assert "zero_rows" not in names(facts(status="abstained", evidence=evidence(rows=[], count=0)))


def test_truncation_must_be_acknowledged_in_the_answer():
    cut = evidence(rows=[[i] for i in range(20)], count=101, truncated=True)
    assert "truncation_unacknowledged" in names(facts(evidence=cut, answer="Hay 18 productos."))
    for said in ("Son 20 de 101 filas.", "Lista parcial: 20.", "Showing the first 20 rows."):
        assert "truncation_unacknowledged" not in names(facts(evidence=cut, answer=said)), said


def test_figures_not_in_the_evidence_but_not_when_the_rows_were_cut():
    assert "figures_not_in_evidence" in names(facts(answer="Hubo 9,999 facturas."))
    assert "figures_not_in_evidence" not in names(facts(answer="Hubo 829 facturas."))
    cut = evidence(rows=[[i] for i in range(20)], count=101, truncated=True)
    assert "figures_not_in_evidence" not in names(facts(evidence=cut, answer="El 500 es el mayor."))


def test_an_answer_with_no_query_behind_it():
    assert "no_evidence" in names(facts(evidence=None))
    assert "no_evidence" not in names(facts(evidence=None, status="abstained"))


def test_verifier_signals():
    def verdicts(*agrees, checks=1):
        return [VerdictRecord(agrees=a, confidence=0.9, checks_run=checks) for a in agrees]

    assert "verifier_disagreed_final" in names(facts(verdicts=verdicts(False)))
    found = names(facts(verdicts=verdicts(False, True)))
    assert "verifier_disagreed_retried" in found and "verifier_disagreed_final" not in found
    assert "no_independent_check" in names(facts(verdicts=verdicts(True, checks=0)))
    assert "no_independent_check" not in names(
        facts(status="pending_approval", evidence=None, verdicts=verdicts(True, checks=0))
    )
    assert "unverified" in names(facts(verdicts=[]))
    assert "unverified" not in names(facts(status="abstained", verdicts=[]))


# --------------------------------------------------------------------------- semantics
def test_a_defect_the_question_touches_and_the_sql_violates():
    both = facts(evidence=evidence(violations=[WARNING]))  # "facturas activas" -> FAC-09
    assert "question_defect_violated" in names(both)
    other = facts(
        question="¿Cuántos clientes activos hay?", evidence=evidence(violations=[WARNING])
    )
    assert "question_defect_violated" not in names(other), "FAC-09 is not a customer defect"


@pytest.mark.parametrize(
    "question,answer,assumptions,fires",
    [
        ("¿Cuánto vendimos?", "Son 829.", [], True),
        ("¿Cuánto vendimos en 2003?", "Son 829.", [], False),
        ("¿Cuánto vendimos?", "Son 829 en 2004.", [], False),
        ("¿Cuánto vendimos?", "Son 829.", ["periodo: 2004"], False),
        ("¿Cuánto vendimos en total?", "Son 829.", [], False),
        ("Ventas por año", "Son 829.", [], False),
        ("¿Cuántos clientes hay?", "Son 829.", [], False),
    ],
)
def test_a_period_the_question_needed_and_nobody_stated(question, answer, assumptions, fires):
    found = names(facts(question=question, answer=answer, assumptions=assumptions))
    assert ("unstated_parameter" in found) is fires


# --------------------------------------------------------------------------- combination
def test_noisy_or_and_the_group_cap():
    def sig(name):
        from legacybridge.agent.schemas import SwarSignal

        spec = CFG.signals[name]
        return SwarSignal(name=name, family=spec.family, weight=spec.weight)

    assert combine([], CFG) == 0.0
    assert combine([sig("zero_rows")], CFG) == 0.4
    assert combine([sig("zero_rows"), sig("figures_not_in_evidence")], CFG) == pytest.approx(0.76)
    # the two rule-derived signals alone cannot pass the cap, however heavy: 1-(0.5)(0.3)=0.85 -> 0.6
    capped = combine([sig("unresolved_violations"), sig("question_defect_violated")], CFG)
    assert capped == 0.6 < CFG.action_threshold
    # ... but they do tip the balance when something independent agrees
    assert combine(
        [
            sig("unresolved_violations"),
            sig("question_defect_violated"),
            sig("figures_not_in_evidence"),
        ],
        CFG,
    ) == pytest.approx(0.84)
    everything = [sig(n) for n in CFG.signals]
    assert combine(everything, CFG) == 0.99


def test_levels_and_configuration_checks():
    assert [CFG.level(r) for r in (0.0, 0.39, 0.4, 0.69, 0.7, 0.99)] == [
        "low", "low", "elevated", "elevated", "high", "high",
    ]  # fmt: skip
    raw = CFG.model_dump()
    with pytest.raises(ValueError, match="elevated < high"):
        SwarConfig.model_validate({**raw, "thresholds": {"elevated": 0.8, "high": 0.7}})
    bad = {
        **raw,
        "signals": {
            **raw["signals"],
            "zero_rows": {**raw["signals"]["zero_rows"], "group": "nope"},
        },
    }
    with pytest.raises(ValueError, match="unknown group"):
        SwarConfig.model_validate(bad)


def test_signals_only_fire_once_they_can_be_known():
    f = facts(evidence=evidence(violations=[WARNING]), verdicts=[])
    assert not names(f, "trajectory")
    writer = names(f, "writer")
    assert "unresolved_violations" in writer and "unverified" not in writer
    assert "unverified" in names(f, "final")


def test_the_dominant_family_is_the_heaviest_signal():
    a = assess(facts(question="¿Cuánto vendimos?", answer="Son 829."), ONTOLOGY, CFG)
    assert dominant_family(a.signals) == "semantics" and dominant_family([]) is None


# --------------------------------------------------------------------------- the early warning
pytestmark_db = pytest.mark.integration
anyio_mark = pytest.mark.anyio


async def run(llm, tools, question=QUESTION, *, action="gate", escalation=None, **config):
    async with McpToolBackend.in_memory(tools) as backend:
        agent = Agent(llm, backend, config=AgentConfig(swar_action=action, **config),
                      escalation_llm=escalation)  # fmt: skip
        return await agent.ask(question)


def answer(text, evidence="q1", **extra):
    return call("submit_answer", answer=text, outcome="answer", evidence_query=evidence,
                assumptions=extra.pop("assumptions", ["una por folio"]), **extra)  # fmt: skip


def agrees(confidence=0.9):
    return call("submit_verdict", agrees=True, confidence=confidence, issues=[])


HAPPY = dict(
    planner=[plan("query")],
    writer=[call("run_query", sql=METRIC), answer("Hubo 829 facturas en 2004.")],
    verifier=[call("run_query", sql=CLEAN_SQL), agrees()],
)
LOOP = [call("run_query", sql="SELECT clicve FROM clientes")] * 6  # six identical rejected queries


def scripted(**overrides):
    return ScriptedLLM(**{k: list(v) for k, v in {**HAPPY, **overrides}.items()})


@pytest.mark.integration
@anyio_mark
async def test_every_result_carries_a_swar_assessment(tools):
    result = await run(scripted(), tools)
    assert result.status == "answered" and result.early_warning is None
    assert (result.swar_risk, result.swar_level, result.swar_signals) == (0.0, "low", [])
    assert "FAC-03" in result.defects_touched
    clarify = await run(
        ScriptedLLM(planner=[plan("clarify", clarification="¿Qué periodo?")]), tools
    )
    assert clarify.status == "needs_clarification" and clarify.swar_risk == 0.0


@pytest.mark.integration
@anyio_mark
async def test_c1_stops_a_writer_that_goes_in_circles(tools):
    llm = scripted(writer=LOOP)
    result = await run(llm, tools)
    assert (result.status, result.abstain_reason, result.early_warning) == (
        "abstained",
        "swar_risk",
        "C1",
    )
    assert len(llm.sent("writer")) == 6, "stopped at the checkpoint, not at the step limit"
    assert llm.sent("verifier") == [] and "fiable" in result.answer
    assert any(w.startswith("swar: ") for w in result.warnings)


@pytest.mark.integration
@anyio_mark
async def test_c1_does_nothing_before_its_checkpoint(tools):
    """Five rejected queries are below the checkpoint (six tool results), so the early warning
    stays silent; the writer then gives up and that is reported as what it is."""
    llm = scripted(writer=[*LOOP[:5], answer("No pude.", evidence="")])
    result = await run(llm, tools)
    assert result.early_warning is None and result.abstain_reason == "no_evidence"
    assert len(llm.sent("writer")) == 6


@pytest.mark.integration
@anyio_mark
async def test_the_sixth_result_is_where_c1_looks_even_if_that_one_succeeded(tools):
    """A stuck pattern is judged by what happened, not by how the last call ended."""
    llm = scripted(writer=[*LOOP[:5], call("run_query", sql=METRIC), answer("Hubo 829.")])
    result = await run(llm, tools)
    assert result.early_warning == "C1" and result.abstain_reason == "swar_risk"


@pytest.mark.integration
@anyio_mark
async def test_a_risky_trajectory_escalates_to_the_second_profile_once(tools):
    first, backup = scripted(writer=LOOP, verifier=[]), scripted()
    backup.name = "backup"
    result = await run(first, tools, escalation=backup)

    assert result.status == "answered" and result.profile == "backup"
    assert (result.escalated_from, result.early_warning) == ("scripted", "C1")
    assert any("Escalated from scripted" in w for w in result.warnings)
    assert any(e.escalated for e in result.events) and any(not e.escalated for e in result.events)
    assert result.usage.llm_calls == (1 + 6) + 5, "the cost of both runs is accumulated"
    assert result.swar_risk == 0.0, "the answer is the rerun's, so its trajectory is the rerun's"
    assert first.left_over() == {} and backup.left_over() == {}


@pytest.mark.integration
@anyio_mark
async def test_a_second_risky_run_abstains_instead_of_escalating_again(tools):
    first, backup = scripted(writer=LOOP, verifier=[]), scripted(writer=LOOP, verifier=[])
    backup.name = "backup"
    result = await run(first, tools, escalation=backup)
    assert (result.status, result.abstain_reason) == ("abstained", "swar_risk")
    assert result.escalated_from == "scripted" and result.profile == "backup"


@pytest.mark.integration
@anyio_mark
async def test_c2_turns_an_unstated_period_into_a_clarification_before_the_verifier(tools):
    writer = [
        call("run_query", sql="SELECT COUNT(*) AS n FROM clientes WHERE status <> 'B'"),
        answer("Son 278.", assumptions=[]),
    ]
    llm = scripted(writer=writer)
    result = await run(llm, tools, "¿Cuánto vendimos?")
    assert (result.status, result.early_warning) == ("needs_clarification", "C2")
    assert "2000" in result.answer and "2005" in result.answer
    assert llm.sent("verifier") == [], (
        "no verifier call is spent on an answer that will not be given"
    )


@pytest.mark.integration
@anyio_mark
async def test_c2_abstains_when_the_rules_and_an_independent_signal_agree(tools):
    writer = [call("run_query", sql=NAIVE_SQL), answer("Hubo 9,999 facturas en 2004.")]
    llm = scripted(writer=writer)
    result = await run(llm, tools)
    assert (result.status, result.abstain_reason, result.early_warning) == (
        "abstained",
        "swar_risk",
        "C2",
    )
    assert "9,999" in result.answer and "sin verificar" in result.answer
    assert llm.sent("verifier") == []
    assert {"unresolved_violations", "figures_not_in_evidence"} <= {
        s.name for s in result.swar_signals
    }


@pytest.mark.integration
@anyio_mark
async def test_a_lone_heuristic_never_acts_it_only_raises_the_level(tools):
    """The rule engine is noisy on neutral cases, so rule signals alone cannot pass the action
    threshold. The answer is given, with an elevated risk the reader can see."""
    count = await _naive_count(tools)
    llm = scripted(
        writer=[call("run_query", sql=NAIVE_SQL), answer(f"Hubo {count} facturas en 2004.")]
    )
    result = await run(llm, tools)
    assert result.status == "answered" and result.early_warning is None
    assert {s.name for s in result.swar_signals} == {
        "unresolved_violations",
        "question_defect_violated",
    }
    assert result.swar_level == "elevated" and 0.4 <= result.swar_risk < CFG.action_threshold


async def _naive_count(tools):
    return tools.run_query(NAIVE_SQL)["rows"][0][0]


@pytest.mark.integration
@anyio_mark
async def test_annotate_reports_the_warning_without_changing_the_outcome(tools):
    writer = [call("run_query", sql=NAIVE_SQL), answer("Hubo 9,999 facturas en 2004.")]
    gated = await run(scripted(writer=writer), tools)
    annotated = await run(scripted(writer=writer), tools, action="annotate")
    off = await run(scripted(writer=writer), tools, action="off")

    assert gated.status == "abstained"
    for result in (annotated, off):
        assert result.status == "answered" and result.early_warning is None
        assert result.swar_risk >= CFG.thresholds["high"], "the final assessment is always computed"
    assert any(w.startswith("early warning C2") for w in annotated.warnings)
    assert not any(w.startswith("early warning") for w in off.warnings)


@pytest.mark.integration
@anyio_mark
async def test_a_verifier_that_disagreed_before_a_retry_leaves_a_mark(tools):
    llm = scripted(
        writer=[*HAPPY["writer"], *HAPPY["writer"]],
        verifier=[call("submit_verdict", agrees=False, confidence=0.3, issues=["count differs"]),
                  *HAPPY["verifier"]],
    )  # fmt: skip
    result = await run(llm, tools)
    assert result.status == "answered" and [v.agrees for v in result.verdicts] == [False, True]
    assert {"verifier_disagreed_retried", "writer_retry"} <= {s.name for s in result.swar_signals}
    assert result.swar_level == "elevated"


@pytest.mark.integration
@anyio_mark
async def test_without_the_verifier_the_answer_is_marked_unverified(tools):
    result = await run(scripted(verifier=[]), tools, verify=False)
    assert {s.name for s in result.swar_signals} == {"unverified"} and result.swar_level == "low"


# --------------------------------------------------------------------------- replay: first k spans
@pytest.mark.integration
@anyio_mark
async def test_risk_from_the_first_k_events_never_falls_as_more_is_seen(tools):
    runs = [
        await run(
            scripted(writer=[*LOOP, answer("x", evidence="")], verifier=[]),
            tools,
            action="annotate",
        ),
        await run(
            scripted(writer=[call("run_query", sql=NAIVE_SQL), answer("Hubo 9,999 facturas.")]),
            tools,
            action="annotate",
        ),
        await run(scripted(), tools),
    ]
    for result in runs:
        series = [risk_at(result, k, ONTOLOGY).risk for k in range(len(result.events) + 1)]
        assert series == sorted(series), series
        assert series[-1] == result.swar_risk, "all events is the final assessment"
    loop = runs[0]
    assert risk_at(loop, 6, ONTOLOGY).risk > risk_at(loop, 2, ONTOLOGY).risk, (
        "visible before the end"
    )


def test_replay_uses_only_trajectory_signals_until_the_writer_is_done():
    events = [llm_event("planner"), llm_event("sql_writer"), tool(ok=False), tool(ok=False, h="b")]
    done = AgentResult(
        status="answered", question=QUESTION, answer="Hubo 9,999.", events=events + [llm_event("verifier")],
        evidence=evidence(violations=[WARNING]),
    )  # fmt: skip
    early = {s.name for s in risk_at(done, 3, ONTOLOGY).signals}
    assert "unresolved_violations" not in early, "the writer's outcome is not known after 3 events"
    after_writer = {s.name for s in risk_at(done, 4, ONTOLOGY).signals}
    assert {"unresolved_violations", "figures_not_in_evidence", "sql_rejections"} <= after_writer
