"""The RDS database initializer (ADR-014) reuses the files that build the local database.

The full run was checked once against a scratch Postgres (same data fingerprint as the local one);
these tests keep the parts that can drift: the role SQL taken from ``03_roles.sh`` and the order.
"""

import psycopg
import pytest

from legacybridge.aws import db_init

PASSWORDS = {"reader": "r-pw", "writer": "w'pw", "proposer": "p-pw"}


def test_the_role_sql_is_the_one_in_the_script_with_every_password_filled():
    text = db_init.roles_sql(PASSWORDS)
    assert ":'" not in text, "no psql variable is left unfilled"
    assert "CREATE ROLE lb_reader LOGIN PASSWORD 'r-pw';" in text
    assert "CREATE ROLE lb_proposer LOGIN PASSWORD 'p-pw';" in text
    assert "GRANT UPDATE (status, telefono, email) ON clientes TO lb_writer;" in text
    assert "ALTER ROLE lb_reader SET default_transaction_read_only = on;" in text


def test_a_quote_in_a_password_is_escaped_not_executed():
    assert "CREATE ROLE lb_writer LOGIN PASSWORD 'w''pw';" in db_init.roles_sql(PASSWORDS)


def test_a_missing_password_is_an_error_not_an_empty_one():
    with pytest.raises(KeyError):
        db_init.roles_sql({"reader": "x"})


class FakeConn:
    def __init__(self, has_tables, refuse=()):
        self.has_tables, self.statements, self.commits = has_tables, [], 0
        self.refuse = refuse  # statements the server answers with "permission denied"

    def execute(self, text):
        self.statements.append(text)
        if text in self.refuse:
            raise psycopg.errors.InsufficientPrivilege("permission denied to set parameter")

        class Row:
            def fetchone(inner):
                return (self.has_tables,)

        return Row()

    def commit(self):
        self.commits += 1


def test_a_fresh_database_gets_schema_roles_ops_and_then_the_seed_in_that_order():
    conn, seeded = FakeConn(has_tables=False), []
    out = db_init.run(conn, PASSWORDS, seeder=lambda seed, c: seeded.append(seed))
    kinds = []
    for s in conn.statements[1:5]:
        kinds.append(
            "facturacion" if "CREATE TABLE clientes" in s
            else "roles" if "CREATE ROLE lb_reader" in s
            else "ops" if "lb_ops" in s and "write_requests" in s
            else "sicpt" if "invart" in s else "other"
        )  # fmt: skip
    assert out == {"status": "initialized", "synthetic_marker": "database"}
    assert seeded == [42] and conn.commits == 1
    assert kinds == ["facturacion", "sicpt", "roles", "ops"], "ops grants need the roles to exist"


def test_a_database_that_already_has_its_tables_is_left_alone():
    conn, seeded = FakeConn(has_tables=True), []
    assert db_init.run(conn, PASSWORDS, seeder=lambda *a: seeded.append(a)) == {
        "status": "already_initialized"
    }
    assert len(conn.statements) == 1 and seeded == []


def test_reseeding_reloads_the_data_without_touching_the_schema():
    conn, seeded = FakeConn(has_tables=True), []
    out = db_init.run(conn, PASSWORDS, reseed=True, seeder=lambda seed, c: seeded.append(seed))
    assert out == {"status": "reseeded"} and seeded == [42] and len(conn.statements) == 1


# --------------------------------------------------------------------------- the synthetic marker on RDS
def test_the_marker_statement_is_cut_out_of_the_ops_script_and_set_on_its_own():
    conn = FakeConn(has_tables=False)
    db_init.run(conn, PASSWORDS, seeder=lambda *a: None)
    ops = next(s for s in conn.statements if "write_requests" in s and "CREATE" in s)
    assert db_init.MARKER_SQL not in ops, "it would abort the whole script when RDS refuses it"
    assert conn.statements.count(db_init.MARKER_SQL) == 1


def test_when_rds_refuses_the_database_setting_the_reader_role_carries_the_marker():
    conn = FakeConn(has_tables=False, refuse=(db_init.MARKER_SQL,))
    out = db_init.run(conn, PASSWORDS, seeder=lambda *a: None)
    assert out["synthetic_marker"] == "role" and db_init.MARKER_FALLBACK_SQL in conn.statements
    assert "ROLLBACK TO SAVEPOINT synthetic_marker" in conn.statements, (
        "the failure must not poison the transaction"
    )
    assert conn.commits == 1


def test_when_neither_is_allowed_the_result_says_so_instead_of_pretending():
    conn = FakeConn(has_tables=False, refuse=(db_init.MARKER_SQL, db_init.MARKER_FALLBACK_SQL))
    out = db_init.run(conn, PASSWORDS, seeder=lambda *a: None)
    assert out == {"status": "initialized", "synthetic_marker": "none"}


def test_a_changed_ops_script_that_lost_the_marker_is_an_error_not_a_silent_gap(
    monkeypatch, tmp_path
):
    (tmp_path / "04_ops.sql").write_text("SELECT 1;")
    for name in (*db_init.SCHEMA_FILES, db_init.ROLES_FILE):
        (tmp_path / name).write_text((db_init.INIT_DIR / name).read_text())
    monkeypatch.setattr(db_init, "INIT_DIR", tmp_path)
    with pytest.raises(RuntimeError, match="no longer contains the synthetic marker"):
        db_init.run(FakeConn(has_tables=False), PASSWORDS, seeder=lambda *a: None)
