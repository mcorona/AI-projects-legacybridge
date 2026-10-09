-- Approval queue for write requests (ADR-005). `propose_write` inserts here and executes nothing.
-- Approval and execution arrive in Phase 4, which is why no role may UPDATE or DELETE rows yet.

CREATE SCHEMA lb_ops;

CREATE TABLE lb_ops.write_requests (
    id             BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    status         TEXT NOT NULL DEFAULT 'pending'
                   CHECK (status IN ('pending', 'approved', 'rejected', 'executed')),
    tbl            TEXT NOT NULL,           -- target table, one of policy.WRITABLE
    update_values  JSONB NOT NULL,          -- {"status": "B"}
    where_values   JSONB NOT NULL,          -- equality filters that identify the rows
    reason         TEXT,
    requested_by   TEXT NOT NULL DEFAULT 'mcp',
    affected_rows  INTEGER NOT NULL,        -- counted with lb_reader when the request was made
    preview        JSONB NOT NULL DEFAULT '[]'  -- up to 5 of the rows that would change
);

REVOKE ALL ON SCHEMA lb_ops FROM PUBLIC;
GRANT USAGE ON SCHEMA lb_ops TO lb_proposer;
GRANT SELECT, INSERT ON lb_ops.write_requests TO lb_proposer;
