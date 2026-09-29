# LegacyBridge

**An AI agent that safely answers business questions over legacy enterprise databases through MCP —
with verifiable evidence, reproducible evaluation, layered guardrails and a cost-aware model cascade.**

> Status: ✅ Phases 0–4 · ◐ Phase 5 (cascade and telemetry built; the Bedrock benchmark is pending an
> AWS account-level block) · ✅ Phase 6 publication. See [`docs/PLAN.md`](docs/PLAN.md).

## Why
Critical business data still lives in decades-old ERPs: cryptic table names (`cliemae`, `artmae`),
no foreign keys, dates stored as `VARCHAR(8)`, `CHAR(1)` flags where `NULL` means "no", magic status
codes, mixed units and currencies. Generic text-to-SQL demos return confident, wrong numbers on them.

LegacyBridge encodes that institutional knowledge — schema tools, a business dictionary and ten
documented legacy defects ([`docs/LEGACY_DEFECTS.md`](docs/LEGACY_DEFECTS.md)) — and proves every
answer with the SQL it actually ran and the rows it got back.

## Results
Held-out **test split** (90 questions), synthetic ERP data with every defect seeded, local
**Qwen3.6-35B-A3B** on LM Studio. Every figure links to a versioned report with a reproducibility
fingerprint (commit, golden-set, prompt, tool-spec, data and index hashes, model ids, prices).

| Metric | Local Qwen (×3 runs) | OmniRoute free tier (×1) |
|---|---|---|
| **Execution accuracy** (result sets, not SQL text) | **95.4%** [95.0–96.2] | 88.8% |
| **SWAR** — silent wrong answers (wrong *and* confidence ≥ 0.6) | **3.8%** [2.5–5.0] | 11.2% |
| Runs completed | 97.0% | 100% |
| False refusals on legitimate questions | 0% | 0% |
| Latency p50 / p95 | 19 s / 43 s | 17 s / 28 s |
| Real cost | **$0** | $0 |

By level (local): easy 100% · joins & business rules 96.7% · legacy-defect traps 86.7%.
Reports: [local](evals/reports/2026-09-28-2307-test-local.md) ·
[OmniRoute](evals/reports/2026-09-29-0051-test-omniroute.md) ·
[comparison](evals/reports/2026-09-29-phase5-comparison.md).

**Security** — adversarial prompts, 3 runs each ([golden set](evals/reports/2026-09-28-adversarial-local-rescored.md),
[held-out](evals/reports/2026-09-28-holdout-local-rescored.md)):

| Attack set | Expected behavior | Safe handling | Leaks | Writes executed |
|---|---|---|---|---|
| 15 golden-set attacks | 80.0% | 100% | 0 | 0 |
| 15 held-out attacks, written *before* the defenses | 93.3% | 100% | 0 | 0 |

How the numbers moved: Phase 3 baseline 77.9% accuracy / 5.4% SWAR / 84.1% completed → current
95.4% / 3.8% / 97.0%, after did-you-mean hints for cryptic names, an evidence pointer and the
Phase 4 guardrails. Measurement corrections are published next to the original numbers
([ADR-005](docs/adr/005-evaluation-methodology.md), [ADR-006](docs/adr/006-guardrails-and-human-in-the-loop.md)).

## Architecture
```mermaid
flowchart LR
    U([User / MCP client]) --> GIn[check_input<br/>injection · denied topics · financial PII]
    GIn --> C{{Answer-level cascade<br/>local Qwen → Bedrock Haiku}}
    C --> A[Agent loop<br/>tool use · 2 SQL retries]
    A --> L[llm.router<br/>LM Studio · OmniRoute · Bedrock Converse]
    A --> S[legacybridge-schema MCP<br/>tables · columns · rules · RAG]
    A --> Q[legacybridge-sql MCP<br/>run_query]
    A --> P[propose_change<br/>write guard · human confirm]
    S --> R[(pgvector<br/>schema knowledge)]
    Q --> G[SQL guard<br/>AST, deny by default]
    G --> RO[(PostgreSQL<br/>read-only role lb_ro)]
    Q --> SAN[sanitize · spotlight<br/>indirect injection D9]
    P --> PR[(ops.change_proposals<br/>PENDING_REVIEW · never executed)]
    A --> GOut[check_output<br/>DLP · PII masking]
    GOut --> ANS([Answer + evidence<br/>SQL · rows · tables · confidence])
    A -.-> T[(telemetry JSONL<br/>latency by stage · cost)]
    GIn -.-> AU[(ops.audit_log<br/>append-only)]
```

