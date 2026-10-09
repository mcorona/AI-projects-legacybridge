# ADR-003: Models are reached through profiles and one OpenAI-compatible adapter

- **Status:** Accepted
- **Date:** 2026-10-08
- **Related:** ADR-002, ADR-010, Phase 4 and Phase 7 of `docs/PLAN.md`, `config/models.yaml`,
  `src/legacybridge/llm/`

## Context

The agent must run on a local model (LM Studio), on free-tier models behind a gateway (OmniRoute)
and, in Phase 7, on Bedrock. Each has different limits: the local one is slow and spends its token
budget thinking, the free tier rate-limits, and the gateway may fail transiently.

## Decision

- Agent code depends only on `llm.base.LLMClient.chat(messages, tools, ...)`. No provider SDK is
  imported outside `src/legacybridge/llm/`.
- A **profile** in `config/models.yaml` names a client, its endpoint, model and limits, with
  secrets and addresses read from environment variables:
  `local-qwen` (LM Studio) and `omniroute-free` (OmniRoute), plus `bedrock-claude` in Phase 7.
- One adapter, `openai_compat`, serves both current profiles. It:
  - limits concurrency per profile (`max_concurrency`; 1 for the free tier),
  - retries rate limits (429, honouring `Retry-After`), server errors, timeouts and **empty
    completions** with exponential backoff and jitter (`retries`),
  - strips `<think>` blocks and ignores `reasoning_content` (reasoning is not an answer),
  - keeps tool arguments that arrive as invalid JSON under `_raw` so the loop can ask again,
  - gives reasoning models a generous `max_tokens`, because a thinking model that runs out of
    budget returns an empty answer.
- A profile marked `synthetic_data_only` (ADR-010) **refuses to start** unless the database says it
  is synthetic (`lb.synthetic = 'true'`, set by `db/init`). The promise is enforced, not trusted.
- Temperature is 0 by default so runs are as repeatable as the model allows.

## Consequences

- Adding Bedrock (Phase 7) is a new adapter and a new profile; the agent does not change.
- Rate limits and retries are visible in the result's events, which makes a slow free tier
  explainable instead of mysterious.
- Results from `omniroute-free` depend on which models are available that day, so they are
  labelled best-effort and kept out of the CI gate (ADR-010).

## Alternatives considered

1. **LiteLLM or another abstraction layer.** One more dependency that hides retries and token
   accounting, which we need to see.
2. **One adapter per provider from the start.** The two current providers speak the same API.
3. **Disable the local model's reasoning to save time.** It lowers SQL quality, which is exactly
   what the project measures.
