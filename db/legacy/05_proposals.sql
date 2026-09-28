-- Propuestas de cambio (Fase 4, human-in-the-loop). IDEMPOTENTE: `make db-migrate`.
-- El sistema NUNCA ejecuta estas sentencias: una persona autorizada las revisa y, si las aprueba,
-- un DBA las aplica por fuera del agente.
--   lb_proposals  el agente: solo INSERT (no puede leer, cambiar ni aprobar propuestas)
--   lb_reviewer   el revisor: lee y solo puede actualizar las columnas de revisión

CREATE TABLE IF NOT EXISTS ops.change_proposals (
    id                BIGSERIAL PRIMARY KEY,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    requested_by      TEXT        NOT NULL,
    confirmed_by      TEXT        NOT NULL,
    question          TEXT        NOT NULL,
    rationale         TEXT        NOT NULL DEFAULT '',
    sql               TEXT        NOT NULL,
    kind              TEXT        NOT NULL CHECK (kind IN ('INSERT', 'UPDATE', 'DELETE')),
    target_table      TEXT        NOT NULL,
    affected_rows_est INTEGER,
    status            TEXT        NOT NULL DEFAULT 'PENDING_REVIEW'
                      CHECK (status IN ('PENDING_REVIEW', 'APPROVED', 'REJECTED')),
    reviewed_by       TEXT,
    reviewed_at       TIMESTAMPTZ,
    review_note       TEXT,
    CHECK ((status = 'PENDING_REVIEW') = (reviewed_by IS NULL))
);

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'lb_proposals') THEN
        CREATE ROLE lb_proposals LOGIN PASSWORD 'lb_proposals';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'lb_reviewer') THEN
        CREATE ROLE lb_reviewer LOGIN PASSWORD 'lb_reviewer';
    END IF;
END $$;

GRANT CONNECT ON DATABASE legacy TO lb_proposals, lb_reviewer;
GRANT USAGE ON SCHEMA ops TO lb_proposals, lb_reviewer;
-- INSERT por columnas y sin SELECT: el id se toma con nextval() (INSERT … RETURNING exigiría leer)
GRANT INSERT (id, requested_by, confirmed_by, question, rationale, sql, kind, target_table, affected_rows_est)
    ON ops.change_proposals TO lb_proposals;
GRANT USAGE ON SEQUENCE ops.change_proposals_id_seq TO lb_proposals;
GRANT SELECT ON ops.change_proposals TO lb_reviewer;
GRANT UPDATE (status, reviewed_by, reviewed_at, review_note) ON ops.change_proposals TO lb_reviewer;
-- el revisor también deja rastro en la bitácora append-only
GRANT INSERT ON ops.audit_log TO lb_reviewer;
GRANT USAGE ON SEQUENCE ops.audit_log_id_seq TO lb_reviewer;
