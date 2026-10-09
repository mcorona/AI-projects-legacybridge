-- Approval queue for write requests, its audit trail, and the synthetic-data marker
-- (ADR-005, ADR-010). `propose_write` inserts here and executes nothing; a person decides, and
-- ApprovalService applies an approved change in one transaction.

CREATE SCHEMA lb_ops;

CREATE TABLE lb_ops.write_requests (
    id             BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    status         TEXT NOT NULL DEFAULT 'pending'
                   CHECK (status IN ('pending', 'rejected', 'executed', 'failed')),
    tbl            TEXT NOT NULL,           -- target table, one of policy.WRITABLE
    update_values  JSONB NOT NULL,          -- {"status": "B"}
    where_values   JSONB NOT NULL,          -- equality filters that identify the rows
    reason         TEXT,
    requested_by   TEXT NOT NULL DEFAULT 'mcp',
    affected_rows  INTEGER NOT NULL,        -- counted with lb_reader when the request was made
    preview        JSONB NOT NULL DEFAULT '[]',  -- up to 5 of the rows that would change
    decided_by     TEXT,                    -- the reviewer
    decided_at     TIMESTAMPTZ,
    decision_note  TEXT,
    executed_at    TIMESTAMPTZ,
    result         JSONB                    -- {"rows_updated": n} or why it failed
);

-- Append-only trail. Nothing writes to it directly: the trigger below does, running as the
-- table owner, so no application role can skip an entry, edit one or delete one.
CREATE TABLE lb_ops.audit_log (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    at          TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    db_user     TEXT NOT NULL DEFAULT session_user,   -- the role that opened the connection
    event       TEXT NOT NULL,                        -- proposed | rejected | executed | failed
    request_id  BIGINT NOT NULL,
    actor       TEXT,                                 -- proposer, then reviewer
    details     JSONB NOT NULL
);

CREATE FUNCTION lb_ops.audit_write_request() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, lb_ops AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        INSERT INTO lb_ops.audit_log (event, request_id, actor, details)
        VALUES ('proposed', NEW.id, NEW.requested_by, to_jsonb(NEW));
    ELSIF NEW.status IS DISTINCT FROM OLD.status THEN
        INSERT INTO lb_ops.audit_log (event, request_id, actor, details)
        VALUES (NEW.status, NEW.id, NEW.decided_by,
                jsonb_build_object('from', OLD.status, 'request', to_jsonb(NEW)));
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER write_requests_audit
AFTER INSERT OR UPDATE ON lb_ops.write_requests
FOR EACH ROW EXECUTE FUNCTION lb_ops.audit_write_request();

-- A request is decided once. This also stops a second execution of the same change.
CREATE FUNCTION lb_ops.guard_write_request() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.status <> 'pending' THEN
        RAISE EXCEPTION 'write request % is already %', OLD.id, OLD.status USING ERRCODE = '55000';
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER write_requests_decided_once
BEFORE UPDATE ON lb_ops.write_requests
FOR EACH ROW EXECUTE FUNCTION lb_ops.guard_write_request();

CREATE FUNCTION lb_ops.audit_append_only() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'lb_ops.audit_log is append-only' USING ERRCODE = '55000';
END $$;

CREATE TRIGGER audit_log_no_change
BEFORE UPDATE OR DELETE ON lb_ops.audit_log
FOR EACH ROW EXECUTE FUNCTION lb_ops.audit_append_only();

CREATE TRIGGER audit_log_no_truncate
BEFORE TRUNCATE ON lb_ops.audit_log
FOR EACH STATEMENT EXECUTE FUNCTION lb_ops.audit_append_only();

REVOKE ALL ON SCHEMA lb_ops FROM PUBLIC;
GRANT USAGE ON SCHEMA lb_ops TO lb_proposer, lb_writer;

-- lb_proposer queues requests and can read them back; it can never decide one.
GRANT SELECT, INSERT ON lb_ops.write_requests TO lb_proposer;

-- lb_writer applies approved requests: it reads them and may touch only the decision columns.
GRANT SELECT ON lb_ops.write_requests TO lb_writer;
GRANT UPDATE (status, decided_by, decided_at, decision_note, executed_at, result)
    ON lb_ops.write_requests TO lb_writer;
GRANT SELECT ON lb_ops.audit_log TO lb_writer;

-- ADR-010: profiles that send data to third parties may only run against synthetic data.
ALTER DATABASE legacy SET lb.synthetic = 'true';
