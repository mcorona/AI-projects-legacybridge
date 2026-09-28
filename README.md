# LegacyBridge

**An AI agent that safely answers business questions over legacy enterprise databases through MCP —
with verifiable evidence, reproducible evaluation, and a cost-aware model cascade.**

> Status: ✅ Phase 0 · ✅ Phase 1 (MCP servers) · ✅ Phase 2 (RAG + agent) · 🚧 Phase 3 — evaluation harness.

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
| Golden set (21/120 q, D1–D10 covered; attacks verified against the guard) + execution accuracy / SWAR | 🧪 |
| Prompt-injection defense (direct and indirect) | ⏳ |
| Cost cascade benchmark (local vs cloud) | ⏳ |

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
make agent-check    # agent on the golden set (result-set comparison)
make mcp-dev SERVER=schema   # or SERVER=sql — MCP Inspector UI
claude              # then run /kickoff
```

## License
MIT
