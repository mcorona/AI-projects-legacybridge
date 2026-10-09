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


class AgentConfig(BaseModel):
    verify: bool = True  # Phase 6 ablation: with / without verifier
    max_steps: int = 10  # tool-loop turns for the writer
    verifier_steps: int = 6
    max_retries: int = 1  # writer retries after the verifier disagrees
    rows_to_model: int = 30  # rows of a result shown to a model
    evidence_rows: int = 20  # rows kept in the result
