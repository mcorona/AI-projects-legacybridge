"""SWAR detector (ADR-012): how likely is an answer to be silently wrong?

``assess`` is a pure function of the facts of a run: no database, no model, and no use of the
calibrated ``confidence`` (the Phase 6 SWAR rate is defined from it, so using it here would be
circular). It returns a risk in [0, 1], a level, and the signals that fired, in three families:

- result:     the verifier disagreed, rule violations left unresolved, a confident answer over zero
              rows, truncation not acknowledged, figures not in the evidence, no independent check;
- trajectory: repeated identical tool calls, a cascade of failures, rejected SQL, retries;
- semantics:  a term of the question whose known defects the SQL violates, or a period the
              question needed and nobody stated.

Signals carry ``available_at`` so the same function serves the early-warning checkpoints and the
final assessment, and so a stored run can be replayed with only its first *k* events (``risk_at``).
Weights and thresholds are priors in ``config/swar.yaml``; they are not fitted to any data.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from math import prod
from typing import Any, Literal

from legacybridge.agent.evidence import numbers_not_in_evidence
from legacybridge.agent.loop import QueryRun
from legacybridge.agent.schemas import AgentResult, Event, Evidence, SwarSignal, VerdictRecord
from legacybridge.observability.swar_config import PHASES, SwarConfig, load_swar_config
from legacybridge.semantic.loader import Ontology

Phase = Literal["trajectory", "writer", "final"]
YEAR = re.compile(r"(?<!\d)(19|20)\d{2}(?!\d)")
# A question that asks for everything explicitly does not need a period.
WHOLE_PERIOD = re.compile(
    r"en total|hist[oó]ric|acumulad|todos los a[ñn]os|desde siempre|desde el inicio|por a[ñn]o|"
    r"por mes|all[- ]time|overall|in total|every year|each year|per year|per month",
    re.IGNORECASE,
)
ACKNOWLEDGES_TRUNCATION = re.compile(
    r"trunc|parcial|partial|primer[oa]s?\s+\d|first\s+\d|solo\s+\d|only\s+\d|"
    r"de\s+\d+\s+(filas|registros|rows)|l[ií]mite|limit|lista\s+completa|complete list|showing",
    re.IGNORECASE,
)


@dataclass
class RunFacts:
    """What the detector needs to know about a run. Every part may be missing mid-run."""

    question: str
    status: str | None = None  # None while the run is in progress
    abstain_reason: str | None = None
    answer: str = ""
    assumptions: list[str] = field(default_factory=list)
    events: list[Event] = field(default_factory=list)
    evidence: Evidence | None = None
    verdicts: list[VerdictRecord] = field(default_factory=list)


@dataclass
class SwarAssessment:
    risk: float
    level: Literal["low", "elevated", "high"]
    signals: list[SwarSignal]
    phase: Phase = "final"

    def has(self, name: str) -> bool:
        return any(s.name == name for s in self.signals)


def facts_from_result(result: AgentResult) -> RunFacts:
    return RunFacts(
        question=result.question,
        status=result.status,
        abstain_reason=result.abstain_reason,
        answer=result.answer,
        assumptions=result.assumptions,
        events=result.events,
        evidence=result.evidence,
        verdicts=result.verdicts,
    )


# ------------------------------------------------------------------------------ the signals
class _Collector:
    def __init__(self, cfg: SwarConfig, phase: Phase):
        self.cfg, self.reached = cfg, PHASES.index(phase)
        self.signals: list[SwarSignal] = []

    def fire(self, name: str, detail: str = "") -> None:
        spec = self.cfg.signals[name]
        if PHASES.index(spec.available_at) > self.reached:
            return  # not knowable yet
        self.signals.append(
            SwarSignal(name=name, family=spec.family, weight=spec.weight, detail=detail[:200])
        )


def _trajectory(facts: RunFacts, c: _Collector) -> None:
    t = c.cfg.trajectory
    tools = [e for e in facts.events if e.kind == "tool"]
    repeats = Counter((e.role, e.escalated, e.name, e.args_hash) for e in tools if e.args_hash)
    worst = max(repeats.items(), key=lambda kv: kv[1], default=(None, 0))
    if worst[1] >= t["repeated_calls"]:
        c.fire(
            "repeated_tool_call", f"{worst[0][2]} called {worst[1]} times with the same arguments"
        )

    run = longest = 0
    for e in tools:
        run = run + 1 if not e.ok else 0
        longest = max(longest, run)
    if longest >= t["cascade_length"]:
        c.fire("error_cascade", f"{longest} tool calls failed in a row")

    rejected = sum(1 for e in tools if e.name == "run_query" and not e.ok)
    if rejected >= t["sql_rejections_many"]:
        c.fire("sql_rejections_many", f"{rejected} run_query calls failed")
    elif rejected >= t["sql_rejections"]:
        c.fire("sql_rejections", f"{rejected} run_query calls failed")

    if any(e.attempt >= 1 for e in facts.events):
        c.fire("writer_retry", "the writer retried after the verifier disagreed")
    trouble = [e for e in facts.events if e.kind == "llm" and (e.retries or not e.ok)]
    if trouble:
        c.fire("model_retries", f"{len(trouble)} model call(s) were retried or failed")
    if facts.abstain_reason == "no_answer":
        c.fire("step_limit", "the writer hit its step limit")


def _as_query(ev: Evidence) -> QueryRun:
    return QueryRun(ev.query_id, ev.sql, ev.columns, ev.rows, ev.row_count, ev.truncated, [], [])


def _result(facts: RunFacts, c: _Collector) -> None:
    ev, status = facts.evidence, facts.status
    answering = status in (None, "answered")
    if ev is not None:
        warnings = [v for v in ev.violations if v.get("severity") == "warning"]
        if warnings:
            ids = sorted({v.get("defect", "?") for v in warnings})
            c.fire("unresolved_violations", "the final query still breaks " + ", ".join(ids))
        if ev.row_count == 0 and answering:
            c.fire("zero_rows", "the answer rests on a query that returned no rows")
        if ev.truncated and answering and not ACKNOWLEDGES_TRUNCATION.search(facts.answer):
            c.fire(
                "truncation_unacknowledged", "the result was truncated and the answer says nothing"
            )
        if len(ev.rows) >= ev.row_count:  # with cut rows we cannot tell whether a figure is missing
            invented = numbers_not_in_evidence(
                facts.answer, facts.question, facts.assumptions, _as_query(ev)
            )
            if invented:
                c.fire("figures_not_in_evidence", "not in the result: " + ", ".join(invented[:5]))
    elif status == "answered":
        c.fire("no_evidence", "answered with no query behind it")

    verdicts = facts.verdicts
    if verdicts:
        last = verdicts[-1]
        if not last.agrees:
            c.fire("verifier_disagreed_final", "; ".join(last.issues)[:160])
        elif any(not v.agrees for v in verdicts):
            c.fire("verifier_disagreed_retried", "the verifier disagreed with an earlier attempt")
        elif last.checks_run == 0 and status != "pending_approval":
            c.fire("no_independent_check", "the verifier re-derived nothing")
    elif status in ("answered", "pending_approval"):
        c.fire("unverified", "no verifier verdict")


def _semantics(facts: RunFacts, c: _Collector, ontology: Ontology) -> None:
    terms = ontology.terms_in(facts.question)
    if facts.evidence is not None:
        violated = {
            v.get("defect") for v in facts.evidence.violations if v.get("severity") == "warning"
        }
        mentioned = {d for t in terms for d in t.defects}
        both = sorted(violated & mentioned)
        if both:
            c.fire("question_defect_violated", "the question touches " + ", ".join(both))
    needs = [t for t in terms if t.requires_period]
    if needs and facts.status in (None, "answered") and facts.evidence is not None:
        said = " ".join([facts.question, facts.answer, *facts.assumptions])
        if not YEAR.search(said) and not WHOLE_PERIOD.search(facts.question):
            c.fire("unstated_parameter", f"'{needs[0].name}' needs a period and none was given")


# ------------------------------------------------------------------------------ combination
def combine(signals: list[SwarSignal], cfg: SwarConfig) -> float:
    """Noisy-OR over the signals; signals in a capped group are combined first and then capped."""
    grouped: dict[str, list[float]] = {}
    for s in signals:
        grouped.setdefault(cfg.signals[s.name].group or s.name, []).append(s.weight)
    keep = 1.0
    for group, weights in grouped.items():
        p = 1 - prod(1 - w for w in weights)
        if group in cfg.groups:
            p = min(p, cfg.groups[group]["cap"])
        keep *= 1 - p
    return round(min(0.99, 1 - keep), 3)


def assess(
    facts: RunFacts,
    ontology: Ontology,
    cfg: SwarConfig | None = None,
    phase: Phase = "final",
) -> SwarAssessment:
    cfg = cfg or load_swar_config()
    c = _Collector(cfg, phase)
    _trajectory(facts, c)
    _result(facts, c)
    _semantics(facts, c, ontology)
    risk = combine(c.signals, cfg)
    return SwarAssessment(risk, cfg.level(risk), c.signals, phase)


def risk_at(
    result: AgentResult, k: int, ontology: Ontology, cfg: SwarConfig | None = None
) -> SwarAssessment:
    """The risk a monitor would have seen after the first ``k`` events of a stored run.

    Before the writer's last event only trajectory signals exist; from there on the writer's
    outcome is known; with every event, the final assessment. This is what backs the claim that a
    risk computed from the first spans is already informative.
    """
    events = result.events[:k]
    writer_done = max(
        (i for i, e in enumerate(result.events) if e.role == "sql_writer"), default=-1
    )
    if k >= len(result.events):
        return assess(facts_from_result(result), ontology, cfg, "final")
    facts = facts_from_result(result)
    facts.events = events
    if k > writer_done:
        facts.status, facts.verdicts = None, []
        return assess(facts, ontology, cfg, "writer")
    facts.evidence, facts.status, facts.verdicts, facts.answer = None, None, [], ""
    return assess(facts, ontology, cfg, "trajectory")


def dominant_family(signals: list[SwarSignal]) -> str | None:
    """The family of the heaviest signal, for choosing what to do about a risk."""
    return max(signals, key=lambda s: s.weight).family if signals else None


__all__: list[Any] = [
    "RunFacts",
    "SwarAssessment",
    "assess",
    "combine",
    "dominant_family",
    "facts_from_result",
    "risk_at",
]
