"""The measurement harness (evals/swar_eval.py): its arithmetic, its report, and a regression
guard on the deterministic baseline (no LLM)."""

import pytest

from evals.golden import load_golden
from evals.swar_eval import (
    Record,
    build_report,
    confusion,
    load_subset,
    population,
    risk_with,
    run_oracle,
    wilson,
)
from legacybridge.agent.schemas import AgentResult, Evidence, SwarSignal
from legacybridge.observability.swar_config import load_swar_config

CFG = load_swar_config()


def rec(case_id, kind, risk, status="answered", signals=(), variant="live", events=()):
    result = AgentResult(
        status=status, question="q", answer="a", swar_risk=risk, swar_level=CFG.level(risk),
        swar_signals=[
            SwarSignal(name=n, family=CFG.signals[n].family, weight=CFG.signals[n].weight)
            for n in signals
        ],
        events=list(events), evidence=Evidence(query_id="q1", sql="SELECT 1", columns=["n"], rows=[[1]], row_count=1, truncated=False),
    )  # fmt: skip
    return Record(case_id=case_id, category="simple", variant=variant, profile="p", result=result,
                  kind=kind, reason="because")  # fmt: skip


# --------------------------------------------------------------------------- arithmetic
def test_wilson_interval():
    assert wilson(0, 0) == (0.0, 1.0)
    lo, hi = wilson(5, 10)
    assert 0.23 < lo < 0.25 and 0.75 < hi < 0.78
    lo, hi = wilson(0, 13)
    assert lo == 0.0 and 0.2 < hi < 0.25, "no successes in 13 still leaves a wide interval"
    assert wilson(10, 10)[1] == 1.0


def test_confusion_counts_and_ratios():
    rows = [(0.9, True), (0.6, True), (0.2, True), (0.8, False), (0.1, False), (0.1, False)]
    c = confusion(rows, 0.5)
    assert (c.tp, c.fp, c.fn, c.tn) == (2, 1, 1, 2)
    assert c.precision == pytest.approx(2 / 3) and c.recall == pytest.approx(2 / 3)
    assert confusion([], 0.5).precision is None and confusion([(0.1, False)], 0.5).recall is None


def test_the_population_is_delivered_answers_with_a_known_label():
    records = [
        rec("a", "correct", 0.1),
        rec("b", "silently_wrong", 0.8),
        rec("c", "loud_failure", 0.0, status="abstained"),
        rec("d", "unverifiable", 0.2),
        rec("e", "silently_wrong", 0.7, status="pending_approval"),
    ]
    assert [r.case_id for r in population(records)] == ["a", "b", "e"]


def test_risk_can_be_recomputed_from_a_subset_of_signals():
    r = rec("a", "correct", 0.9, signals=("unresolved_violations", "zero_rows")).result
    only_rules = risk_with(r, CFG, {"unresolved_violations"})
    without_rules = risk_with(r, CFG, None, {"unresolved_violations"})
    assert only_rules == 0.5 and without_rules == 0.4


# --------------------------------------------------------------------------- the report
def test_the_report_states_counts_intervals_and_limits():
    ontology = __import__(
        "legacybridge.semantic.loader", fromlist=["load_ontology"]
    ).load_ontology()
    records = [
        rec("a", "correct", 0.1),
        rec("b", "silently_wrong", 0.8, signals=("unresolved_violations",)),
        rec("c", "silently_wrong", 0.1),
        rec("d", "loud_failure", 0.0, status="abstained"),
    ]
    text = build_report(records, "unit", ontology, CFG, notes=["a note"])
    assert text.startswith("# SWAR detector measurement: unit")
    assert "**3** delivered answers" in text and "**2 silently wrong**" in text
    assert "| >= 0.5 | 1 | 1 | 0 | 1 | 1 |" in text
    assert "1 of 2 silently wrong answers raised no rule signal" in text and "- c (live)" in text
    assert "> a note" in text and "Small sample" in text and "Circularity" in text
    assert "none was fitted" in text


# --------------------------------------------------------------------------- the fixed subset
def test_load_subset_returns_the_twenty_cases_in_order():
    cases = load_subset()
    assert len(cases) == 20 and cases[0].id == "SIMPLE-003" and cases[-1].id == "WRITE-009"


# --------------------------------------------------------------------------- baseline regression
@pytest.mark.integration
@pytest.mark.anyio
async def test_the_baseline_keeps_its_measured_properties(db_available):
    """Regression guard, not a target: these are what the first baseline measured (see
    evals/reports/). A change to the detector that moves them must be a conscious one."""
    if not db_available:
        pytest.skip("legacy Postgres is not reachable")
    records = await run_oracle(load_golden(), log=lambda _: None)
    pop = population(records)
    rows = [(r.result.swar_risk, r.kind == "silently_wrong") for r in pop]

    wrong = [r for r in pop if r.kind == "silently_wrong"]
    assert {r.variant for r in wrong} == {"oracle-naive"} and len(wrong) == 13

    c = confusion(rows, 0.5)
    assert (c.tp, c.fn) == (12, 1), "the rules see 12 of the 13 silently wrong naive answers"
    assert c.fp == 8, "and flag eight correct answers: the 8 neutral cases of EXPECTED_NOISE"
    assert (
        confusion(rows, CFG.action_threshold).tp + confusion(rows, CFG.action_threshold).fp == 0
    ), "a lone heuristic never reaches the action threshold"
    traps_expected = [
        r for r in pop if r.variant == "oracle-expected" and r.category == "defect_trap"
    ]
    assert traps_expected and all(r.result.swar_risk < 0.5 for r in traps_expected)
