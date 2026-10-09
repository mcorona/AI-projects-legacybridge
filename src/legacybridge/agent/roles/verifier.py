"""verifier: judges an answer independently of the writer's reasoning (ADR-002)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from legacybridge.agent.backends import ToolBackend
from legacybridge.agent.events import Recorder
from legacybridge.agent.loop import QueryRun, compact, run_tool_loop
from legacybridge.agent.prompt_loader import load_prompt
from legacybridge.agent.schemas import AgentConfig
from legacybridge.llm.base import LLMClient, ToolSpec

VERIFIER_TOOLS = {"describe_table", "explain_business_term", "run_query"}


def verifier_tools(semantic: bool = True) -> set[str]:
    return VERIFIER_TOOLS if semantic else VERIFIER_TOOLS - {"explain_business_term"}


SUBMIT_VERDICT = ToolSpec(
    "submit_verdict",
    "Finish. Say whether the answer is supported.",
    {
        "type": "object",
        "properties": {
            "agrees": {"type": "boolean"},
            "confidence": {"type": "number", "description": "0 to 1"},
            "checks": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "what": {"type": "string"},
                        "sql": {"type": "string"},
                        "matches": {"type": "boolean"},
                    },
                },
            },
            "issues": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["agrees", "confidence"],
    },
)


@dataclass
class Verdict:
    agrees: bool
    confidence: float
    issues: list[str] = field(default_factory=list)
    checks_run: int = 0  # successful run_query calls the verifier really made, not what it claims


def _clamp(value: Any, default: float) -> float:
    try:
        return min(1.0, max(0.0, float(value)))
    except (TypeError, ValueError):
        return default


def describe_for_verifier(
    question: str,
    answer: str,
    assumptions: list[str],
    evidence: QueryRun,
    rows_to_model: int,
) -> str:
    result = {
        "columns": evidence.columns, "row_count": evidence.row_count,
        "truncated": evidence.truncated, "rows": evidence.rows,
    }  # fmt: skip
    return "\n".join(
        [
            f"Question: {question}",
            f"Agent's answer: {answer}",
            f"Assumptions: {'; '.join(assumptions) or 'none'}",
            f"Final SQL: {evidence.sql}",
            f"Result: {compact(result, rows_to_model)}",
            "Warnings from the rule engine: " + (" | ".join(evidence.warnings) or "none"),
        ]
    )


def describe_request(question: str, proposal: dict[str, Any]) -> str:
    request = {
        k: proposal.get(k)
        for k in ("table", "update", "where", "affected_rows", "preview", "request_id")
    }
    return (
        f"User's request: {question}\n"
        f"Queued request: {json.dumps(request, ensure_ascii=False, default=str)}"
    )


async def run_verifier(
    *,
    llm: LLMClient,
    backend: ToolBackend,
    rec: Recorder,
    specs: list[ToolSpec],
    briefing: str,
    mode: str,
    config: AgentConfig,
) -> Verdict | None:
    """None when the verifier produced no verdict (that is not a disagreement)."""
    prompt = "verifier_write.md" if mode == "write" else "verifier.md"
    loop = await run_tool_loop(
        llm=llm, backend=backend, rec=rec, role="verifier",
        system=load_prompt(prompt, config.semantic), user=briefing,
        tools=[s for s in specs if s.name in verifier_tools(config.semantic)],
        finish=SUBMIT_VERDICT,
        max_steps=config.verifier_steps, rows_to_model=config.rows_to_model,
    )  # fmt: skip
    if loop.error is not None:
        raise loop.error
    if loop.finish is None:
        return None
    f = loop.finish
    return Verdict(
        agrees=f.get("agrees") is True,
        confidence=_clamp(f.get("confidence"), 0.5),
        issues=[str(i) for i in f.get("issues") or [] if str(i).strip()],
        checks_run=len(loop.queries),
    )
