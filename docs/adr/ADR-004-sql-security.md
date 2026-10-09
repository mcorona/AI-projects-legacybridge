# ADR-004: SQL security in three independent layers

- **Status:** Accepted
- **Date:** 2026-10-08
- **Related:** ADR-001, ADR-005, `src/legacybridge/mcp_server/sql_guard.py`, `db/init/03_roles.sh`,
  `evals/attacks.yaml`

## Context

`run_query` accepts SQL written by a model, and the model may have been steered by text stored in
the data. Relying on the prompt ("only write SELECT") is not a control. We need protection that
still holds when one layer has a bug.

## Decision

Three layers, each sufficient against the writes and destructive reads the other two would miss:

1. **SQL guard (AST, `sqlglot`).** Parse once; reject anything that is not a single `SELECT`/`WITH`.
   Reject forbidden nodes (DML, DDL, `INTO`, locking), catalogs and non-`public` schemas, table
   functions, and functions that are not on the allowlist (unknown functions are denied by default,
   so a new dangerous function is blocked without anyone listing it). Resolve every column against
   the allowed schema (`qualify`), so tables **and columns** outside it are refused. Reject
   cartesian products (`CROSS JOIN`, comma joins, `ON TRUE`/`ON 1=1`) and allocation bombs
   (`repeat`, huge `lpad`). Force `LIMIT` to at most 500. **Execute the SQL regenerated from the
   AST, never the original text.**
2. **Database role `lb_reader`.** `SELECT` only on the allowed tables, `default_transaction_read_only`
   on, `statement_timeout` 5 s. If the guard fails, the database still refuses writes.
3. **Session preflight.** Before running anything the executor checks that the connected user is
   `lb_reader` and the transaction is read-only, sets a local `statement_timeout`, and always ends
   with `ROLLBACK`.

The allowlist is **what `lb_reader` can read**: the server introspects the catalog at start-up, so
the grants are the single source of truth and cannot drift from the guard. Tests parse the DDL to
build the same schema without a database and check that both agree.

### Prompt injection inside the data

A guard cannot stop a model from obeying text it reads. The decision is to make that harmless and
visible instead of silent: results are returned verbatim (the correct answer to "what does this
description say?" is the text itself), flagged with a `data_notice`, and cells matching instruction
patterns add a `warning` naming the column. What protects the system is structural: no tool can
change data because of text it read (`run_query` is read-only; `propose_write` only queues a
request that a human approves, ADR-005).

## Consequences

- Every attack in `evals/attacks.yaml` must be blocked (by a named layer) or neutralized; the suite
  also sends the write attacks straight to the database to prove layer 2 on its own.
- The guard may reject valid but exotic PostgreSQL. The 55 reference queries of the golden set are a
  regression corpus, and a rejected query returns a `reason` the model can act on.
- Real LLM behaviour against injected text is measured in Phases 4 and 6, not here.

## Alternatives considered

1. **Trust the prompt.** Not a control.
2. **Read-only role only.** Stops writes but not catalog reads, expensive queries or leaks.
3. **Redact injected text from results.** Hides the real data and breaks questions about it.
