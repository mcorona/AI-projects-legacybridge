"""sql_writer: explores, writes SQL, fixes it from the rule warnings, and submits (ADR-002)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from legacybridge.agent.backends import ToolBackend
from legacybridge.agent.events import Recorder
from legacybridge.agent.loop import QueryRun, run_tool_loop
from legacybridge.agent.roles.planner import PROMPTS, PlannerDecision
from legacybridge.agent.schemas import AgentConfig
from legacybridge.llm.base import LLMClient, ToolSpec

READ_TOOLS = {"list_tables", "describe_table", "sample_rows", "run_query", "explain_business_term"}
WRITE_TOOL = "propose_write"

SUBMIT_ANSWER = ToolSpec(
    "submit_answer",
    "Finish. Give the final answer, naming the query that supports it.",
    {
        "type": "object",
        "properties": {
            "answer": {"type": "string", "description": "The answer, with exact figures"},
            "outcome": {"type": "string", "enum": ["answer", "cannot_answer"]},
            "evidence_query": {
                "type": "string",
                "description": "query_id of the run_query result that supports it (q1...)",
            },
            "assumptions": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["answer", "outcome"],
    },
)


@dataclass
class WriterOutcome:
    outcome: str  # answer | cannot_answer | no_finish
    answer: str = ""
    assumptions: list[str] = field(default_factory=list)
    evidence: QueryRun | None = None
    proposal: dict[str, Any] | None = None  # the real propose_write result, when it succeeded
    refusal: str = ""  # why propose_write refused, if it did
    queries: list[QueryRun] = field(default_factory=list)
    loop_reason: str = ""
    notes: list[str] = field(default_factory=list)
    definitional: bool = (
        False  # answered from definitions only: no query ran, explain_business_term did
    )


def tools_for(specs: list[ToolSpec], mode: str) -> list[ToolSpec]:
    """Least privilege by intent: a read-only plan never sees propose_write (ADR-002)."""
    names = READ_TOOLS | ({WRITE_TOOL} if mode == "write" else set())
    return [s for s in specs if s.name in names]


async def run_writer(
    *,
    llm: LLMClient,
    backend: ToolBackend,
    rec: Recorder,
    specs: list[ToolSpec],
    question: str,
    plan: PlannerDecision,
    digest_text: str,
    mode: str,
    config: AgentConfig,
    feedback: str | None = None,
) -> WriterOutcome:
    write_rules = (
        (PROMPTS / "write_rules.md").read_text(encoding="utf-8") if mode == "write" else ""
    )
    system = (
        (PROMPTS / "sql_writer.md")
        .read_text(encoding="utf-8")
        .replace("{write_rules}", write_rules)
        .replace("{language}", "Spanish" if plan.language == "es" else "English")
        .replace("{digest}", digest_text)
    )
    user = f"Question: {question}\nWhat the planner understood: {plan.intent}"
    if feedback:
        user += (
            "\n\nA verifier reviewed your previous attempt and disagrees:\n"
            f"{feedback}\nRe-check your work, fix what is wrong and finish again."
        )
    loop = await run_tool_loop(
        llm=llm, backend=backend, rec=rec, role="sql_writer", system=system, user=user,
        tools=tools_for(specs, mode), finish=SUBMIT_ANSWER, max_steps=config.max_steps,
        rows_to_model=config.rows_to_model,
    )  # fmt: skip

    out = WriterOutcome(outcome="no_finish", queries=loop.queries, loop_reason=loop.reason)
    proposals = [c for c in loop.calls if c.name == WRITE_TOOL]
    ok = [c.result for c in proposals if c.result.get("ok") is True]
    if ok:
        out.proposal = ok[-1]
    elif proposals:
        last = proposals[-1].result
        out.refusal = f"{last.get('reason', 'refused')}: {last.get('detail', '')}".strip(": ")

    out.definitional = not loop.queries and any(
        c.name == "explain_business_term" and c.result.get("ok") is True for c in loop.calls
    )
    finish = loop.finish
    if finish is None:
        out.answer = loop.text
        if loop.queries and loop.text:
            out.outcome = "answer"  # the model answered in plain text after running queries
            out.evidence = loop.queries[-1]
            out.notes.append(
                "The model did not call submit_answer; its text was used as the answer."
            )
        return out
    out.outcome = "cannot_answer" if finish.get("outcome") == "cannot_answer" else "answer"
    out.answer = str(finish.get("answer", "")).strip()
    out.assumptions = [str(a) for a in finish.get("assumptions") or [] if str(a).strip()]
    named = str(finish.get("evidence_query", "")).strip()
    by_id = {q.id: q for q in loop.queries}
    if named in by_id:
        out.evidence = by_id[named]
    elif loop.queries:
        out.evidence = loop.queries[-1]
        out.notes.append(
            f"evidence_query {named!r} did not match a query that ran; the last one was used."
            if named
            else "evidence_query was not given; the last query that ran was used."
        )
    return out
