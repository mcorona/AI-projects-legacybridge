# ADR-007: OpenTelemetry traces with GenAI conventions, viewed in Phoenix

- **Status:** Accepted
- **Date:** 2026-10-08
- **Related:** Phase 5 of `docs/PLAN.md`, ADR-002, ADR-012, `src/legacybridge/observability/`

## Context

The agent already records a flat list of events per question. That is enough for a result object
but not to *see* a run: which model call was slow, which tool call failed, how the verifier's
queries relate to the writer's, or what happened inside the MCP server. CLAUDE.md requires every
LLM call, tool call and agent step to emit a span with `gen_ai.*` and `lb.*` attributes.

## Decision

- **OpenTelemetry**, not a vendor SDK. The agent exports OTLP/HTTP to an **OTel Collector**, which
  forwards to **Arize Phoenix** locally; in Phase 7 only the Collector's configuration changes
  (ADOT to CloudWatch / X-Ray), not the code.
- **Span tree.** `invoke_agent legacybridge` is the root of a question. Under it: one span per role
  and per writer attempt (`invoke_agent <role>`), and below those `chat <model>` (CLIENT) for each
  model call and `execute_tool <tool>` for each tool call. The MCP SDK (2.x) already creates a
  client span and a server span for each tool call and propagates the W3C trace context in the
  request `_meta`; once the server process sets up a provider, those spans nest under ours with no
  extra code.
- **Our own attribute constants.** The `gen_ai.*` constants in the installed
  `opentelemetry-semantic-conventions` are marked deprecated (the conventions moved to another
  repository and are still in *Development*), so `observability/attrs.py` holds the literals from
  the current spec in one place. If a name changes, one file changes.
- **Dual emission for Phoenix.** Phoenix converts `gen_ai.*` to OpenInference when it ingests, but
  features such as input/output and token views depend on OpenInference attributes. Spans carry
  both families (`openinference.span.kind`, `llm.token_count.*`, `input.value`, `output.value`).
- **Custom attributes** carry what the agent knows and a generic tracer does not:
  `lb.defects_touched`, `lb.verifier_agreement`, `lb.confidence`, `lb.swar_risk`, `lb.status`,
  `lb.abstain_reason`, `lb.profile`, `lb.row_count`, `lb.truncated`.
- **Off by default.** `LB_TRACING` unset means no provider is installed and the OTel API is a
  no-op, so tests and users without Phoenix see no change and no cost. A collector that is down must
  never slow down or break the agent: spans are exported in batches with short timeouts.
- **Content is opt-in.** Prompts, SQL and rows are attached to spans only when
  `LB_TRACE_CONTENT=1`, truncated to 2 000 characters. The data is synthetic today; in AWS the
  Collector is where redaction belongs.
- **Compose profile `obs`.** Phoenix and the Collector are behind a profile so `make db-up` stays
  fast; `make obs-up` starts them. Ports are bound to loopback.

## Consequences

- A span's duration for a model call includes any wait on the profile's concurrency limit and any
  retry backoff, which is what the user experienced; the retries are recorded on the span.
- The Collector adds a container and a hop to debug, in exchange for a code path that is identical
  in AWS.
- Phoenix's UI for plain `gen_ai.*` spans is reduced; dual emission avoids depending on it.

## Alternatives considered

1. **Export straight to Phoenix.** One less container, but the AWS move would touch application
   code and there would be nowhere to redact.
2. **Phoenix's `openinference` instrumentation libraries.** They patch provider SDKs; our model
   calls go through our own adapter, and they would not see roles, attempts or `lb.*` data.
3. **Only keep the `events` list.** Not viewable, not joinable with the MCP server's spans.
