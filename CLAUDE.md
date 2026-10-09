# CLAUDE.md — LegacyBridge

## What this project is
LegacyBridge: a reliable AI agent over **legacy enterprise databases** (inventory `sicpt` + a 2000-era invoicing schema with documented defects). It has a secure MCP server, a semantic/ontology layer, a model-agnostic agent (planner / sql_writer / verifier) with human-in-the-loop writes, OpenTelemetry tracing with **SWAR** (Silently Wrong At Runtime) detection, reproducible evals, and an AWS deployment (Bedrock, Guardrails, Knowledge Bases, CDK).

The full roadmap is in `docs/PLAN.md`. **Work one phase at a time** and update its checkboxes when done.

## Stack
- Python 3.12, `uv`, `ruff`, `pytest`
- `mcp` (official SDK), `sqlglot`, `psycopg`, `pydantic`, `fastapi`
- LLM runtime profiles live in `config/models.yaml` (env vars in `.env.example`):
  - `local-qwen`: `openai_compat`, LM Studio at `http://localhost:1234/v1`, Qwen3.6-35B-A3B
  - `omniroute-free`: `openai_compat`, OmniRoute at `http://localhost:20128/v1`, free model combo with tool calling; `max_concurrency=1`, `retries=3` with backoff (ADR-010)
  - `bedrock-claude` (AWS Bedrock Converse API): added in Phase 7. Phase 4 is validated on the two profiles above; Bedrock is validated in Phase 7
- Postgres 16 in Docker; roles `lb_reader` (SELECT only) and `lb_writer` (approved writes only)
- OpenTelemetry → Phoenix (local, :6006) / CloudWatch (AWS)
- AWS CDK (Python) in `infra/`

## Commands
```bash
make db-up        # start postgres + observability stack
make seed         # deterministic seed (seed=42)
make db-reset     # drop & recreate
make test         # uv run pytest
make lint         # uv run ruff check . && uv run ruff format --check .
make eval-gate    # model-free regression gate (what CI runs): attacks, oracle, detector
make eval         # full evaluation, hours: both profiles + ablations on local-qwen, then the report
make eval-report  # rebuild evals/reports/ and docs/eval-report.md from stored results
uv run python -m legacybridge.mcp_server    # MCP server (stdio)
uv run legacybridge ask "<question>" --profile local-qwen
```

## Conventions
- Source in `src/legacybridge/`; tests mirror the module path in `tests/`.
- Business rules and defects live in `src/legacybridge/semantic/ontology.yaml` and `docs/legacy-defects.md`, never hard-coded in prompts.
- Agent outputs are Pydantic models with `status ∈ {answered, needs_clarification, abstained, pending_approval}`.
- Every LLM call, tool call, and agent step emits an OTel span with `gen_ai.*` attributes plus `lb.*` custom attributes.
- New architectural decisions → add `docs/adr/ADR-NNN-<slug>.md` before implementing.
- Code, comments, and docs in English. The plan (`docs/PLAN.md`) is in Spanish.

## Hard rules
- Never write to the database outside the HITL approval flow.
- `omniroute-free` receives synthetic data only (ADR-010); never route real or customer data through it.
- Never weaken `sql_guard.py` to make a test pass; fix the test or the query.
- Never commit `.env`, credentials, or AWS account identifiers.
- Do not run `cdk deploy`, create AWS resources, or call paid APIs in bulk without explicit confirmation.
- Do not modify `evals/golden_set.yaml` expected results without asking; it is the source of truth.
- A phase is done only when its "Verificación" commands pass and its DoD is met.
