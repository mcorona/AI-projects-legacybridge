-- Operación (Fase 4): bitácora de auditoría append-only.
-- IDEMPOTENTE: se aplica con `make db-migrate`.
--   lb_audit  solo INSERT en ops.audit_log (no puede leer, modificar ni borrar la bitácora).

CREATE SCHEMA IF NOT EXISTS ops;

CREATE TABLE IF NOT EXISTS ops.audit_log (
    id       BIGSERIAL PRIMARY KEY,
    at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    actor    TEXT        NOT NULL,
    event    TEXT        NOT NULL,
    details  JSONB       NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS audit_log_event_at ON ops.audit_log (event, at);

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'lb_audit') THEN
        CREATE ROLE lb_audit LOGIN PASSWORD 'lb_audit';
    END IF;
END $$;

GRANT CONNECT ON DATABASE legacy TO lb_audit;
GRANT USAGE ON SCHEMA ops TO lb_audit;
GRANT INSERT ON ops.audit_log TO lb_audit;
GRANT USAGE ON SEQUENCE ops.audit_log_id_seq TO lb_audit;
