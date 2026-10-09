"""planner -> sql_writer -> verifier (ADR-002).

The orchestrator owns the facts of a run: the SQL, the rows and the request id come from the tool
calls that really happened, never from what a model says about them. It also decides the final
status, so a model cannot talk its way into ``answered`` or ``pending_approval``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from opentelemetry.trace import Tracer

from legacybridge.agent.backends import ToolBackend
from legacybridge.agent.digest import digest, years_covered
from legacybridge.agent.early_warning import EarlyWarning, Escalation, RiskMonitor
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
from legacybridge.agent.schemas import AgentConfig, AgentResult, Evidence, VerdictRecord
from legacybridge.llm.base import InvalidModelOutput, LLMClient, LLMError, ToolSpec
from legacybridge.observability.swar_config import load_swar_config
from legacybridge.observability.swar_detector import RunFacts, assess, facts_from_result
from legacybridge.semantic.loader import Ontology, empty_ontology, load_ontology

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
    "swar_risk": {
        "es": "Detecté señales de que esta respuesta podría ser incorrecta y prefiero no "
        "afirmarla. Respuesta candidata, sin verificar: {candidate}",
        "en": "I found signs that this answer may be wrong, so I am not asserting it. "
        "Unverified candidate: {candidate}",
    },
    "swar_stuck": {
        "es": "No pude completar esta consulta de forma fiable: {why}",
        "en": "I could not complete this request reliably: {why}",
    },
    "clarify_period": {
        "es": "¿De qué periodo quieres esta cifra? Los datos cubren de {first} a {last}.",
        "en": "For which period do you want this figure? The data covers {first} to {last}.",
    },
}


def say(key: str, language: str, **values: Any) -> str:
    return MESSAGES[key][language if language in ("es", "en") else "es"].format(**values)


@dataclass
class Run:
    question: str
    plan: PlannerDecision
    rec: Recorder
    monitor: RiskMonitor
    warnings: list[str] = field(default_factory=list)
    verdicts: list[VerdictRecord] = field(default_factory=list)


def _unique(items: list[str]) -> list[str]:
    return list(dict.fromkeys(i for i in items if i))


class Agent:
    def __init__(
        self,
        llm: LLMClient,
        backend: ToolBackend,
        ontology: Ontology | None = None,
        config: AgentConfig | None = None,
        *,
        escalation_llm: LLMClient | None = None,
        tracer: Tracer | None = None,
    ):
        self.llm, self.backend = llm, backend
        self.config = config or AgentConfig()
        self.ontology = ontology or load_ontology()
        if not self.config.semantic:
            self.ontology = empty_ontology()  # the ablation: no digest, no semantic signals
        self.escalation_llm = escalation_llm
        self.tracer = tracer
        self.swar = load_swar_config()
        self._digest = digest(self.ontology)
        self._specs: list[ToolSpec] | None = None

    # ------------------------------------------------------------------ entry point
    async def ask(self, question: str) -> AgentResult:
        question = question.strip()
        rec = Recorder(tracer=self.tracer)
        with rec.run(question, getattr(self.llm, "name", "")):
            try:
                result = await self._run(question, rec)
            except Escalation as escalation:
                result = await self._escalate(question, rec, escalation.warning)
            self._finish(result, rec)
            rec.annotate_run(result)
        return result

    async def _run(self, question: str, rec: Recorder) -> AgentResult:
        """Plan and route one question on this agent's model."""
        monitor = RiskMonitor(
            question, self.ontology, self.swar, mode=self.config.swar_action,
            can_escalate=self.escalation_llm is not None,
        )  # fmt: skip
        plan = PlannerDecision(action="query")
        run = Run(question, plan, rec, monitor)
        try:
            plan = await self._plan(question, rec)
            run.plan = plan
            result = await self._route(run, plan)
        except LLMError as error:
            result = AgentResult(
                status="abstained", question=question, abstain_reason="llm_error",
                answer=say("llm_error", plan.language, why=str(error)[:200]),
                warnings=[str(error)[:300]],
            )  # fmt: skip
        result.verdicts = run.verdicts
        if monitor.fired is not None and monitor.mode == "annotate":
            result.warnings = _unique(result.warnings + monitor.fired.describe()[:1])
        return result

    async def _escalate(self, question: str, rec: Recorder, warning: EarlyWarning) -> AgentResult:
        """Rerun the question from scratch on the escalation profile, once (ADR-012)."""
        assert self.escalation_llm is not None
        first = getattr(self.llm, "name", "")
        rec.escalated, rec.attempt = True, 0
        second = Agent(
            self.escalation_llm, self.backend, self.ontology, self.config, tracer=self.tracer
        )  # no escalation_llm: a risky second run abstains instead of escalating again
        second._specs = self._specs
        result = await second._run(question, rec)
        result.escalated_from = first
        result.early_warning = warning.checkpoint
        result.profile = getattr(self.escalation_llm, "name", "")
        result.warnings = _unique(
            [f"Escalated from {first} after the early warning {warning.checkpoint}."]
            + result.warnings
        )
        return result

    def _finish(self, result: AgentResult, rec: Recorder) -> None:
        """Usage, trace id and the final SWAR assessment (it is only reported, ADR-012)."""
        result.profile = result.profile or getattr(self.llm, "name", "")
        result.events, result.usage = rec.events, rec.usage()
        result.trace_id = rec.trace_id
        if result.evidence is not None:
            result.defects_touched = list(result.evidence.defects_touched)
        facts = facts_from_result(result)
        # an escalated answer comes from the rerun, so its trajectory is the rerun's
        facts.events = [
            e for e in result.events if e.escalated == (result.escalated_from is not None)
        ]
        assessment = assess(facts, self.ontology, self.swar, "final")
        merged = {sig.name: sig for sig in result.swar_signals}  # set when an early warning acted
        for sig in assessment.signals:
            merged.setdefault(sig.name, sig)
        result.swar_risk = max(assessment.risk, result.swar_risk)
        result.swar_level = self.swar.level(result.swar_risk)
        result.swar_signals = list(merged.values())

    async def _plan(self, question: str, rec: Recorder) -> PlannerDecision:
        try:
            with rec.role("planner"):
                return await run_planner(
                    self.llm, rec, question, self._digest, self.config.semantic
                )
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

    # ------------------------------------------------------------------ the early warning acts
    def _act(self, run: Run, warning: EarlyWarning, candidate: str = "") -> AgentResult:
        if warning.action == "escalate":
            raise Escalation(warning)
        extra = warning.describe()
        if warning.action == "clarify":
            years = years_covered(self.ontology) or (2000, 2005)
            result = AgentResult(
                status="needs_clarification", question=run.question,
                answer=say("clarify_period", run.plan.language, first=years[0], last=years[1]),
                warnings=_unique(run.warnings + extra),
            )  # fmt: skip
        else:
            why = "; ".join(s.detail or s.name for s in warning.assessment.signals)
            result = self._abstain(
                run, "swar_risk", "swar_risk" if candidate else "swar_stuck",
                candidate=candidate, why=why, extra=extra,
            )  # fmt: skip
        result.early_warning = warning.checkpoint
        # The result of an early stop carries no evidence, so the final assessment could not see
        # what made the agent stop: keep the assessment that did.
        result.swar_risk = warning.assessment.risk
        result.swar_level = warning.assessment.level
        result.swar_signals = list(warning.assessment.signals)
        return result

    def _writer_facts(self, run: Run, w: WriterOutcome) -> RunFacts:
        return RunFacts(
            question=run.question, answer=w.answer, assumptions=w.assumptions,
            evidence=self._evidence(w.evidence) if w.evidence is not None else None,
        )  # fmt: skip

    # ------------------------------------------------------------------ read path
    async def _query(self, run: Run) -> AgentResult:
        feedback: str | None = None
        for attempt in range(self.config.max_retries + 1):
            w = await self._writer(run, "query", feedback, attempt)
            run.warnings += w.notes
            if w.loop_reason == "early_warning" and run.monitor.fired is not None:
                return self._act(run, run.monitor.fired, w.answer)
            if w.outcome == "cannot_answer":
                return self._abstain(run, "cannot_answer", "cannot_answer", why=w.answer)
            if w.evidence is None:
                if w.definitional and w.answer and not self._invents(run, w, None):
                    return self._definition(run, w)
                reason = "no_answer" if w.loop_reason != "finished" else "no_evidence"
                return self._abstain(run, reason, reason)
            warning = run.monitor.after_writer(run.rec, self._writer_facts(run, w))
            if warning is not None:
                return self._act(run, warning, w.answer)
            verdict = await self._verify(run, w, "query", attempt) if self.config.verify else None
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
            violations=ev.violations, defects_touched=ev.defects_touched,
        )  # fmt: skip

    # ------------------------------------------------------------------ write path
    async def _write(self, run: Run) -> AgentResult:
        w = await self._writer(run, "write", None, 0)
        run.warnings += w.notes
        if w.loop_reason == "early_warning" and run.monitor.fired is not None:
            return self._act(run, run.monitor.fired, w.answer)
        if w.proposal is None:
            why = w.refusal or w.answer or "no request was created"
            return self._abstain(run, "write_refused", "write_refused", why=why)
        verdict = await self._verify(run, w, "write", 0) if self.config.verify else None
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
    async def _writer(
        self, run: Run, mode: str, feedback: str | None, attempt: int
    ) -> WriterOutcome:
        with run.rec.role("sql_writer", attempt):
            return await run_writer(
                llm=self.llm, backend=self.backend, rec=run.rec, specs=await self._tool_specs(),
                question=run.question, plan=run.plan, digest_text=self._digest, mode=mode,
                config=self.config, feedback=feedback, watch=run.monitor.watcher(run.rec),
            )  # fmt: skip

    async def _verify(self, run: Run, w: WriterOutcome, mode: str, attempt: int) -> Verdict | None:
        try:
            if mode == "write":
                assert w.proposal is not None
                briefing = describe_request(run.question, w.proposal)
            else:
                assert w.evidence is not None
                briefing = describe_for_verifier(
                    run.question, w.answer, w.assumptions, w.evidence, self.config.rows_to_model
                )
            with run.rec.role("verifier", attempt):
                verdict = await run_verifier(
                    llm=self.llm, backend=self.backend, rec=run.rec,
                    specs=await self._tool_specs(), briefing=briefing, mode=mode,
                    config=self.config,
                )  # fmt: skip
        except LLMError as error:
            run.warnings.append(f"The verifier could not run: {str(error)[:200]}")
            return None
        if verdict is None:
            run.warnings.append("The verifier did not give a verdict.")
        else:
            run.verdicts.append(
                VerdictRecord(
                    attempt=attempt,
                    agrees=verdict.agrees,
                    confidence=verdict.confidence,
                    issues=verdict.issues,
                    checks_run=verdict.checks_run,
                )
            )
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
