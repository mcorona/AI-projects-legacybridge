"""Early warning (ADR-012): act on a risky run before it ends, not after.

Two checkpoints:

- **C1**, after ``checkpoint_tool_results`` tool results of the writer, with trajectory signals
  only. It can stop a writer that is going in circles and spare the remaining model calls.
- **C2**, when the writer has finished and before the verifier runs, with result and semantic
  signals as well.

At or above the action threshold the monitor picks one action from what raised the risk: an
*unstated parameter* becomes a clarification question; any other risk escalates to the optional
second profile; with none (or once already escalated) the agent abstains. The final risk is computed
again at the end, including the verifier's verdict, and is only reported.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from legacybridge.agent.events import Recorder
from legacybridge.observability.swar_config import SwarConfig
from legacybridge.observability.swar_detector import RunFacts, SwarAssessment, assess
from legacybridge.semantic.loader import Ontology

Checkpoint = Literal["C1", "C2"]
Action = Literal["clarify", "escalate", "abstain"]


@dataclass
class EarlyWarning:
    checkpoint: Checkpoint
    action: Action
    assessment: SwarAssessment

    def describe(self) -> list[str]:
        a = self.assessment
        lines = [
            f"early warning {self.checkpoint}: risk {a.risk:.2f} ({a.level}), action {self.action}"
        ]
        lines += [
            f"swar: {s.name} - {s.detail}" if s.detail else f"swar: {s.name}" for s in a.signals
        ]
        return lines


class Escalation(Exception):
    """Raised inside a run to hand it to the escalation profile (caught by ``Agent.ask``)."""

    def __init__(self, warning: EarlyWarning):
        super().__init__(f"escalating after {warning.checkpoint}")
        self.warning = warning


class RiskMonitor:
    def __init__(
        self,
        question: str,
        ontology: Ontology,
        cfg: SwarConfig,
        *,
        mode: Literal["gate", "annotate", "off"],
        can_escalate: bool,
    ):
        self.question, self.ontology, self.cfg = question, ontology, cfg
        self.mode, self.can_escalate = mode, can_escalate
        self.fired: EarlyWarning | None = None  # what acted (gate) or would have acted (annotate)

    # ------------------------------------------------------------------ helpers
    def _action(self, assessment: SwarAssessment) -> Action:
        if assessment.has("unstated_parameter"):
            return "clarify"
        return "escalate" if self.can_escalate else "abstain"

    def _segment(self, rec: Recorder) -> list:
        return [e for e in rec.events if e.escalated == rec.escalated]

    def _check(self, checkpoint: Checkpoint, facts: RunFacts, phase) -> EarlyWarning | None:
        assessment = assess(facts, self.ontology, self.cfg, phase)
        if assessment.risk < self.cfg.action_threshold:
            return None
        return EarlyWarning(checkpoint, self._action(assessment), assessment)

    # ------------------------------------------------------------------ C1
    def watcher(self, rec: Recorder) -> Callable[[], bool] | None:
        """For the writer's tool loop: True means stop now."""
        if self.mode == "off":
            return None

        def watch() -> bool:
            events = self._segment(rec)
            tool_results = sum(1 for e in events if e.kind == "tool" and e.role == "sql_writer")
            if tool_results < self.cfg.checkpoint_k:
                return False
            facts = RunFacts(question=self.question, events=events)
            warning = self._check("C1", facts, "trajectory")
            if warning is None:
                return False
            self.fired = warning
            return self.mode == "gate"  # annotate only records it

        return watch

    # ------------------------------------------------------------------ C2
    def after_writer(self, rec: Recorder, facts: RunFacts) -> EarlyWarning | None:
        if self.mode == "off" or self.fired is not None:
            return self.fired if self.mode == "gate" else None
        facts.events = self._segment(rec)
        warning = self._check("C2", facts, "writer")
        if warning is not None:
            self.fired = warning
        return warning if self.mode == "gate" else None
