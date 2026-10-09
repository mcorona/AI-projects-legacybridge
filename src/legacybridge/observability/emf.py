"""One CloudWatch metrics record per question, in the Embedded Metric Format (ADR-014).

A Lambda writes it to stdout; CloudWatch Logs turns it into metrics with no agent, collector or API
call, so the dashboard has data without the trace pipeline. The record carries numbers and labels,
never the question or the answer.
"""

from __future__ import annotations

import json
import time
from typing import Any

from legacybridge.agent.schemas import AgentResult

NAMESPACE = "LegacyBridge"
# Every custom metric is billed as a series per combination of dimension values, so the dimensions
# are kept to what the dashboard reads: each metric by profile, and only the question count by
# status. (The first design repeated all 11 metrics for every status: 55 series a profile, four
# times the cost for nothing the dashboard shows. The cost model, scripts/cost_model.py, found it.)
DIMENSIONS = ["Profile"]
STATUS_DIMENSIONS = ["Profile", "Status"]
STATUS_METRICS = ("Questions",)
UNITS = {
    "LatencyMs": "Milliseconds",
    "InputTokens": "Count",
    "OutputTokens": "Count",
    "LlmCalls": "Count",
    "CostUsd": "None",
    "SwarRisk": "None",
    "Questions": "Count",
    "Answered": "Count",
    "GuardrailIntervened": "Count",
    "GuardrailBlocked": "Count",
    "GuardrailErrors": "Count",
}


def series_per_profile(statuses: int) -> int:
    """Custom metrics one profile creates in CloudWatch, which bills each one by the month."""
    return len(UNITS) + len(STATUS_METRICS) * statuses


def cost_usd(result: AgentResult, price_in: float, price_out: float) -> float:
    """Tokens times the profile's price per million; zero for a local or free profile."""
    return (
        result.usage.input_tokens * price_in + result.usage.output_tokens * price_out
    ) / 1_000_000


def metrics_for(
    result: AgentResult, price_in: float = 0.0, price_out: float = 0.0
) -> dict[str, float]:
    guard = result.guardrail
    values: dict[str, float] = {
        "Questions": 1,
        "Answered": 1 if result.status in ("answered", "pending_approval") else 0,
        "LatencyMs": result.usage.ms,
        "InputTokens": result.usage.input_tokens,
        "OutputTokens": result.usage.output_tokens,
        "LlmCalls": result.usage.llm_calls,
        "CostUsd": round(cost_usd(result, price_in, price_out), 6),
        "SwarRisk": result.swar_risk,
        # an error is not an intervention: the guard did not judge anything, and the agent closed
        "GuardrailIntervened": 1
        if guard and guard.action in ("blocked_input", "blocked_output", "masked")
        else 0,
        "GuardrailBlocked": 1 if guard and (guard.action or "").startswith("blocked") else 0,
        "GuardrailErrors": 1 if guard and guard.action == "error" else 0,
    }
    return values


def emf_record(
    result: AgentResult, *, price_in: float = 0.0, price_out: float = 0.0, now_ms: int | None = None
) -> dict[str, Any]:
    """The record as a dict: ``_aws`` names the metric keys, the others are values and labels."""
    values = metrics_for(result, price_in, price_out)
    record: dict[str, Any] = {
        "_aws": {
            "Timestamp": now_ms if now_ms is not None else int(time.time() * 1000),
            "CloudWatchMetrics": [
                {
                    "Namespace": NAMESPACE,
                    "Dimensions": [DIMENSIONS],
                    "Metrics": [{"Name": k, "Unit": UNITS[k]} for k in values],
                },
                {
                    "Namespace": NAMESPACE,
                    "Dimensions": [STATUS_DIMENSIONS],
                    "Metrics": [{"Name": k, "Unit": UNITS[k]} for k in STATUS_METRICS],
                },
            ],
        },
        "Profile": result.profile or "unknown",
        "Status": result.status,
        "AbstainReason": result.abstain_reason or "",
        "SwarLevel": result.swar_level,
        "TraceId": result.trace_id or "",
    }
    return record | values


def emit(result: AgentResult, *, price_in: float = 0.0, price_out: float = 0.0) -> None:
    print(json.dumps(emf_record(result, price_in=price_in, price_out=price_out)), flush=True)
