# ADR-002: Our own model-agnostic agent loop with an independent verifier

- **Status:** Accepted
- **Date:** 2026-10-08
- **Related:** Phase 4 of `docs/PLAN.md`, ADR-001, ADR-003, ADR-005, `src/legacybridge/agent/`

## Context

The agent has to answer questions over a database whose errors are silent, and it has to run on
very different models (a local Qwen, free-tier combos behind OmniRoute, Bedrock later). To compare
them fairly, and to trace every step, we need to control the loop. Models also overstate their own
certainty: in the previous version of this project the confidence a local model reported was almost
always 1.0, so it carried no information.

## Decision

An asynchronous loop of our own with three roles, each with its own prompt:

| Role | Does | Tools |
|---|---|---|
| **planner** | Reads the question and a digest of the ontology; decides `query`, `clarify`, `abstain` or `write` | none |
| **sql_writer** | Explores, writes SQL, fixes it from `reason`/`hint`/`violations`, ends with `submit_answer` | MCP tools; `propose_write` only for a `write` plan |
| **verifier** | Re-derives a count or total with a *different* query and judges the result | read-only MCP tools |

- **The verifier is independent.** It never sees the writer's reasoning, only the question, the final
  SQL, its result and the warnings. If it disagrees, the writer gets one retry with the critique;
  if the disagreement persists the agent **abstains** (`verification_failed`) and labels the
  candidate answer as unverified. Saying nothing is cheaper than being confidently wrong.
- **Facts come from tool calls, not from the model.** The SQL, the rows and the request id in the
  result are taken from the real `run_query` / `propose_write` calls. The model only points at the
  query that supports its answer (`evidence_query`).
- **Confidence is computed, not asked.** It starts from the verifier's verdict and is lowered by
  rule violations, truncation, zero rows behind a numeric answer, a missing independent check, and
  figures in the answer that do not appear in the evidence.
- **Least privilege by intent.** A `query` plan never sees `propose_write`; a model misled while
  reading has nothing to queue a change with. Approval is not a tool at all (ADR-005).
- **Tools sit behind a `ToolBackend`.** `McpToolBackend` talks to the real server over stdio, as
  users do, so evaluations measure what users get; `LocalToolBackend` calls `LegacyTools` directly
  for tests and for the Lambda deployment (Phase 7).
- Business concepts enter the prompts at run time from the ontology, never hard-coded in them.
- Every LLM and tool call is recorded as an event (role, name, tokens, latency, outcome) in the
  result; Phase 5 turns these into OpenTelemetry spans.

## Consequences

- A typical question costs about 8 to 10 model calls. With a local model that is minutes, so the
  loop is deliberately economical (no tool-less "thinking" calls, short tool outputs).
- `verify`, `max_steps` and `max_retries` are configuration, which the Phase 6 ablation
  (with / without verifier) needs.
- The loop is plain Python; no agent framework decides what runs.

## Alternatives considered

1. **Claude Agent SDK or Bedrock Agents.** They fix the model and hide the loop, which defeats
   comparing models and tracing steps.
2. **A single prompt that plans, writes and checks.** Cheaper, but the same model that made the
   mistake also grades it.
3. **Trusting the model's own confidence.** Measured to carry no information.
