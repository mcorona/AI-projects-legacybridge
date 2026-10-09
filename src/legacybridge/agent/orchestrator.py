"""planner -> sql_writer -> verifier (ADR-002).

The orchestrator owns the facts of a run: the SQL, the rows and the request id come from the tool
calls that really happened, never from what a model says about them. It also decides the final
status, so a model cannot talk its way into ``answered`` or ``pending_approval``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from legacybridge.agent.backends import ToolBackend
from legacybridge.agent.digest import digest
from legacybridge.agent.events import Recorder
from legacybridge.agent.evidence import calibrate, numbers_not_in_evidence
from legacybridge.agent.loop import QueryRun
from legacybridge.agent.roles.planner import PlannerDecision, run_planner
from legacybridge.agent.roles.sql_writer import WriterOutcome, run_writer
from legacybridge.agent.roles.verifier import (
    Verdict,
    describe_for_verifier,
    describe_request,
    run_verifier,
)
from legacybridge.agent.schemas import AgentConfig, AgentResult, Evidence
from legacybridge.llm.base import InvalidModelOutput, LLMClient, LLMError, ToolSpec
from legacybridge.semantic.loader import Ontology, load_ontology

MESSAGES = {
    "verification_failed": {
        "es": "No pude verificar esta respuesta de forma independiente, así que no la afirmo. "
        "Respuesta candidata, sin verificar: {candidate}",
        "en": "I could not verify this answer independently, so I am not asserting it. "
        "Unverified candidate: {candidate}",
    },
    "no_evidence": {
        "es": "No pude respaldar una respuesta con una consulta a los datos.",
        "en": "I could not back an answer with a query to the data.",
    },
    "no_answer": {
        "es": "No logré terminar la consulta dentro del límite de pasos.",
        "en": "I could not finish the query within the step limit.",
    },
    "cannot_answer": {
        "es": "No puedo responder esto con los datos disponibles: {why}",
        "en": "I cannot answer this with the available data: {why}",
    },
    "write_refused": {
        "es": "No se pudo crear la solicitud de cambio: {why}",
        "en": "The change request could not be created: {why}",
    },
    "pending": {
        "es": "Solicitud #{id} en espera de aprobación: en {table}, poner {update} donde {where} "
        "({rows} fila(s)). No se ha cambiado nada; una persona debe aprobarla.",
        "en": "Request #{id} is waiting for approval: in {table}, set {update} where {where} "
        "({rows} row(s)). Nothing has been changed; a person must approve it.",
    },
    "clarify": {
        "es": "¿Podrías precisar lo que necesitas?",
        "en": "Could you be more specific about what you need?",
    },
    "abstain": {
        "es": "No puedo responder esto con esta base de datos.",
        "en": "I cannot answer this with this database.",
    },
    "llm_error": {
        "es": "No pude completar la consulta porque el modelo falló: {why}",
        "en": "I could not complete the request because the model failed: {why}",
    },
}


def say(key: str, language: str, **values: Any) -> str:
    return MESSAGES[key][language if language in ("es", "en") else "es"].format(**values)


@dataclass
class Run:
    question: str
    plan: PlannerDecision
    rec: Recorder
    warnings: list[str] = field(default_factory=list)


def _unique(items: list[str]) -> list[str]:
    return list(dict.fromkeys(i for i in items if i))


class Agent:
    def __init__(
        self,
        llm: LLMClient,
        backend: ToolBackend,
        ontology: Ontology | None = None,
        config: AgentConfig | None = None,
    ):
        self.llm, self.backend = llm, backend
        self.config = config or AgentConfig()
        self._digest = digest(ontology or load_ontology())
        self._specs: list[ToolSpec] | None = None

    # ------------------------------------------------------------------ entry point
    async def ask(self, question: str) -> AgentResult:
        question = question.strip()
        rec = Recorder()
        plan = PlannerDecision(action="query")
        try:
            plan = await self._plan(question, rec)
            result = await self._route(Run(question, plan, rec), plan)
        except LLMError as error:
            result = AgentResult(
                status="abstained", question=question, abstain_reason="llm_error",
                answer=say("llm_error", plan.language, why=str(error)[:200]),
                warnings=[str(error)[:300]],
            )  # fmt: skip
        result.profile = getattr(self.llm, "name", "")
        result.events, result.usage = rec.events, rec.usage()
        return result

    async def _plan(self, question: str, rec: Recorder) -> PlannerDecision:
        try:
            return await run_planner(self.llm, rec, question, self._digest)
        except InvalidModelOutput:
            # Failing safe: a read plan cannot even see propose_write, so nothing can be queued.
            return PlannerDecision(action="query", intent=question)

    async def _tool_specs(self) -> list[ToolSpec]:
        if self._specs is None:
            self._specs = await self.backend.list_tools()
        return self._specs

    async def _route(self, run: Run, plan: PlannerDecision) -> AgentResult:
        if plan.action == "clarify":
            text = plan.clarification or say("clarify", plan.language)
            return AgentResult(status="needs_clarification", question=run.question, answer=text)
        if plan.action == "abstain":
            text = plan.reason or say("abstain", plan.language)
            return AgentResult(
                status="abstained",
                question=run.question,
                answer=text,
                abstain_reason="out_of_scope",
            )
        if plan.action == "write":
            return await self._write(run)
        return await self._query(run)

    # ------------------------------------------------------------------ read path
    async def _query(self, run: Run) -> AgentResult:
        feedback: str | None = None
        for attempt in range(self.config.max_retries + 1):
            w = await self._writer(run, "query", feedback)
            run.warnings += w.notes
            if w.outcome == "cannot_answer":
                return self._abstain(run, "cannot_answer", "cannot_answer", why=w.answer)
            if w.evidence is None:
                if w.definitional and w.answer and not self._invents(run, w, None):
                    return self._definition(run, w)
                reason = "no_answer" if w.loop_reason != "finished" else "no_evidence"
                return self._abstain(run, reason, reason)
            verdict = await self._verify(run, w, "query") if self.config.verify else None
            if verdict is not None and not verdict.agrees:
                if attempt < self.config.max_retries:
                    feedback = "\n".join(verdict.issues) or "your figure does not match mine"
                    continue
                return self._abstain(
                    run, "verification_failed", "verification_failed", candidate=w.answer,
                    extra=[f"verifier: {i}" for i in verdict.issues],
                )  # fmt: skip
            return self._answered(run, w, verdict)
        raise AssertionError("unreachable")  # pragma: no cover

    def _invents(self, run: Run, w: WriterOutcome, evidence: QueryRun | None) -> list[str]:
        nothing = QueryRun("-", "", [], [], 0, False, [], [])
        return numbers_not_in_evidence(w.answer, run.question, w.assumptions, evidence or nothing)

    def _answered(self, run: Run, w: WriterOutcome, verdict: Verdict | None) -> AgentResult:
        ev = w.evidence
        assert ev is not None
        invented = self._invents(run, w, ev)
        warnings = list(run.warnings) + list(ev.warnings)
        if invented:
            warnings.append(
                "The answer mentions figures that are not in the query result: "
                + ", ".join(invented)
            )
        if verdict is None:
            warnings.append("This answer was not independently verified.")
        elif verdict.issues:
            warnings += [f"verifier: {i}" for i in verdict.issues]
        return AgentResult(
            status="answered", question=run.question, answer=w.answer, sql=ev.sql,
            confidence=calibrate(verdict=verdict, evidence=ev, invented=invented),
            assumptions=w.assumptions, warnings=_unique(warnings), evidence=self._evidence(ev),
            verified=verdict is not None and verdict.agrees,
        )  # fmt: skip

    def _definition(self, run: Run, w: WriterOutcome) -> AgentResult:
        """An answer taken from the ontology's definitions, with no data behind it."""
        return AgentResult(
            status="answered", question=run.question, answer=w.answer, confidence=0.7,
            assumptions=w.assumptions, warnings=_unique(run.warnings), verified=False,
        )  # fmt: skip

    def _evidence(self, ev: QueryRun) -> Evidence:
        return Evidence(
            query_id=ev.id, sql=ev.sql, columns=ev.columns, row_count=ev.row_count,
            rows=ev.rows[: self.config.evidence_rows], truncated=ev.truncated,
        )  # fmt: skip

    # ------------------------------------------------------------------ write path
    async def _write(self, run: Run) -> AgentResult:
        w = await self._writer(run, "write", None)
        run.warnings += w.notes
        if w.proposal is None:
            why = w.refusal or w.answer or "no request was created"
            return self._abstain(run, "write_refused", "write_refused", why=why)
        verdict = await self._verify(run, w, "write") if self.config.verify else None
        p = w.proposal
        warnings = list(run.warnings)
        if verdict is None:
            warnings.append("This request was not independently verified.")
        elif not verdict.agrees:
            warnings.append(
                "The verifier thinks the queued request may not match what was asked; "
                "review it carefully: " + "; ".join(verdict.issues)
            )
        return AgentResult(
            status="pending_approval", question=run.question, request_id=p["request_id"],
            preview=p.get("preview", []), confidence=calibrate(
                verdict=verdict, evidence=None, invented=[], pending_write=True
            ), assumptions=w.assumptions, warnings=_unique(warnings),
            answer=say(
                "pending", run.plan.language, id=p["request_id"], table=p["table"],
                update=p["update"], where=p["where"], rows=p["affected_rows"],
            ),
            verified=verdict is not None and verdict.agrees,
        )  # fmt: skip

    # ------------------------------------------------------------------ pieces
    async def _writer(self, run: Run, mode: str, feedback: str | None) -> WriterOutcome:
        return await run_writer(
            llm=self.llm, backend=self.backend, rec=run.rec, specs=await self._tool_specs(),
            question=run.question, plan=run.plan, digest_text=self._digest, mode=mode,
            config=self.config, feedback=feedback,
        )  # fmt: skip

    async def _verify(self, run: Run, w: WriterOutcome, mode: str) -> Verdict | None:
        try:
            if mode == "write":
                assert w.proposal is not None
                briefing = describe_request(run.question, w.proposal)
            else:
                assert w.evidence is not None
                briefing = describe_for_verifier(
                    run.question, w.answer, w.assumptions, w.evidence, self.config.rows_to_model
                )
            verdict = await run_verifier(
                llm=self.llm, backend=self.backend, rec=run.rec, specs=await self._tool_specs(),
                briefing=briefing, mode=mode, config=self.config,
            )  # fmt: skip
        except LLMError as error:
            run.warnings.append(f"The verifier could not run: {str(error)[:200]}")
            return None
        if verdict is None:
            run.warnings.append("The verifier did not give a verdict.")
        return verdict

    def _abstain(
        self,
        run: Run,
        code: str,
        message_key: str,
        extra: list[str] | None = None,
        **values: Any,
    ) -> AgentResult:
        return AgentResult(
            status="abstained", question=run.question, abstain_reason=code,
            answer=say(message_key, run.plan.language, **{"why": "", "candidate": "", **values}),
            warnings=_unique(run.warnings + (extra or [])),
        )  # fmt: skip
