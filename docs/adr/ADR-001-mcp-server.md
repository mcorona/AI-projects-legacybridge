# ADR-001: Expose the legacy database through our own MCP server

- **Status:** Accepted
- **Date:** 2026-10-08
- **Related:** Phase 2 of `docs/PLAN.md`, ADR-004, ADR-005, `src/legacybridge/mcp_server/`

## Context

The agent (Phase 4), Claude Code and Claude Desktop must all query the same legacy database. The
database is hostile territory for a language model: cryptic names, text-typed amounts and dates,
logical deletes and free-text columns that can carry instructions. Handing a model a connection
string or a generic SQL tool would make every client re-implement (or skip) the safeguards.

## Decision

One MCP server, `legacybridge`, over stdio, built with the official `mcp` SDK (`MCPServer`). It is
the only path from a model to the data, and it exposes six tools:

| Tool | Effect |
|---|---|
| `list_tables`, `describe_table`, `sample_rows` | Read-only exploration, with known defects per column |
| `run_query` | One validated `SELECT`, always capped and time-boxed |
| `explain_business_term` | Answers from the ontology, never from the model's memory |
| `propose_write` | Queues a pending request; executes nothing (ADR-005) |

Tool logic lives in plain functions (`tools.py`) that receive their connections, so it is tested
without the MCP transport. `server.py` only registers them. Every response is structured JSON that
says what happened (`ok`, `stage`, `reason`, `hint`) so a model can correct itself.

## Consequences

- Safeguards are written once and apply to every client, including Claude Code through `.mcp.json`.
- The same code serves the Phase 4 agent as an MCP client, so evals measure what users get.
- A stdio server per client session means one short-lived connection pool per process; that is
  fine locally. The AWS deployment (Phase 7) exposes an HTTP API around the same `tools.py`.

## Alternatives considered

1. **Direct function calling in the agent.** Simpler, but nothing is reusable from Claude Code and
   each client owns its own safeguards.
2. **A generic Postgres MCP server.** No AST guard, no column allowlist, no defect awareness.
