"""What the agent returns (CLAUDE.md: structured outputs, status in a closed set)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

Status = Literal["answered", "needs_clarification", "abstained", "pending_approval"]


class Evidence(BaseModel):
    """The query that supports the answer, taken from the real tool call, not from the model."""

    query_id: str
    sql: str
    columns: list[str]
    rows: list[list[Any]]  # at most 20
    row_count: int
    truncated: bool
    violations: list[dict[str, Any]] = Field(default_factory=list)  # rule engine, structured
    defects_touched: list[str] = Field(default_factory=list)


class Usage(BaseModel):
    llm_calls: int = 0
    tool_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    ms: float = 0.0


class Event(BaseModel):
    """One model call or tool call. Phase 5 turns these into OpenTelemetry spans."""

    role: str  # planner | sql_writer | verifier
    kind: Literal["llm", "tool"]
    name: str  # model name, or tool name
    ok: bool
    ms: float
    input_tokens: int = 0
    output_tokens: int = 0
    detail: str = ""
    retries: list[str] = Field(default_factory=list)
    attempt: int = 0  # writer attempt (0 = first); a retry after the verifier disagreed is 1
    args_hash: str = ""  # tool events: short hash of the arguments, to spot identical repeats
    stage: str = ""  # tool events that failed: guard | execution | connection | ...
    sql: str = ""  # run_query events: the SQL that was sent, truncated
    escalated: bool = False  # recorded during the rerun on the escalation profile


class VerdictRecord(BaseModel):
    """One verifier verdict. A run can have several (a writer retry gets verified again)."""

    attempt: int = 0
    agrees: bool
    confidence: float
    issues: list[str] = Field(default_factory=list)
    checks_run: int = 0


class SwarSignal(BaseModel):
    name: str
    family: Literal["result", "trajectory", "semantics"]
    weight: float
    detail: str = ""


class GuardrailRecord(BaseModel):
    """What the text guardrails did to this question (ADR-014); absent when none is configured."""

    input_findings: list[str] = Field(default_factory=list)
    output_findings: list[str] = Field(default_factory=list)
    action: Literal["blocked_input", "blocked_output", "masked", "error"] | None = None


class AgentResult(BaseModel):
    status: Status
    question: str
    answer: str
    sql: str | None = None
    confidence: float = 0.0  # meaningful for answered / pending_approval; 0 otherwise
    assumptions: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    abstain_reason: str | None = None
    request_id: int | None = None  # pending_approval
    preview: list[dict[str, Any]] = Field(default_factory=list)  # pending_approval
    evidence: Evidence | None = None
    profile: str = ""
    verified: bool = False
    usage: Usage = Field(default_factory=Usage)
    events: list[Event] = Field(default_factory=list)
    verdicts: list[VerdictRecord] = Field(default_factory=list)
    # SWAR (ADR-012): risk of this answer being silently wrong, and why
    swar_risk: float = 0.0
    swar_level: Literal["low", "elevated", "high"] = "low"
    swar_signals: list[SwarSignal] = Field(default_factory=list)
    defects_touched: list[str] = Field(default_factory=list)
    early_warning: str | None = None  # checkpoint that acted: C1 | C2
    escalated_from: str | None = None  # profile of the first run, when it was rerun elsewhere
    trace_id: str | None = None  # OpenTelemetry trace id, when tracing is on
    guardrail: GuardrailRecord | None = None  # set only when a guard is configured


class AgentConfig(BaseModel):
    verify: bool = True  # Phase 6 ablation: with / without verifier
    max_steps: int = 10  # tool-loop turns for the writer
    verifier_steps: int = 6
    max_retries: int = 1  # writer retries after the verifier disagrees
    rows_to_model: int = 30  # rows of a result shown to a model
    evidence_rows: int = 20  # rows kept in the result
    swar_action: Literal["gate", "annotate", "off"] = "gate"  # ADR-012
    semantic: bool = True  # False = the no-semantic ablation: an empty ontology (ADR-013)
