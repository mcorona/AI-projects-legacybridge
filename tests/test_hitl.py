"""Approval and execution of write requests (ADR-005), against the real database."""

import psycopg
import pytest
from psycopg import errors

from legacybridge.agent.hitl import ApprovalError, ApprovalService
from legacybridge.db import connect

pytestmark = pytest.mark.integration

CLIENT = "0000000100"  # status A, Herrajes Central S de RL (AGS)


@pytest.fixture
def clean(admin):
    """Restores the customers table and removes the requests a test created."""
    snapshot = admin.execute("SELECT clave, status, telefono, email FROM clientes").fetchall()
    created: list[int] = []
    yield created
    admin.rollback()
    with admin.cursor() as cursor:
        cursor.executemany(
            "UPDATE clientes SET status = %s, telefono = %s, email = %s WHERE clave = %s",
            [(s, t, e, c) for c, s, t, e in snapshot],
        )
    if created:
        admin.execute("DELETE FROM lb_ops.write_requests WHERE id = ANY(%s)", (created,))
    admin.commit()


@pytest.fixture
def service():
    return ApprovalService()


def propose(tools, created, update, where, table="clientes"):
    out = tools.propose_write(table, update, where, "test")
    assert out["ok"], out
    created.append(out["request_id"])
    return out["request_id"]


def client_state(conn, clave=CLIENT):
    return conn.execute(
        "SELECT status, telefono FROM clientes WHERE clave = %s", (clave,)
    ).fetchone()


def table_fingerprint(conn):
    return conn.execute(
        "SELECT md5(string_agg(c::text, '|' ORDER BY c::text)) FROM clientes c"
    ).fetchone()[0]


def events(service, request_id):
    return [a["event"] for a in service.get(request_id)["audit"]]


def test_approving_applies_exactly_the_stored_change_and_closes_the_request(
    tools, admin, service, clean
):
    request_id = propose(tools, clean, {"status": "I"}, {"clave": CLIENT})
    before = client_state(admin)
    assert before[0] == "A"

    done = service.decide(request_id, True, "ana", "ok")

    assert client_state(admin) == ("I", before[1])  # only the status moved
    assert (done["status"], done["decided_by"], done["decision_note"]) == ("executed", "ana", "ok")
    assert done["result"] == {"rows_updated": 1} and done["executed_at"]
    assert events(service, request_id) == ["proposed", "executed"]


def test_approving_a_multi_column_change_touches_only_those_columns(tools, admin, service, clean):
    request_id = propose(tools, clean, {"status": "I", "telefono": "3300000001"}, {"clave": CLIENT})
    before = admin.execute(
        "SELECT nombre, rfc, email FROM clientes WHERE clave = %s", (CLIENT,)
    ).fetchone()
    service.decide(request_id, True, "ana")
    assert client_state(admin) == ("I", "3300000001")
    assert (
        admin.execute(
            "SELECT nombre, rfc, email FROM clientes WHERE clave = %s", (CLIENT,)
        ).fetchone()
        == before
    )


def test_a_multi_row_request_changes_exactly_the_rows_it_counted(tools, admin, service, clean):
    request_id = propose(tools, clean, {"status": "B"}, {"estado": "GTO", "status": "I"})
    done = service.decide(request_id, True, "ana")
    assert done["result"] == {"rows_updated": 2} and done["affected_rows"] == 2
    assert (
        admin.execute(
            "SELECT count(*) FROM clientes WHERE estado = 'GTO' AND status = 'I'"
        ).fetchone()[0]
        == 0
    )


def test_rejecting_changes_nothing_and_is_audited(tools, admin, service, clean):
    request_id = propose(tools, clean, {"status": "B"}, {"clave": CLIENT})
    before = table_fingerprint(admin)
    done = service.decide(request_id, False, "ana", "no")
    assert table_fingerprint(admin) == before
    assert (done["status"], done["executed_at"], done["result"]) == ("rejected", None, None)
    assert events(service, request_id) == ["proposed", "rejected"]


def test_a_request_is_decided_once(tools, service, clean):
    request_id = propose(tools, clean, {"status": "I"}, {"clave": CLIENT})
    service.decide(request_id, True, "ana")
    for approve in (True, False):
        with pytest.raises(ApprovalError) as raised:
            service.decide(request_id, approve, "luis")
        assert raised.value.code == "already_decided" and raised.value.http_status == 409


def test_the_proposer_cannot_approve_its_own_request(tools, admin, service, clean):
    request_id = propose(tools, clean, {"status": "B"}, {"clave": CLIENT})
    before = table_fingerprint(admin)
    with pytest.raises(ApprovalError) as raised:
        service.decide(request_id, True, "MCP")
    assert raised.value.code == "self_approval" and raised.value.http_status == 403
    assert service.get(request_id)["status"] == "pending" and table_fingerprint(admin) == before


def test_data_that_changed_since_the_proposal_makes_it_stale_and_nothing_is_applied(
    tools, admin, service, clean
):
    request_id = propose(tools, clean, {"status": "B"}, {"estado": "GTO", "status": "I"})
    admin.execute(
        "UPDATE clientes SET status = 'A' WHERE estado = 'GTO' AND status = 'I' AND clave = (SELECT min(clave) FROM clientes WHERE estado = 'GTO' AND status = 'I')"
    )
    admin.commit()
    before = table_fingerprint(admin)
    with pytest.raises(ApprovalError) as raised:
        service.decide(request_id, True, "ana")
    assert raised.value.code == "stale"
    done = service.get(request_id)
    assert done["status"] == "failed" and done["result"]["reason"] == "stale"
    assert done["result"] == {"reason": "stale", "proposed_rows": 2, "rows_now": 1}
    assert table_fingerprint(admin) == before, "a stale request must not change anything"
    assert events(service, request_id) == ["proposed", "failed"]


