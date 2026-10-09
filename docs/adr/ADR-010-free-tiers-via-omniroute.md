# ADR-010: Free tiers via OmniRoute for development and evals

- **Status:** Accepted
- **Date:** 2026-10-08
- **Related:** ADR-003 (model access), `config/models.yaml`, Phase 4 and Phase 6 of `docs/PLAN.md`

## Context

Phases 4 to 6 need many LLM calls: agent development, golden-set runs (80 cases) and ablations
(with/without semantic layer, with/without verifier). Running all of that on Bedrock costs money
before the AWS phase (Phase 7) and a budget alert exists only from Phase 7. A local model
(`local-qwen`, LM Studio) is free but is a single model on a single machine, so it cannot show
that the agent is model-agnostic across independent providers.

OmniRoute is an OpenAI-compatible gateway that can route to a combo of free-tier models. It costs
nothing, but free tiers come with rate limits, changing model availability and data-handling terms
that we do not control.

## Decision

Add a second runtime profile, `omniroute-free`, next to `local-qwen`:

- `kind: openai_compat`, `base_url` `http://localhost:20128/v1`, a combo of free models that
  **supports tool calling** (a combo without tool calling is not acceptable for the agent).
- **`max_concurrency=1`** and **`retries=3` with exponential backoff** (and jitter, honouring
  `Retry-After` on HTTP 429) to stay inside free-tier rate limits.
- **Synthetic data only** (`synthetic_data_only: true` in the profile). Prompts, tool results and
  SQL results are sent to third-party free-tier providers whose logging and training terms we
  assume are permissive. The seed data is deterministic and synthetic (`seed=42`), so nothing real
  leaves the machine. Real or customer data must never go through this profile.
- Phase 4 DoD is validated on `local-qwen` and `omniroute-free`. `bedrock-claude` is added and
  validated in Phase 7.
- `omniroute-free` is **not** part of the CI eval gate: its results depend on which free models
  are available that day, so they are not reproducible enough to gate a PR. The gate uses the
  local profile or a mock (Phase 6).

## Consequences

- **Cost:** development and eval iterations cost $0 until Phase 7. The cost-per-question metric
  for `omniroute-free` is reported as $0 and flagged as "free tier, not comparable" in
  `docs/cost-model.md`.
- **Rate limits and latency:** serial calls make eval runs slow; 429s and timeouts are expected
  and handled by the retry policy. Latency p50/p95 for this profile are not comparable with the
  other profiles.
- **Reproducibility:** the upstream model behind the combo may change between runs. Every eval
  report records the profile, the resolved model name returned by the API and the run date, and
  `omniroute-free` numbers are labelled as best-effort.
- **Privacy:** enforced by convention and by the profile flag; the profile loader must refuse to
  start `omniroute-free` against a database that is not the synthetic one.
- **Risk:** a free provider can be withdrawn or change terms. The agent only depends on the
  `openai_compat` adapter, so swapping the combo or dropping the profile is a configuration change.

## Alternatives considered

1. **Local model only.** Free and private, but a single model and machine; weaker evidence of
   model-agnosticism.
2. **Bedrock only.** Best quality and the production target, but it spends budget on iteration and
   would pull AWS setup (Phase 7) forward.
3. **Direct free-tier provider APIs.** Removes the gateway but multiplies adapters, keys and
   rate-limit logic that OmniRoute already centralises.
