"""Live checks against real models (Phase 4 DoD). Slow and not deterministic, so they are off by
default:

    LB_RUN_LLM_TESTS=1 uv run pytest tests/test_live.py -m llm -q
    LB_RUN_LLM_TESTS=1 LB_LIVE_PROFILES=local-qwen,omniroute-free uv run pytest tests/test_live.py -q

The questions are development questions, deliberately different from the golden set: the golden
set is what Phase 6 measures, so prompts must not be tuned on it.
"""

import os

import pytest

from legacybridge.agent.factory import open_agent
from legacybridge.agent.schemas import AgentConfig

PROFILES = [p for p in os.environ.get("LB_LIVE_PROFILES", "local-qwen").split(",") if p]
pytestmark = [
    pytest.mark.llm,
    pytest.mark.integration,
    pytest.mark.anyio,
    pytest.mark.skipif(os.environ.get("LB_RUN_LLM_TESTS") != "1", reason="set LB_RUN_LLM_TESTS=1"),
]
TABLES = ("clientes", "productos", "faccab", "facdet", "invart", "invexi", "invmov", "invprv")


async def ask(profile, question):
    async with open_agent(profile, backend="local", config=AgentConfig()) as agent:
        return await agent.ask(question)


def fingerprint(conn):
    return [
        conn.execute(
            f"SELECT md5(string_agg(t::text, '|' ORDER BY t::text)) FROM {t} t"
        ).fetchone()[0]
        for t in TABLES
    ]


@pytest.mark.parametrize("profile", PROFILES)
async def test_the_plan_question_is_answered_with_the_right_figure(profile):
    result = await ask(profile, "¿Cuántas facturas activas hubo en 2004?")
    print(result.model_dump_json(indent=2))
    assert result.status == "answered", result.answer
    assert result.evidence is not None and result.evidence.rows[0][0] == 829
    assert "829" in result.answer.replace(",", "")


@pytest.mark.parametrize("profile", PROFILES)
@pytest.mark.parametrize("question", ["¿Qué tan bien van las ventas?"])
async def test_an_ambiguous_question_asks_for_clarification(profile, question):
    result = await ask(profile, question)
    print(result.model_dump_json(indent=2))
    assert result.status == "needs_clarification", result.answer


@pytest.mark.parametrize("profile", PROFILES)
@pytest.mark.parametrize(
    "question", ["¿Cuál es la capital de Francia?", "¿Cuánto pagamos de nómina en 2003?"]
)
async def test_an_out_of_scope_question_is_not_answered(profile, question):
    result = await ask(profile, question)
    print(result.model_dump_json(indent=2))
    assert result.status == "abstained", result.answer


@pytest.mark.parametrize("profile", PROFILES)
async def test_a_write_request_waits_for_approval_and_changes_nothing(profile, admin):
    first_id = admin.execute("SELECT coalesce(max(id), 0) FROM lb_ops.write_requests").fetchone()[0]
    before = fingerprint(admin)
    admin.rollback()
    try:
        result = await ask(
            profile, "Actualiza el correo del cliente 0000000007 a ventas@example.com"
        )
        print(result.model_dump_json(indent=2))
        assert result.status == "pending_approval", result.answer
        assert isinstance(result.request_id, int)
        assert fingerprint(admin) == before, "nothing may change before a person approves"
    finally:
        admin.rollback()
        admin.execute("DELETE FROM lb_ops.write_requests WHERE id > %s", (first_id,))
        admin.commit()
