"""Create the legacy database on RDS (ADR-014): the same schema, roles and seed as ``make db-up``.

Docker builds the local database from ``db/init/`` in its entrypoint; RDS has no entrypoint, so this
function does the same work once, from inside the VPC where the database lives. It reuses the files
instead of copying them: the SQL files run as they are, the role script is read for its SQL (its
password variables are filled from Secrets Manager), and the data comes from ``scripts.seed`` with
seed 42, so a deployed database holds exactly what the golden set was hashed against.
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import psycopg
from psycopg import sql

from legacybridge.aws.secrets import build_dsn, secret_json

INIT_DIR = Path(__file__).resolve().parents[3] / "db" / "init"
SCHEMA_FILES = ("01_facturacion.sql", "02_sicpt.sql")
ROLES_FILE = "03_roles.sh"
OPS_FILE = "04_ops.sql"
ROLE_VARIABLES = {"reader_pw": "reader", "writer_pw": "writer", "proposer_pw": "proposer"}
SEED = 42

# The marker that ADR-010 reads (``lb.synthetic``). On RDS the master user is not a superuser and
# the service may refuse a custom setting on the database, so it is applied apart, with a fallback
# to the role that the check connects as. The line is cut out of 04_ops.sql, which stays the one
# definition.
MARKER_SQL = "ALTER DATABASE legacy SET lb.synthetic = 'true';"
MARKER_FALLBACK_SQL = "ALTER ROLE lb_reader SET lb.synthetic = 'true';"


def roles_sql(passwords: Mapping[str, str], path: Path | None = None) -> str:
    """The SQL of ``03_roles.sh`` with each ``:'x_pw'`` psql variable replaced by a quoted literal.

    The script is the single definition of the roles and their grants; this reads it rather than
    keeping a second copy that could drift.
    """
    text = (path or INIT_DIR / ROLES_FILE).read_text(encoding="utf-8")
    body = text.split("<<'SQL'\n", 1)[1].rsplit("\nSQL", 1)[0]

    def fill(match: re.Match[str]) -> str:
        role = ROLE_VARIABLES[match.group(1)]
        return sql.Literal(passwords[role]).as_string(None)

    return re.sub(r":'(\w+_pw)'", fill, body)


def set_marker(conn: Any) -> str:
    """Mark the database as synthetic. Returns where it was set: ``database``, ``role`` (the
    reader, which is who ADR-010's check connects as) or ``none`` when neither is allowed."""
    for where, statement in (("database", MARKER_SQL), ("role", MARKER_FALLBACK_SQL)):
        conn.execute("SAVEPOINT synthetic_marker")
        try:
            conn.execute(statement)
        except psycopg.errors.InsufficientPrivilege:
            conn.execute("ROLLBACK TO SAVEPOINT synthetic_marker")
            continue
        conn.execute("RELEASE SAVEPOINT synthetic_marker")
        return where
    return "none"


def initialized(conn: Any) -> bool:
    return conn.execute("SELECT to_regclass('public.clientes') IS NOT NULL").fetchone()[0]


def run(
    conn: Any,
    passwords: Mapping[str, str],
    *,
    seed: int = SEED,
    reseed: bool = False,
    seeder: Callable[[int, Any], Any] | None = None,
) -> dict[str, str]:
    """Build the database on ``conn`` (the admin role). A database that already has its tables is
    left alone unless ``reseed`` asks for the data to be loaded again."""
    if seeder is None:
        from scripts.seed import seed_database as seeder
    if initialized(conn):
        if not reseed:
            return {"status": "already_initialized"}
        seeder(seed, conn)
        return {"status": "reseeded"}
    for name in SCHEMA_FILES:
        conn.execute((INIT_DIR / name).read_text(encoding="utf-8"))
    conn.execute(roles_sql(passwords))
    ops = (INIT_DIR / OPS_FILE).read_text(encoding="utf-8")
    if MARKER_SQL not in ops:
        raise RuntimeError(f"{OPS_FILE} no longer contains the synthetic marker statement")
    conn.execute(ops.replace(MARKER_SQL, ""))
    marker = set_marker(conn)
    conn.commit()
    seeder(seed, conn)
    return {"status": "initialized", "synthetic_marker": marker}


def handler(event: dict[str, Any], context: Any = None) -> dict[str, str]:
    """The Lambda entry point: ``{}`` initializes a fresh database, ``{"reseed": true}`` reloads."""
    import boto3

    env = os.environ
    client = boto3.client("secretsmanager")
    admin = secret_json(client, env["LB_ADMIN_SECRET"])
    host, port = env["LB_DB_HOST"], env.get("LB_DB_PORT", "5432")
    name = env.get("LB_DB_NAME", "legacy")
    passwords = {
        role: secret_json(client, env[f"LB_{role.upper()}_SECRET"])["password"]
        for role in ROLE_VARIABLES.values()
    }
    with psycopg.connect(build_dsn(admin, host, port, name), connect_timeout=10) as conn:
        return run(conn, passwords, reseed=bool(event.get("reseed")))
