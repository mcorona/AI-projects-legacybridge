"""The model-free demo (Phase 8): what it shows is true, it changes nothing, and it cleans up."""

import asyncio

import pytest

from evals import demo
from evals.runner import max_request_id

pytestmark = pytest.mark.integration


def lines():
    out = []
    return out, out.append


@pytest.fixture(scope="module")
def trap(db_available, tools):
    if not db_available:
        pytest.skip("database not reachable")
    out, say = lines()
    return asyncio.run(demo.act_trap(tools, say, demo.Style(color=False))), out


def test_the_naive_query_is_silently_wrong_and_flagged_and_the_reference_is_neither(trap):
    (naive, reference), out = trap
    assert (naive.name, reference.name) == ("naive", "reference")
    assert naive.label == "silently_wrong" and reference.label == "correct"
    assert [v["defect"] for v in naive.violations] == ["FAC-09"] and reference.violations == []
    assert naive.value - reference.value == 4, "four folios captured twice"
    assert naive.swar_risk > reference.swar_risk == 0.0
    text = "\n".join(out)
    assert "silently wrong" in text and "no violations" in text and "FAC-09 dedupe_required" in text


def test_the_demo_names_the_question_in_english_too(trap):
    _, out = trap
    assert demo.GLOSS[demo.TRAP_ID] in "\n".join(out)


def test_every_attack_shown_is_stopped_and_each_says_where(tools, db_available):
    if not db_available:
        pytest.skip("database not reachable")
    out, say = lines()
    outcomes = demo.act_attacks(tools, say, demo.Style(color=False))
    assert [o.attack_id for o in outcomes] == list(demo.ATTACK_IDS) and all(
        o.passed for o in outcomes
    )
    text = "\n".join(out)
    assert "refused at the guard: multiple_statements" in text and "neutralized" in text
    assert "GOT THROUGH" not in text


def test_the_write_is_queued_listed_rejected_and_leaves_no_trace(db_available, tools):
    if not db_available:
        pytest.skip("database not reachable")
    before = max_request_id()
    out, say = lines()
    facts = demo.act_write(tools, say, demo.Style(color=False))
    assert facts.affected_rows == 1 and facts.pending_listed and facts.decision == "rejected"
    assert facts.unchanged and facts.leftovers == 0
    assert max_request_id() == before, "the request the demo created is gone"
    assert "identical before and after" in "\n".join(out)
    assert facts.preview and facts.preview[0]["nombre"] in "\n".join(out)


def test_the_demo_cleans_up_even_when_something_fails_midway(db_available, tools, monkeypatch):
    if not db_available:
        pytest.skip("database not reachable")
    before = max_request_id()

    class Broken:
        def list(self, *a, **k):
            raise RuntimeError("the approval service is down")

    monkeypatch.setattr(demo, "ApprovalService", Broken)
    with pytest.raises(RuntimeError, match="down"):
        demo.act_write(tools, lambda _: None, demo.Style(color=False))
    assert max_request_id() == before, "the queued request is removed in the finally block"


# --------------------------------------------------------------------------- safety and style
def test_the_demo_refuses_a_database_that_is_not_marked_synthetic(monkeypatch, capsys):
    class Conn:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def execute(self, *a):
            class Row:
                def fetchone(inner):
                    return (None,)

            return Row()

    monkeypatch.setattr(demo, "connect", lambda *a, **k: Conn())
    assert demo.main([]) == 1
    assert "not marked synthetic" in capsys.readouterr().err


def test_an_unreachable_database_says_how_to_start_it(monkeypatch, capsys):
    def down(*a, **k):
        raise OSError("connection refused")

    monkeypatch.setattr(demo, "connect", down)
    assert demo.main([]) == 1
    assert "make db-up && make seed" in capsys.readouterr().err


def test_colors_appear_only_when_asked_for():
    assert demo.Style(color=False).bad("x") == "x"
    assert demo.Style(color=True).bad("x") == "\033[31mx\033[0m"


def test_a_single_act_can_be_run_on_its_own(db_available, capsys):
    if not db_available:
        pytest.skip("database not reachable")
    assert demo.main(["--act", "2", "--no-color"]) == 0
    out = capsys.readouterr().out
    assert "ACT 2" in out and "ACT 1" not in out and "ACT 3" not in out
