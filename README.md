# LegacyBridge

**An AI agent that safely answers business questions over legacy enterprise databases through MCP —
with verifiable evidence, reproducible evaluation, and a cost-aware model cascade.**

> Status: ✅ Phase 0 complete (environment + provider smoke test) · 🚧 Phase 1 — MCP servers.

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
| AST-based SQL guard (SELECT-only, allowlist, no catalogs, forced LIMIT) | ✅ |
| Multi-provider LLM layer with cost/latency tracking and honest cascade escalation (ADR-002) | ✅ |
| MCP servers (schema explorer, read-only SQL) | 🧪 |
| Legacy defect catalog (10 intentional defects) | ✅ |
| Agent with self-correction and evidence | ⏳ |
| Golden set (120 q) + execution accuracy / SWAR | ⏳ |
| Prompt-injection defense (direct and indirect) | ⏳ |
| Cost cascade benchmark (local vs cloud) | ⏳ |

## Quick start
```bash
make setup && make db && make test
make smoke          # needs LM Studio server on :1234 and/or OmniRoute on :20128
claude              # then run /kickoff
```

## License
MIT