@pytest.mark.parametrize(
    "table,update,where",
    [
        ("faccab", {"total": "0"}, {"numfac": "0000000001"}),
        ("clientes", {"nombre": "Hacked"}, {"clave": CLIENT}),
        ("clientes", {"status": "X"}, {"clave": CLIENT}),
        ("clientes", {"status": "B"}, {}),
        ("clientes", {"status": "B"}, {"password": "x"}),
    ],
)
def test_a_tampered_request_is_revalidated_and_refused(admin, service, clean, table, update, where):
    """The stored row is not trusted: someone with database access could have edited it."""
    import json

    request_id = admin.execute(
        "INSERT INTO lb_ops.write_requests (tbl, update_values, where_values, affected_rows) "
        "VALUES (%s, %s, %s, 1) RETURNING id",
        (table, json.dumps(update), json.dumps(where)),
    ).fetchone()[0]
    admin.commit()
    clean.append(request_id)
    before = table_fingerprint(admin)
    with pytest.raises(ApprovalError) as raised:
        service.decide(request_id, True, "ana")
    assert raised.value.code == "policy"
    assert service.get(request_id)["status"] == "failed" and table_fingerprint(admin) == before


def test_if_closing_the_request_fails_the_business_update_is_rolled_back(
    tools, admin, service, clean, monkeypatch
):
    """Execution and bookkeeping are one transaction."""
    request_id = propose(tools, clean, {"status": "I"}, {"clave": CLIENT})

    def boom(*args, **kwargs):
        raise psycopg.errors.OperationalError("simulated failure while closing the request")

    monkeypatch.setattr(ApprovalService, "_close", staticmethod(boom))
    with pytest.raises(psycopg.errors.OperationalError):
        service.decide(request_id, True, "ana")
    assert client_state(admin)[0] == "A", "the UPDATE must not survive a failed close"
    assert service.get(request_id)["status"] == "pending"


def test_unknown_requests_and_bad_input(service):
    with pytest.raises(ApprovalError) as raised:
        service.decide(999_999_999, True, "ana")
    assert raised.value.code == "not_found" and raised.value.http_status == 404
    with pytest.raises(ApprovalError) as raised:
        service.get(999_999_999)
    assert raised.value.code == "not_found"
    for reviewer in ("", "   ", "x" * 81):
        with pytest.raises(ApprovalError) as raised:
            service.decide(1, True, reviewer)
        assert raised.value.code == "invalid"
    with pytest.raises(ApprovalError, match="status must be"):
        service.list(status="bogus")


def test_list_and_get_expose_what_a_reviewer_needs(tools, service, clean):
    request_id = propose(tools, clean, {"status": "I"}, {"clave": CLIENT})
    pending = [r for r in service.list(status="pending") if r["id"] == request_id]
    assert len(pending) == 1
    row = pending[0]
    assert (row["tbl"], row["update_values"], row["where_values"]) == (
        "clientes", {"status": "I"}, {"clave": CLIENT},
    )  # fmt: skip
    detail = service.get(request_id)
    assert detail["preview"][0]["clave"] == CLIENT and detail["audit"][0]["event"] == "proposed"
    assert detail["audit"][0]["db_user"] == "lb_proposer"
    assert request_id not in [r["id"] for r in service.list(status="executed")]


# --------------------------------------------------------------------------- the audit trail
def test_nobody_but_the_trigger_can_write_the_audit_log(tools, admin, service, clean):
    request_id = propose(tools, clean, {"status": "I"}, {"clave": CLIENT})
    for role in ("writer", "proposer", "reader"):
        with connect(role) as conn:
            for statement in (
                "INSERT INTO lb_ops.audit_log (event, request_id, details) VALUES ('x', 1, '{}')",
                "UPDATE lb_ops.audit_log SET event = 'x'",
                "DELETE FROM lb_ops.audit_log",
            ):
                with pytest.raises(errors.InsufficientPrivilege):
                    conn.execute(statement)
                conn.rollback()
    # Even the owner cannot rewrite history.
    for statement in (
        "UPDATE lb_ops.audit_log SET event = 'x'",
        "DELETE FROM lb_ops.audit_log",
        "TRUNCATE lb_ops.audit_log",
    ):
        with pytest.raises(psycopg.Error) as raised:
            admin.execute(statement)
        assert raised.value.sqlstate == "55000"
        admin.rollback()
    assert events(service, request_id) == ["proposed"]


def test_the_database_itself_refuses_to_decide_a_request_twice(tools, service, clean):
    request_id = propose(tools, clean, {"status": "I"}, {"clave": CLIENT})
    service.decide(request_id, False, "ana")
    with connect("writer") as conn, pytest.raises(psycopg.Error) as raised:
        conn.execute(
            "UPDATE lb_ops.write_requests SET status = 'executed' WHERE id = %s", (request_id,)
        )
    assert raised.value.sqlstate == "55000"


def test_the_writer_cannot_rewrite_the_proposal_only_decide_it(tools, clean):
    request_id = propose(tools, clean, {"status": "I"}, {"clave": CLIENT})
    with connect("writer") as conn:
        for column in ("tbl", "update_values", "where_values", "affected_rows", "requested_by"):
            value = "'faccab'" if column == "tbl" else "'{}'" if "values" in column else "'x'"
            if column == "affected_rows":
                value = "0"
            with pytest.raises(errors.InsufficientPrivilege):
                conn.execute(
                    f"UPDATE lb_ops.write_requests SET {column} = {value}::{'jsonb' if 'values' in column else 'text' if column != 'affected_rows' else 'int'} WHERE id = {request_id}"
                )
            conn.rollback()
