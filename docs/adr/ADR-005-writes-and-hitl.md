# ADR-005: Writes are proposals; a human approves them

- **Status:** Accepted
- **Date:** 2026-10-08
- **Related:** ADR-001, ADR-004, Phase 2 and Phase 4 of `docs/PLAN.md`,
  `db/init/04_ops.sql`, `src/legacybridge/policy.py`

## Context

Users will ask the agent to change data ("deactivate customer X"). A model must never be able to
change business data on its own, but refusing every write makes the agent less useful.

## Decision

- Writing is a **separate tool**, `propose_write`, and the agent has no other way to write.
- `propose_write` **executes nothing.** It validates the change against `policy.WRITABLE` (table,
  columns, allowed values), requires an equality `where` that identifies rows, counts the affected
  rows with `lb_reader` (refusing more than 100) and stores a preview.
- The request is inserted into `lb_ops.write_requests` by a dedicated role, `lb_proposer`, that can
  only `INSERT` and `SELECT` on that one table. This is the single write the server performs, and it
  is the approval queue itself, not business data.
- Removal is a logical delete (`status = 'B'`, `activo = 'N'`). No `DELETE`, no DDL, and no write
  access to invoices or inventory movements, at policy level and in the database grants.
- **Approval and execution (Phase 4)** live outside the agent, in `ApprovalService`, behind
  `POST /approvals/{id}` (bearer token, fail-closed if none is configured) and the
  `legacybridge approvals` CLI. Neither is a tool the agent or the MCP server can call.
  Approving is **one transaction** with `lb_writer`: lock the request (`FOR UPDATE`), require
  `pending`, re-validate it against `policy` (the stored row is not trusted), recount the affected
  rows and require them to match what was proposed (otherwise it is marked `failed`, because the
  data changed since the proposal), run a parameterised `UPDATE`, check `rowcount`, and mark it
  `executed`. The reviewer must not be the proposer.
- **Audit is enforced by the database.** `lb_ops.audit_log` is filled by a `SECURITY DEFINER`
  trigger on every insert and status change of `write_requests`. No application role can insert
  into it, update it or delete from it, so the trail cannot be skipped or edited by code that has a
  bug or by a model that has been steered.
- `policy.WRITABLE` and the real privileges of `lb_writer` must match; a test compares them.

## Consequences

- A compromised or misled model can at worst create pending requests, each reviewable by a person.
- Two writer-like roles exist (`lb_proposer` queues, `lb_writer` applies) so no single credential can
  both invent and execute a change.
- Bulk changes are capped at 100 rows per request.

## Alternatives considered

1. **Forbid writes.** Safe, but pushes users back to manual SQL.
2. **Let the model write with `lb_writer` after a chat confirmation.** The confirmation can be
   forged by injected text; the approval must happen outside the model.