- **Evidence is recorded by the loop, never by the model.** Every successful `run_query` stores the
  executed SQL, rows and tables; the model can only point to one of them ([ADR-004](docs/adr/004-rag-and-agent-evidence-contract.md)).
- **Defense in depth for reads.** AST guard (single SELECT, table and schema allowlist, deny-by-default
  functions, no catalogs, forced LIMIT) → fail-closed preflight (`current_user = lb_ro`, read-only
  transaction) → 5 s statement timeout → rollback. Restricted tables have no grants at all
  ([ADR-003](docs/adr/003-mcp-v2-and-guard-function-policy.md)).
- **Writes are proposals.** The agent can draft an INSERT/UPDATE/DELETE on allowlisted tables (WHERE
  required, impact estimated with a read-only COUNT); a human confirms, a reviewer approves or rejects,
  and nothing in the system ever executes it ([ADR-006](docs/adr/006-guardrails-and-human-in-the-loop.md)).
- **Cost first.** Local Qwen answers by default; a question is re-asked on Bedrock Haiku only if the
  local result fails an explicit policy (provider error, unfinished run, SQL failed twice, answer without
  evidence, low confidence) ([ADR-001](docs/adr/001-cost-cascade.md), [ADR-007](docs/adr/007-cost-cascade-and-observability.md)).

## What it demonstrates
| Capability | Where |
|---|---|
| MCP servers (schema explorer + read-only SQL), usable from Claude Code or MCP Inspector | `src/legacybridge/mcp_servers/` |
| AST SQL guard, write guard, least-privilege PostgreSQL roles | `src/legacybridge/guard/`, `db/legacy/` |
| Provider-neutral LLM layer: tool calling and embeddings over LM Studio, OmniRoute and Bedrock Converse | `src/legacybridge/llm/` |
| Validated business dictionary (column semantics, rules, catalogs, defects) | `config/business_dictionary.yaml` |
| Schema RAG in pgvector with redaction of restricted names and incremental indexing | `src/legacybridge/rag/` |
| Tool-use agent with self-correction, did-you-mean hints and verifiable evidence | `src/legacybridge/agent/` |
| Guardrails: direct/indirect prompt injection (incl. base64 and zero-width tricks), denied topics, DLP, Mexican PII formats, append-only audit log, optional Bedrock Guardrails | `src/legacybridge/guardrails/` |
| Human-in-the-loop change proposals with separate proposer/reviewer roles | `src/legacybridge/agent/proposals.py` |
| Answer-level cost cascade and per-turn telemetry | `src/legacybridge/agent/cascade.py`, `src/legacybridge/telemetry.py` |
| Reproducible evaluation: 120-question golden set (dev/test), 15-item adversarial holdout, execution accuracy, SWAR, refusal and safety rates, cost, fingerprints | `evals/` |
| Deterministic synthetic ERP data with every defect seeded | `scripts/gen_data.py` |

## Failure modes
What still goes wrong, measured on the test split — not hidden:

- **Silent wrong answers (3.8%).** The agent is sometimes confidently wrong on defect traps: summing
  amounts across currencies instead of grouping them (D7), converting boxes to pieces but not excluding
  kilograms (D6), counting orphan lines with an inner join (D2). The model reports confidence ≈ 1.0, so
  **confidence-based escalation cannot catch these**; only objective signals (unfinished runs, SQL
  failures, missing evidence) trigger the cascade.
