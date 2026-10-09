"""HTTP API (Phase 4): /ask, and the approval endpoints behind a bearer token."""

import pytest
from fastapi.testclient import TestClient

from legacybridge.agent.hitl import ApprovalService
from legacybridge.agent.schemas import AgentResult
from legacybridge.api.app import create_app
from legacybridge.llm.base import LLMUnavailable
from legacybridge.llm.profiles import ProfileError

TOKEN = "test-token-123"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
CLIENT = "0000000100"


async def fake_runner(question, profile, verify):
    return AgentResult(
        status="answered",
        question=question,
        answer=f"{profile}/{verify}",
        confidence=0.9,
        profile=profile,
    )


def client(token=TOKEN, runner=fake_runner):
    return TestClient(create_app(runner=runner, token=token))


def test_health_and_ask_use_the_injected_agent():
    c = client()
    assert c.get("/health").json() == {"status": "ok"}
    out = c.post(
        "/ask",
        json={"question": "¿Cuántas facturas hubo?", "profile": "omniroute-free", "verify": False},
    )
    assert out.status_code == 200
    assert (out.json()["status"], out.json()["answer"]) == ("answered", "omniroute-free/False")
    assert c.post("/ask", json={"question": "hola"}).json()["answer"] == "local-qwen/True"


def test_ask_validates_its_input_and_maps_errors():
    c = client()
    assert c.post("/ask", json={"question": "a"}).status_code == 422
    assert c.post("/ask", json={"question": "x" * 2001}).status_code == 422

    def failing(exc):
        async def run(*args):
            raise exc

        return client(runner=run)

    assert (
        failing(ProfileError("unknown profile 'x'"))
        .post("/ask", json={"question": "hola!"})
        .status_code
        == 422
    )
    assert (
        failing(ProfileError("needs LMSTUDIO_MODEL"))
        .post("/ask", json={"question": "hola!"})
        .status_code
        == 503
    )
    assert (
        failing(LLMUnavailable("down")).post("/ask", json={"question": "hola!"}).status_code == 502
    )


@pytest.mark.parametrize(
    "method,path",
    [("get", "/approvals"), ("get", "/approvals/1"), ("post", "/approvals/1")],
)
def test_approvals_fail_closed_without_a_configured_token(method, path):
    c = client(token="")
    body = {"json": {"decision": "approve", "reviewer": "ana"}} if method == "post" else {}
    response = getattr(c, method)(path, headers=AUTH, **body)
    assert response.status_code == 503 and "disabled" in response.json()["detail"]


@pytest.mark.parametrize(
    "headers",
    [{}, {"Authorization": "Bearer wrong"}, {"Authorization": "Basic x"}, {"Authorization": TOKEN}],
)
def test_approvals_reject_a_missing_or_wrong_token(headers):
    c = client()
    for method, path in (("get", "/approvals"), ("get", "/approvals/1")):
        response = getattr(c, method)(path, headers=headers)
        assert response.status_code == 401 and response.headers["www-authenticate"] == "Bearer"
    response = c.post(
        "/approvals/1", headers=headers, json={"decision": "approve", "reviewer": "ana"}
    )
    assert response.status_code == 401


def test_only_two_routes_can_change_anything_and_neither_is_a_tool():
    routes = {
        (r.path, m)
        for r in create_app(runner=fake_runner, token=TOKEN).routes
        for m in getattr(r, "methods", ())
    }
    assert {p for p, m in routes if m == "POST"} == {"/ask", "/approvals/{request_id}"}


# --------------------------------------------------------------------------- with the database
@pytest.fixture
def pending(tools, admin):
    snapshot = admin.execute("SELECT clave, status FROM clientes").fetchall()
    first_id = admin.execute("SELECT coalesce(max(id), 0) FROM lb_ops.write_requests").fetchone()[0]
    admin.rollback()
    out = tools.propose_write("clientes", {"status": "I"}, {"clave": CLIENT}, "api test")
    assert out["ok"]
    yield out["request_id"]
    admin.rollback()
    with admin.cursor() as cur:
        cur.executemany(
            "UPDATE clientes SET status = %s WHERE clave = %s", [(s, c) for c, s in snapshot]
        )
    admin.execute("DELETE FROM lb_ops.write_requests WHERE id > %s", (first_id,))
    admin.commit()


@pytest.mark.integration
def test_a_reviewer_lists_inspects_and_approves(pending, admin):
    c = TestClient(create_app(runner=fake_runner, approvals=ApprovalService(), token=TOKEN))
    listed = c.get("/approvals", headers=AUTH, params={"status": "pending"}).json()
    assert pending in [r["id"] for r in listed]
    detail = c.get(f"/approvals/{pending}", headers=AUTH).json()
    assert detail["status"] == "pending" and detail["audit"][0]["event"] == "proposed"

    done = c.post(
        f"/approvals/{pending}", headers=AUTH, json={"decision": "approve", "reviewer": "ana"}
    )
    assert done.status_code == 200 and done.json()["status"] == "executed"
    assert (
        admin.execute("SELECT status FROM clientes WHERE clave = %s", (CLIENT,)).fetchone()[0]
        == "I"
    )
    admin.rollback()

    again = c.post(
        f"/approvals/{pending}", headers=AUTH, json={"decision": "reject", "reviewer": "luis"}
    )
    assert again.status_code == 409 and again.json()["error"] == "already_decided"


@pytest.mark.integration
def test_rejecting_self_approval_unknown_ids_and_bad_bodies(pending, admin):
    c = TestClient(create_app(runner=fake_runner, approvals=ApprovalService(), token=TOKEN))
    own = c.post(
        f"/approvals/{pending}", headers=AUTH, json={"decision": "approve", "reviewer": "mcp"}
    )
    assert own.status_code == 403 and own.json()["error"] == "self_approval"
    assert (
        c.post(
            "/approvals/999999999", headers=AUTH, json={"decision": "approve", "reviewer": "a"}
        ).status_code
        == 404
    )
    assert (
        c.post(
            f"/approvals/{pending}", headers=AUTH, json={"decision": "maybe", "reviewer": "a"}
        ).status_code
        == 422
    )
    assert (
        c.post(f"/approvals/{pending}", headers=AUTH, json={"decision": "approve"}).status_code
        == 422
    )
    rejected = c.post(
        f"/approvals/{pending}",
        headers=AUTH,
        json={"decision": "reject", "reviewer": "ana", "note": "no"},
    )
    assert rejected.json()["status"] == "rejected"
    assert (
        admin.execute("SELECT status FROM clientes WHERE clave = %s", (CLIENT,)).fetchone()[0]
        == "A"
    )
    admin.rollback()


@pytest.mark.integration
def test_the_real_app_starts_the_mcp_server_in_its_lifespan_and_reports_profile_problems():
    with TestClient(create_app(token=TOKEN)) as c:  # no injected runner: spawns the real server
        assert c.get("/health").json() == {"status": "ok"}
        response = c.post("/ask", json={"question": "¿Cuántos clientes hay?", "profile": "nope"})
        assert response.status_code == 422 and "unknown profile" in response.json()["detail"]
