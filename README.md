# LegacyBridge

**An AI agent that safely answers business questions over legacy enterprise databases through MCP —
with verifiable evidence, reproducible evaluation, and a cost-aware model cascade.**

> Status: ✅ Phases 0–4 · ◐ Phase 5 (cascade + telemetry built; Bedrock benchmark pending an AWS account block) · 🚧 Phase 6 — publication.

## Why
Critical business data still lives in decades-old ERPs: cryptic table names, no foreign keys,
dates stored as text, magic status codes. Generic text-to-SQL demos break on them.
LegacyBridge encodes 30 years of enterprise-systems experience into schema exploration tools,
a business dictionary, and an evaluation set built around real legacy defects.

## Architecture
```
User ─► Agent ─► LLM router ─┬─► Qwen3.6 (LM Studio, local, $0)
          │                  ├─► OmniRoute (free-tier gateway, synthetic data only)
          │                  └─► Amazon Bedrock (Claude Haiku 4.5, escalation)
          ├─ MCP: legacybridge-schema  (tables, columns, business rules)
          ├─ MCP: legacybridge-sql     (AST guard ─► read-only role ─► PostgreSQL)
          └─ RAG: DDL + data dictionary + defect catalog (pgvector)
```

## What it demonstrates
| Capability | Status |
|---|---|
| AST-based SQL guard (SELECT-only, table/schema allowlist, deny-by-default functions, no catalogs, forced LIMIT) — ADR-003 | ✅ |
| Multi-provider LLM layer with cost/latency tracking and honest cascade escalation (ADR-002) | ✅ |
| MCP servers (schema explorer, read-only SQL) with fail-closed execution | ✅ |
| Validated business dictionary (column semantics, rules, catalogs, defects) | ✅ |
| Legacy defect catalog (10 intentional defects) | ✅ |
| Agent with self-correction and loop-recorded evidence (local Qwen: 10/11 golden answers correct) — ADR-004 | ✅ |
| Schema RAG in pgvector (DDL, dictionary, defects; redacted, least-privilege roles) | ✅ |
| Golden set (120 q, dev/test split, every defect question proven to discriminate) + reproducible harness (execution accuracy, SWAR, refusals, p50/p95, cost) — ADR-005 | ✅ |
| Deterministic synthetic ERP data with every legacy defect seeded | ✅ |
| Layered guardrails: direct/indirect prompt injection (incl. base64 and zero-width tricks), denied topics, output DLP, PII masking (Mexican formats), append-only audit log, optional Bedrock Guardrails — ADR-006 | ✅ |
| Human-in-the-loop write proposals: validated, confirmed, reviewed — never executed | ✅ |
| Answer-level cost cascade (local Qwen → Bedrock Haiku) with escalation policy, per-turn telemetry (latency by stage, cost), verified AWS pricing — ADR-007 | ✅ |
| Cost cascade benchmark vs Bedrock-only | ⏳ pending AWS |

## Results (held-out test split, latest code)
90 questions × 3 runs, local Qwen3.6-35B-A3B on LM Studio, **$0** (≈ $2.37 per run at verified Bedrock
Haiku 4.5 prices). Full reports with reproducibility fingerprint: [`evals/reports/`](evals/reports/).

| Metric | Local Qwen (×3) | OmniRoute free tier (×1) | Phase 3 baseline |
|---|---|---|---|
| Execution accuracy (result sets, not SQL text) | **95.4%** [95.0–96.2] | 88.8% | 77.9% |
| SWAR — silent wrong answers (wrong, confidence ≥ 0.6) | **3.8%** [2.5–5.0] | 11.2% | 5.4% |
| Correct refusals on adversarial prompts / false refusals | 76.7% / 0% | 70.0% / 0% | 80% / 0% |
| Runs completed | 97.0% | 100% | 84.1% |
| Latency p50 / p95 | 19 s / 43 s | 17 s / 28 s | 16 s / 92 s |

By level (local): easy 100% · joins & rules 96.7% · legacy-defect traps 86.7%.

**Security (Phase 4)** — adversarial prompts, 3 runs each:

| Set | Expected behavior | Safe handling | Leaks | Writes executed |
|---|---|---|---|---|
| 15 golden-set attacks | 80.0% | 100% | 0 | 0 |
| 15 held-out attacks (written before the defenses) | 93.3% | 100% | 0 | 0 |

With guardrails on, the test split keeps 0% false refusals (83.8% execution accuracy, single run).

Phase 3 figures are after a documented measurement fix (original run: 74.2% / 9.2% SWAR; ADR-005).
The Bedrock-only and cascade benchmark (Phase 5 target: cascade ≥ 95% of Bedrock accuracy at ≤ 20% of
its cost) is pending an AWS account-level Bedrock block; see ADR-007.

## MCP tools
| Server | Tool | Purpose |
|---|---|---|
| `legacybridge-schema` | `list_tables` | Queryable tables with business concept, key and synonyms |
| | `describe_table` | Real column types + meaning, defect tags (D1–D10), joins, rules, catalogs |
| | `find_columns(concept)` | Maps a business concept ("order date", "currency") to ranked columns |
| | `get_business_rule(term)` | Business rules and code catalogs ("valid order", "active customer") |
| | `search_knowledge(query)` | Semantic search over schema knowledge (pgvector, bge-m3) |
| `legacybridge-sql` | `run_query(sql)` | AST guard → `lb_ro` preflight → read-only txn with 5 s timeout → rows + normalized SQL |

Every tool is annotated read-only. Failures return a `stage` (`guard`, `connection`,
`preflight`, `execution`) and a machine-readable reason so the agent can self-correct.

## Quick start
```bash
make setup && make db && make test
make smoke          # needs LM Studio server on :1234 and/or OmniRoute on :20128
make mcp-check      # lists both MCP servers' tools (MCP Inspector CLI)
make db-migrate && make index   # pgvector store + schema knowledge index
make ask Q="How many active customers are there?"   # answer + evidence + trace + cost
make seed           # deterministic synthetic data (seed 42) with every defect seeded
make eval           # official evaluation: test split × 3 (≈ 1.5 h local); ARGS="--split dev --scratch" for quick runs
make proposals      # human review of change proposals (the agent never executes writes)
make ask P=cascade Q="..."   # answer-level cascade: local Qwen, Bedrock only if needed
make telemetry      # per-turn latency by stage, tokens, cost, escalations
make mcp-dev SERVER=schema   # or SERVER=sql — MCP Inspector UI
claude              # then run /kickoff
```

## License
MIT
