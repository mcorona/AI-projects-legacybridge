#!/bin/bash
# Creates the two application roles. Passwords come from the container environment
# (docker-compose.yml, fed by .env), so no credential lives in this repository.
set -euo pipefail

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  -v reader_pw="$LB_READER_PASSWORD" -v writer_pw="$LB_WRITER_PASSWORD" <<'SQL'
CREATE ROLE lb_reader LOGIN PASSWORD :'reader_pw';
CREATE ROLE lb_writer LOGIN PASSWORD :'writer_pw';

REVOKE ALL ON DATABASE legacy FROM PUBLIC;
GRANT CONNECT ON DATABASE legacy TO lb_reader, lb_writer;
GRANT USAGE ON SCHEMA public TO lb_reader, lb_writer;

-- lb_reader: SELECT only. Defense in depth: even if the SQL guard fails, the database refuses writes.
GRANT SELECT ON clientes, productos, faccab, facdet TO lb_reader;
ALTER ROLE lb_reader SET statement_timeout = '5s';
ALTER ROLE lb_reader SET default_transaction_read_only = on;

-- lb_writer: used only by the human-approved write flow (Phase 4). It can read, and update a short
-- list of columns. No INSERT, no DELETE, no DDL, and no write access to invoices (faccab, facdet).
-- Removing a record is a logical delete (STATUS = 'B'), never a DELETE.
GRANT SELECT ON clientes, productos, faccab, facdet TO lb_writer;
GRANT UPDATE (status, telefono, email) ON clientes TO lb_writer;
GRANT UPDATE (status) ON productos TO lb_writer;
ALTER ROLE lb_writer SET statement_timeout = '5s';
SQL
