"""The eval gate fails when it should, and only then (ADR-013).

The sabotage tests break the system *around* the real source with `monkeypatch`: the guard is never
edited, and nothing is committed that weakens it. They show that a pull request that disabled the
guard, or made the agent answer wrongly, would turn the CI red.
"""

import copy
import json

import pytest

from evals import gate, runner
from evals.golden import load_golden
from evals.swar_eval import load_subset
from legacybridge.mcp_server import tools as tools_module
from legacybridge.mcp_server.sql_guard import GuardResult

BASELINE = gate.load_baseline()


def failed(checks):
    return [c.name for c in checks if not c.ok]


# --------------------------------------------------------------------------- the judging logic
def test_a_measurement_equal_to_the_baseline_passes_everything():
    checks = gate.evaluate(copy.deepcopy(BASELINE), BASELINE)
    assert failed(checks) == [] and len(checks) >= 10


def with_(measured, path, **changes):
    node = measured
    for key in path:
        node = node[key]
    node.update(changes)
    return measured


def test_a_reference_query_that_did_not_run_fails_the_gate_instead_of_shrinking_the_population():
    """Found in CI: on a database with no planner statistics the reference SQL of TRAP-007 timed out
    and was dropped, the gate still passed on the smaller population, and nobody noticed."""
    m = copy.deepcopy(BASELINE)
    m["reference_skipped"] = ["TRAP-007 (execution/timeout)"]
    assert failed(gate.evaluate(m, BASELINE)) == ["every reference query ran"]
    assert "TRAP-007 (execution/timeout)" in gate.render(gate.evaluate(m, BASELINE))
    m["reference_skipped"] = []
    assert failed(gate.evaluate(m, BASELINE)) == []


def test_the_skip_watcher_collects_reference_skips_only():
    assert gate.REFERENCE_SKIPPED.match("TRAP-007 oracle-expected: skipped (execution/timeout)")
    assert not gate.REFERENCE_SKIPPED.match(
        "TRAP-001 oracle-naive: skipped (execution/invalid_query)"
    )


def test_a_single_attack_getting_through_fails_the_gate():
    m = copy.deepcopy(BASELINE)
    with_(m, ["security", "attacks"], k=44, p=44 / 45)
    m["security"]["failed"] = [{"id": "ATK-007", "detail": "got through"}]
    bad = failed(gate.evaluate(m, BASELINE))
    assert bad == ["security pass rate is 100%"]
    assert "ATK-007" in gate.render(gate.evaluate(m, BASELINE))


def test_dropping_an_attack_or_a_database_refusal_fails_the_gate():
    m = copy.deepcopy(BASELINE)
    with_(m, ["security", "attacks"], k=44, n=44, p=1.0)
    assert failed(gate.evaluate(m, BASELINE)) == ["no attack was dropped"]
    m = copy.deepcopy(BASELINE)
    with_(m, ["security", "database_refuses"], k=10, p=10 / 11)
    assert failed(gate.evaluate(m, BASELINE)) == [
        "the database refuses what it should without the guard"
    ]


def test_the_swar_rate_may_rise_two_points_but_not_more():
    for k, ok in ((0, True), (1, False), (2, False)):  # baseline is 0/16; one case is 6.25 pp
        m = copy.deepcopy(BASELINE)
        with_(m, ["subset", "swar"], k=k, p=k / 16)
        assert (
            "subset: SWAR rate did not rise more than 2 pp"
            not in failed(gate.evaluate(m, BASELINE))
        ) is ok
    wide = copy.deepcopy(BASELINE)
    wide["subset"]["swar"] = {"k": 2, "n": 100, "p": 0.02, "lo": 0, "hi": 1}
    wide_base = copy.deepcopy(BASELINE)
    wide_base["subset"]["swar"] = {"k": 0, "n": 100, "p": 0.0, "lo": 0, "hi": 1}
    assert "subset: SWAR rate did not rise more than 2 pp" not in failed(
        gate.evaluate(wide, wide_base)
    ), "exactly two points is allowed"


def test_accuracy_must_not_fall_at_all_and_no_case_may_vanish():
    m = copy.deepcopy(BASELINE)
    with_(m, ["subset", "accuracy"], k=12, p=12 / 13)
    assert failed(gate.evaluate(m, BASELINE)) == ["subset: accuracy did not fall"]
    m = copy.deepcopy(BASELINE)
    m["subset"]["runs"] -= 1
    assert failed(gate.evaluate(m, BASELINE)) == ["subset: no case disappeared"]


def test_the_detector_may_lose_two_points_but_not_more():
    base = copy.deepcopy(BASELINE)
    base["detector"]["0.5"]["precision"] = {"k": 60, "n": 100, "p": 0.60, "lo": 0, "hi": 1}
    ok = copy.deepcopy(base)
    ok["detector"]["0.5"]["precision"] = {"k": 58, "n": 100, "p": 0.58, "lo": 0, "hi": 1}
    assert failed(gate.evaluate(ok, base)) == []
    worse = copy.deepcopy(base)
    worse["detector"]["0.5"]["precision"] = {"k": 57, "n": 100, "p": 0.57, "lo": 0, "hi": 1}
    assert failed(gate.evaluate(worse, base)) == [
        "detector precision at 0.5 did not fall more than 2 pp"
    ]
    silent = copy.deepcopy(base)
    silent["detector"]["0.5"]["recall"] = {"k": 0, "n": 0, "p": None, "lo": 0, "hi": 1}
    assert "detector recall at 0.5 did not fall more than 2 pp" in failed(
        gate.evaluate(silent, base)
    ), "a detector that stops flagging anything must not slip through as 'no data'"


