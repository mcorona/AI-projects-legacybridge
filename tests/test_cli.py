"""The ``legacybridge`` command line."""

import json

import pytest

from legacybridge.agent.hitl import ApprovalService
from legacybridge.agent.schemas import AgentResult
from legacybridge.cli import build_parser, format_result, main
from legacybridge.semantic.loader import load_ontology
from tests.support.scripted import ScriptedLLM, call, plan

QUESTION = "¿Cuántas facturas activas hubo en 2004?"
ONTOLOGY = load_ontology()
METRIC = ONTOLOGY.render_metric("facturas_vigentes", year=2004)


def scripted():
    return ScriptedLLM(
        planner=[plan("query")],
        writer=[
            call("run_query", sql=METRIC),
            call("submit_answer", answer="Hubo 829 facturas vigentes en 2004.", outcome="answer",
                 evidence_query="q1", assumptions=["una por folio"]),
        ],
        verifier=[call("run_query", sql=METRIC), call("submit_verdict", agrees=True, confidence=0.9)],
    )  # fmt: skip


@pytest.mark.integration
def test_ask_prints_a_readable_answer(capsys):
    assert main(["ask", QUESTION, "--backend", "local"], llm=scripted()) == 0
    out = capsys.readouterr().out
    assert out.startswith("[answered] confidence 0.90, verified")
    assert "829" in out and "SQL: " in out and "- una por folio" in out
    assert "scripted: 5 model calls, 2 tool calls" in out


@pytest.mark.integration
def test_ask_json_is_the_full_structured_result(capsys):
    assert main(["ask", QUESTION, "--backend", "local", "--json"], llm=scripted()) == 0
    result = AgentResult.model_validate_json(capsys.readouterr().out)
    assert result.status == "answered" and result.evidence.rows == [[829]] and result.events


@pytest.mark.integration
def test_no_verify_skips_the_verifier(capsys):
    llm = scripted()
    llm.queues["verifier"] = []
    assert main(["ask", QUESTION, "--backend", "local", "--no-verify"], llm=llm) == 0
    assert "not verified" in capsys.readouterr().out and llm.sent("verifier") == []


def test_a_bad_profile_is_a_clean_error_not_a_traceback(capsys):
    assert main(["ask", QUESTION, "--profile", "nope", "--backend", "local"]) == 2
    assert "unknown profile 'nope'" in capsys.readouterr().err


def test_a_placeholder_api_key_is_a_clean_error(monkeypatch, capsys):
    monkeypatch.setenv("OMNIROUTE_API_KEY", "changeme")
    assert main(["ask", QUESTION, "--profile", "omniroute-free", "--backend", "local"]) == 2
    assert "OMNIROUTE_API_KEY still has the placeholder" in capsys.readouterr().err


def test_the_parser_requires_what_a_decision_needs():
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["approvals", "approve", "3"])  # no --reviewer
    args = parser.parse_args(["approvals", "approve", "3", "--reviewer", "ana", "--note", "ok"])
    assert (args.id, args.reviewer, args.note) == (3, "ana", "ok")
    assert parser.parse_args(["ask", "hola"]).profile == "local-qwen"


def test_format_result_for_each_status():
    base = {"question": "q", "profile": "p"}
    pending = format_result(
        AgentResult(status="pending_approval", answer="waiting", request_id=7, **base)
    )
    assert "[pending_approval]" in pending and "approvals show 7" in pending
    abstained = format_result(
        AgentResult(status="abstained", answer="no", abstain_reason="out_of_scope", **base)
    )
    assert (
        "[abstained]\n" in abstained
        and "Reason: out_of_scope" in abstained
        and "confidence" not in abstained
    )


# --------------------------------------------------------------------------- approvals
@pytest.fixture
def request_id(tools, admin):
    snapshot = admin.execute("SELECT clave, status FROM clientes").fetchall()
    first_id = admin.execute("SELECT coalesce(max(id), 0) FROM lb_ops.write_requests").fetchone()[0]
    admin.rollback()
    out = tools.propose_write("clientes", {"status": "I"}, {"clave": "0000000100"}, "cli test")
    yield out["request_id"]
    admin.rollback()
    with admin.cursor() as cur:
        cur.executemany(
            "UPDATE clientes SET status = %s WHERE clave = %s", [(s, c) for c, s in snapshot]
        )
    admin.execute("DELETE FROM lb_ops.write_requests WHERE id > %s", (first_id,))
    admin.commit()


@pytest.mark.integration
def test_a_person_lists_shows_and_approves_from_the_terminal(request_id, admin, capsys):
    service = ApprovalService()
    assert main(["approvals", "list", "--status", "pending"], service=service) == 0
    assert f"#{request_id}" in capsys.readouterr().out
    assert main(["approvals", "show", str(request_id)], service=service) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "pending"

    assert (
        main(["approvals", "approve", str(request_id), "--reviewer", "ana"], service=service) == 0
    )
    assert "is now executed (reviewer ana)" in capsys.readouterr().out
    assert (
        admin.execute("SELECT status FROM clientes WHERE clave = '0000000100'").fetchone()[0] == "I"
    )
    admin.rollback()

    assert (
        main(["approvals", "approve", str(request_id), "--reviewer", "luis"], service=service) == 1
    )
    assert "already_decided" in capsys.readouterr().err


@pytest.mark.integration
def test_rejecting_and_refused_decisions_from_the_terminal(request_id, admin, capsys):
    service = ApprovalService()
    assert (
        main(["approvals", "approve", str(request_id), "--reviewer", "MCP"], service=service) == 1
    )
    assert "self_approval" in capsys.readouterr().err
    assert (
        main(
            ["approvals", "reject", str(request_id), "--reviewer", "ana", "--note", "no"],
            service=service,
        )
        == 0
    )
    assert "is now rejected" in capsys.readouterr().out
    assert (
        admin.execute("SELECT status FROM clientes WHERE clave = '0000000100'").fetchone()[0] == "A"
    )
    admin.rollback()
    assert main(["approvals", "show", "999999999"], service=service) == 1
