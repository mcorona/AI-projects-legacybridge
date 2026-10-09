"""The evaluation runner and CLI (ADR-013), driven by a model that costs nothing."""

import json

import pytest

from evals import run_evals
from evals.golden import load_golden
from evals.runner import ARMS, Record, arm_config, git_state, load_records, models_used, run_golden
from legacybridge.db import connect
from legacybridge.llm.base import LLMError, LLMResponse

pytestmark = pytest.mark.integration
GOLDEN = {c.id: c for c in load_golden()}
CASES = [GOLDEN[i] for i in ("SIMPLE-003", "AMB-001", "OOS-002")]


class Abstainer:
    """A model that declines every question: enough to exercise the whole run, for free."""

    name = "abstainer"

    async def chat(self, messages, tools=None, **kw):
        body = {"action": "abstain", "language": "es", "intent": "x", "reason": "not for me"}
        return LLMResponse(text=json.dumps(body), input_tokens=10, output_tokens=5, model="stub-model",
                           stop_reason="stop")  # fmt: skip


@pytest.fixture
def need_db(db_available):
    if not db_available:
        pytest.skip("database not reachable")


# --------------------------------------------------------------------------- arms
def test_each_arm_changes_exactly_one_thing():
    full = arm_config("full")
    assert (full.verify, full.semantic, full.swar_action) == (True, True, "annotate")
    assert arm_config("no-verify").verify is False and arm_config("no-verify").semantic is True
    assert arm_config("no-semantic").semantic is False and arm_config("no-semantic").verify is True
    assert arm_config("full", "gate").swar_action == "gate"
    with pytest.raises(ValueError, match="unknown arm"):
        arm_config("no-everything")
    assert set(ARMS) == {"full", "no-verify", "no-semantic"}


def test_the_git_state_names_a_commit_and_says_whether_the_tree_is_clean():
    commit, dirty = git_state()
    assert commit and isinstance(dirty, bool)


# --------------------------------------------------------------------------- a run
@pytest.mark.anyio
async def test_a_run_labels_every_case_stores_it_and_leaves_the_data_alone(need_db, tmp_path):
    out = tmp_path / "run.jsonl"
    lines: list[str] = []
    records = await run_golden("stub", CASES, out, arm="full", llm=Abstainer(), log=lines.append)
    assert [r.case_id for r in records] == ["SIMPLE-003", "AMB-001", "OOS-002"]
    assert [r.kind for r in records] == ["loud_failure", "loud_failure", "correct"]
    assert all(r.arm == "full" and r.commit and r.profile == "stub" for r in records)
    assert len(out.read_text().splitlines()) == 3 and len(lines) == 3
    assert models_used(records) == ["stub-model"]


@pytest.mark.anyio
async def test_a_stopped_run_resumes_without_asking_again(need_db, tmp_path):
    out = tmp_path / "run.jsonl"
    await run_golden("stub", CASES[:2], out, llm=Abstainer(), log=lambda _: None)
    lines: list[str] = []
    records = await run_golden("stub", CASES, out, llm=Abstainer(), log=lines.append)
    assert sum("already in" in line for line in lines) == 2
    assert [r.case_id for r in records] == [c.id for c in CASES], (
        "the new case was appended in order"
    )
    assert len(load_records(out)) == 3


@pytest.mark.anyio
async def test_the_ablation_arms_run_end_to_end(need_db, tmp_path):
    for arm in ("no-verify", "no-semantic"):
        out = tmp_path / f"{arm}.jsonl"
        records = await run_golden(
            "stub", CASES[2:], out, arm=arm, llm=Abstainer(), log=lambda _: None
        )
        assert records[0].arm == arm and records[0].kind == "correct"


@pytest.mark.anyio
async def test_a_run_never_leaves_requests_in_the_queue(need_db, tmp_path):
    with connect("admin") as admin:
        before = admin.execute("SELECT count(*) FROM lb_ops.write_requests").fetchone()[0]
        admin.rollback()
    await run_golden("stub", CASES, tmp_path / "q.jsonl", llm=Abstainer(), log=lambda _: None)
    with connect("admin") as admin:
        assert admin.execute("SELECT count(*) FROM lb_ops.write_requests").fetchone()[0] == before
        admin.rollback()


def test_records_written_before_phase_6_still_load():
    old = {"case_id": "X", "category": "simple", "variant": "live", "profile": "p",
           "result": {"status": "abstained", "question": "q", "answer": "a"}, "kind": "loud_failure",
           "reason": "r"}  # fmt: skip
    rec = Record.model_validate(old)
    assert (rec.arm, rec.commit, rec.dirty) == ("full", "", False)


# --------------------------------------------------------------------------- the CLI
def test_the_attacks_set_runs_against_the_real_tools(need_db):
    outcomes = run_evals.run_attacks(log=lambda _: None)
    assert len(outcomes) >= 45 and all(o.passed for o in outcomes)


def test_result_files_are_named_after_the_run_profile_and_arm(tmp_path):
    assert run_evals.golden_path("20261009", "local-qwen", "no-verify", tmp_path).name == (
        "eval-20261009-local-qwen-no-verify.jsonl"
    )
    assert run_evals.attacks_path("20261009", tmp_path).name == "attacks-20261009.json"


def test_unknown_case_ids_are_refused():
    assert len(run_evals.pick_cases(None, 5)) == 5
    assert [c.id for c in run_evals.pick_cases("AMB-001,OOS-002", None)] == ["AMB-001", "OOS-002"]
    with pytest.raises(SystemExit, match="unknown case ids"):
        run_evals.pick_cases("NOPE-1", None)


def test_unknown_arms_and_sets_are_refused():
    with pytest.raises(SystemExit):
        run_evals.main(["--arm", "no-everything", "--set", "golden"])
    with pytest.raises(SystemExit):
        run_evals.main(["--set", "everything"])


@pytest.mark.anyio
async def test_a_profile_that_cannot_answer_stops_the_run_before_any_case(monkeypatch):
    class Broken:
        async def chat(self, *a, **k):
            raise LLMError("omniroute: gave up after 4 attempts (InternalServerError 500)")

    monkeypatch.setattr(run_evals, "build_llm", lambda name: Broken())
    with pytest.raises(SystemExit, match="cannot answer right now"):
        await run_evals.probe("omniroute-free", lambda _: None)
