"""Propuestas en PostgreSQL: mínimo privilegio y ciclo de revisión (integración)."""
import os

import pytest

from legacybridge.agent import proposals as pr
from legacybridge.agent import review_cli

ADMIN = os.environ.get("LB_ADMIN_DSN", "postgresql://postgres:postgres@localhost:5433/legacy")
pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def admin():
    psycopg = pytest.importorskip("psycopg")
    try:
        conn = psycopg.connect(ADMIN, connect_timeout=2, autocommit=True)
    except psycopg.OperationalError:
        pytest.skip("Postgres legacy no disponible (make db)")
    if not conn.execute("SELECT to_regclass('ops.change_proposals')").fetchone()[0]:
        pytest.skip("propuestas no migradas (make db-migrate)")
    yield conn
    conn.close()


def _priv(conn, role, privs, col=None):
    if col:
        return conn.execute("SELECT has_column_privilege(%s, 'ops.change_proposals', %s, %s)",
                            (role, col, privs)).fetchone()[0]
    return conn.execute("SELECT has_table_privilege(%s, 'ops.change_proposals', %s)", (role, privs)).fetchone()[0]


def test_least_privilege(admin):
    assert _priv(admin, "lb_proposals", "INSERT", "sql") and not _priv(admin, "lb_proposals", "INSERT", "status")
    assert not _priv(admin, "lb_proposals", "SELECT")
    assert not _priv(admin, "lb_proposals", "UPDATE") and not _priv(admin, "lb_proposals", "DELETE")
    assert _priv(admin, "lb_reviewer", "SELECT") and not _priv(admin, "lb_reviewer", "DELETE")
    assert _priv(admin, "lb_reviewer", "UPDATE", "status")
    assert not _priv(admin, "lb_reviewer", "UPDATE", "sql")          # el revisor no puede reescribir la SQL
    for role in ("lb_proposals", "lb_reviewer"):
        for t in ("public.pedenc", "public.cliemae"):
            assert not admin.execute("SELECT has_table_privilege(%s, %s, 'INSERT,UPDATE,DELETE')",
                                     (role, t)).fetchone()[0], (role, t)


def test_save_and_review_cycle(admin, capsys):
    marker = f"pytest-{os.getpid()}"
    preview = pr.ProposalPreview("DELETE FROM pedenc WHERE pedest = 'X'", "DELETE", "pedenc", "prueba", 3)
    pid = pr.DbProposalStore().save(pr.Proposal(preview, "¿?", marker, marker))
    try:
        assert admin.execute("SELECT status FROM ops.change_proposals WHERE id = %s", (pid,)).fetchone()[0] \
            == "PENDING_REVIEW"
        assert review_cli.main(["reject", str(pid), "--reviewer", marker, "--note", "no procede"]) == 0
        assert review_cli.main(["approve", str(pid), "--reviewer", marker]) == 1          # ya revisada
        status, by = admin.execute("SELECT status, reviewed_by FROM ops.change_proposals WHERE id = %s",
                                   (pid,)).fetchone()
        assert (status, by) == ("REJECTED", marker)
        assert admin.execute("SELECT count(*) FROM ops.audit_log WHERE actor = %s AND event = 'proposal_rejected'",
                             (marker,)).fetchone()[0] == 1
        # nada se ejecutó: los pedidos cancelados siguen ahí
        assert admin.execute("SELECT count(*) FROM pedenc WHERE pedest = 'X'").fetchone()[0] > 0
    finally:
        admin.execute("DELETE FROM ops.change_proposals WHERE id = %s", (pid,))