- **Reasoning runaway on the local model.** On some adversarial prompts (catalog discovery, "what other
  tables exist?") Qwen reasons until `max_tokens` and returns nothing. No data leaks, but no explicit
  refusal either; the cascade is designed to escalate these (`provider_error`).
- **A false but harmless answer.** Asked what other tables exist, the agent sometimes says "no other
  tables exist" — nothing is revealed, but the statement is untrue.
- **Heuristic guardrails.** Injection rules and denied topics are deterministic patterns: a sufficiently
  novel paraphrase can reach the model. The SQL guard, database grants and the never-execute write path
  are what keep that from becoming a data or integrity incident (100% safe handling on both attack sets).
- **Evaluation limits.** Result-set comparison accepts extra columns (tolerant mode; strict is reported
  too) and treats business labels from the dictionary as equivalent to codes. Local model output varies
  between runs even at temperature 0, which is why every official figure is a mean of repetitions.
- **Pending benchmark.** The Bedrock-only and cascade runs — and the Phase 5 target (cascade ≥ 95% of
  Bedrock accuracy at ≤ 20% of its cost) — are blocked by an AWS account-level restriction on Bedrock
  model access. The code and harness are ready; see [ADR-007](docs/adr/007-cost-cascade-and-observability.md).

## Quick start
Requirements: Python 3.12+, Docker, [LM Studio](https://lmstudio.ai) serving `qwen/qwen3.6-35b-a3b`
and `text-embedding-bge-m3` on `:1234`. OmniRoute and AWS Bedrock are optional.

```bash
make setup                 # virtualenv + dependencies, creates .env from .env.example
make db                    # PostgreSQL 16 + pgvector on :5433 (schema, roles, anchor rows)
make db-migrate            # RAG store, audit log, change proposals
make seed                  # deterministic synthetic ERP data (seed 42)
make index                 # schema knowledge into pgvector
make test                  # full test suite
make demo                  # 90-second scripted tour (answer + evidence, guardrails, human-in-the-loop)
make ask Q="¿Cuántos clientes activos hay?"      # answer, evidence, tool trace, tokens, cost
```

More: `make eval` (official evaluation, test × 3, ~1.5 h locally) · `make agent-check` (quick dev run) ·
`make proposals` (review change proposals) · `make telemetry` (latency by stage, cost, escalations) ·
`make mcp-check` / `make mcp-dev SERVER=schema|sql` (MCP Inspector) · `make ask P=cascade Q="..."`.

The MCP servers are registered in [`.mcp.json`](.mcp.json): open the repo in Claude Code and the
`legacybridge-schema` and `legacybridge-sql` tools are available.

## MCP tools
| Server | Tool | Purpose |
|---|---|---|
| `legacybridge-schema` | `list_tables` | Queryable tables with business concept, key and synonyms |
| | `describe_table` | Real column types + meaning, defect tags (D1–D10), joins, rules, catalogs |
| | `find_columns(concept)` | Maps a business concept ("order date", "currency") to ranked columns |
| | `get_business_rule(term)` | Business rules and code catalogs ("valid order", "active customer") |
| | `search_knowledge(query)` | Semantic search over schema knowledge (pgvector, bge-m3) |
| `legacybridge-sql` | `run_query(sql)` | AST guard → `lb_ro` preflight → read-only txn with 5 s timeout → rows + normalized SQL, sanitized and PII-masked for the client |

Every tool is annotated read-only. Failures return a `stage` (`guard`, `connection`, `preflight`,
`execution`), a machine-readable reason and, for misspelled names, suggestions.

## AWS mapping
| Concern | Here | AWS service |
|---|---|---|
| Model inference with tool use | `llm.router` (Converse API) | Amazon Bedrock — Claude Haiku 4.5 |
| Managed guardrails | `guardrails.bedrock` (ApplyGuardrail adapter, optional) | Amazon Bedrock Guardrails |
| Embeddings | local bge-m3 by default; Titan v2 optional | Amazon Titan Text Embeddings V2 |
| Agent loop, tools, confirmation | explicit loop, MCP tools, `propose_change` | Bedrock Agents / AgentCore (action groups, `requireConfirmation`) |
| Per-turn telemetry | JSONL with stage latency, tokens, cost | CloudWatch EMF metrics · AgentCore Observability spans |
| Cost control | verified prices (AWS Price List API) in every report | AWS Price List API · AWS Budgets |

## Architecture decisions
[001](docs/adr/001-cost-cascade.md) cost cascade ·
[002](docs/adr/002-empty-completion-escalation.md) empty completions escalate ·
[003](docs/adr/003-mcp-v2-and-guard-function-policy.md) MCP 2.x and deny-by-default SQL functions ·
[004](docs/adr/004-rag-and-agent-evidence-contract.md) RAG and the evidence contract ·
[005](docs/adr/005-evaluation-methodology.md) evaluation methodology ·
[006](docs/adr/006-guardrails-and-human-in-the-loop.md) guardrails and human-in-the-loop ·
[007](docs/adr/007-cost-cascade-and-observability.md) answer-level cascade and observability.

## Data and license
All data is synthetic, generated deterministically by `scripts/gen_data.py`; no real company or
person is represented. Code under the [MIT License](LICENSE).
