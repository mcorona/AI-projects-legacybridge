"""Esquema RAG: extensión, tabla y separación de privilegios (consultas de solo lectura al catálogo)."""
import os

import pytest

from legacybridge.dictionary import SENSITIVE_TABLES, load

ADMIN_DSN = os.environ.get("LB_ADMIN_DSN", "postgresql://postgres:postgres@localhost:5433/legacy")
pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def admin():
    psycopg = pytest.importorskip("psycopg")
    try:
        conn = psycopg.connect(ADMIN_DSN, connect_timeout=2, autocommit=True)
    except psycopg.OperationalError:
        pytest.skip("Postgres legacy no disponible (make db)")
    if not conn.execute("SELECT to_regclass('rag.chunks')").fetchone()[0]:
        pytest.skip("índice RAG no migrado (make db-migrate)")
    yield conn
    conn.close()


def _priv(conn, role, table, privs):
    return conn.execute("SELECT has_table_privilege(%s, %s, %s)", (role, table, privs)).fetchone()[0]


def test_vector_column_has_1024_dims(admin):
    typ = admin.execute("SELECT format_type(atttypid, atttypmod) FROM pg_attribute "
                        "WHERE attrelid = 'rag.chunks'::regclass AND attname = 'embedding'").fetchone()[0]
    assert typ == "vector(1024)"


def test_hnsw_cosine_index(admin):
    idx = admin.execute("SELECT indexdef FROM pg_indexes WHERE schemaname='rag' "
                        "AND indexname='chunks_embedding_hnsw'").fetchone()[0]
    assert "hnsw" in idx and "vector_cosine_ops" in idx


def test_lb_ro_reads_index_but_cannot_write(admin):
    assert _priv(admin, "lb_ro", "rag.chunks", "SELECT")
    assert not _priv(admin, "lb_ro", "rag.chunks", "INSERT,UPDATE,DELETE,TRUNCATE")


def test_writer_role_is_confined_to_index(admin):
    assert _priv(admin, "lb_rag_rw", "rag.chunks", "SELECT,INSERT,DELETE")
    super_, = admin.execute("SELECT rolsuper FROM pg_roles WHERE rolname='lb_rag_rw'").fetchone()
    assert super_ is False
    for t in sorted(load().allowed_tables | SENSITIVE_TABLES):
        assert not _priv(admin, "lb_rag_rw", f"public.{t}", "SELECT,INSERT,UPDATE,DELETE"), t