def test_the_report_always_says_what_the_gate_does_not_measure():
    for measured in (copy.deepcopy(BASELINE),):
        text = gate.render(gate.evaluate(measured, BASELINE))
        assert "Eval gate PASSED" in text and "does not measure the quality of any model" in text
    bad = copy.deepcopy(BASELINE)
    with_(bad, ["subset", "accuracy"], k=0, p=0.0)
    text = gate.render(gate.evaluate(bad, BASELINE))
    assert "Eval gate FAILED" in text and "**FAIL**" in text and "does not measure" in text


def test_the_committed_baseline_is_complete_and_says_how_to_change_it():
    assert BASELINE["version"] == 1 and "Review the diff" in BASELINE["note"]
    assert BASELINE["security"]["attacks"]["k"] == BASELINE["security"]["attacks"]["n"] >= 45
    assert BASELINE["subset"]["accuracy"]["p"] == 1.0 and BASELINE["subset"]["swar"]["p"] == 0.0


# --------------------------------------------------------------------------- the real system
@pytest.fixture(scope="module")
def healthy(db_available):
    if not db_available:
        pytest.skip("database not reachable")
    return gate.measure()


@pytest.mark.integration
def test_the_healthy_tree_passes_against_the_committed_baseline(healthy):
    checks = gate.evaluate(healthy, BASELINE)
    assert failed(checks) == [], gate.render(checks)


# --------------------------------------------------------------------------- sabotage
def permissive_guard(sql, schema, max_rows=100, **kw):
    """What a pull request that switched the guard off would look like to the tools."""
    return GuardResult(ok=True, sql=sql, limit=max_rows)


@pytest.mark.integration
def test_a_change_that_disables_the_guard_turns_the_gate_red(db_available, monkeypatch):
    if not db_available:
        pytest.skip("database not reachable")
    monkeypatch.setattr(tools_module, "validate", permissive_guard)
    security = gate.measure_security()
    assert security["attacks"]["k"] < security["attacks"]["n"], "some attack must now get through"
    measured = copy.deepcopy(BASELINE)
    measured["security"] = security
    assert "security pass rate is 100%" in failed(gate.evaluate(measured, BASELINE))
    assert any(f["id"].startswith("ATK-") for f in security["failed"])


@pytest.mark.integration
@pytest.mark.anyio
async def test_a_change_that_makes_the_agent_answer_wrongly_and_confidently_turns_the_gate_red(
    db_available, monkeypatch
):
    if not db_available:
        pytest.skip("database not reachable")
    real = runner.Oracle
    monkeypatch.setattr(runner, "Oracle", lambda sql, answer: real("SELECT 0 AS n", "Resultado: 0"))
    subset = {c.id for c in load_subset()}
    cases = [c for c in load_golden() if c.id in subset]
    records = await runner.run_oracle(cases, log=lambda _: None)
    measured = copy.deepcopy(BASELINE)
    measured.update(gate.summarize_oracle(records, subset))
    bad = failed(gate.evaluate(measured, BASELINE))
    assert "subset: accuracy did not fall" in bad
    assert "subset: SWAR rate did not rise more than 2 pp" in bad


# --------------------------------------------------------------------------- the command
def test_without_a_baseline_the_gate_refuses_to_pass_silently(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(gate, "measure", lambda log=None: copy.deepcopy(BASELINE))
    assert gate.main(["--baseline", str(tmp_path / "none.json")]) == 1
    assert "run with --update once" in capsys.readouterr().err


def test_the_command_exit_code_follows_the_checks_and_writes_the_step_summary(
    tmp_path, monkeypatch
):
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    monkeypatch.setattr(gate, "measure", lambda log=None: copy.deepcopy(BASELINE))
    assert gate.main([]) == 0
    assert "Eval gate PASSED" in summary.read_text()
    bad = copy.deepcopy(BASELINE)
    with_(bad, ["subset", "accuracy"], k=0, p=0.0)
    monkeypatch.setattr(gate, "measure", lambda log=None: bad)
    assert gate.main([]) == 1


def test_a_baseline_is_never_written_while_an_attack_gets_through(tmp_path, monkeypatch, capsys):
    bad = copy.deepcopy(BASELINE)
    with_(bad, ["security", "attacks"], k=44, p=44 / 45)
    monkeypatch.setattr(gate, "measure", lambda log=None: bad)
    target = tmp_path / "gate.json"
    assert gate.main(["--update", "--baseline", str(target)]) == 1
    assert not target.exists() and "an attack gets through" in capsys.readouterr().err


def test_update_writes_a_baseline_that_the_same_measurement_then_passes(tmp_path, monkeypatch):
    measured = copy.deepcopy(BASELINE)
    monkeypatch.setattr(gate, "measure", lambda log=None: measured)
    target = tmp_path / "gate.json"
    assert gate.main(["--update", "--baseline", str(target)]) == 0
    written = json.loads(target.read_text())
    assert written["version"] == 1 and "commit" in written and "Review the diff" in written["note"]
    assert gate.main(["--baseline", str(target)]) == 0
