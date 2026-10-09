"""The evaluation metrics (ADR-013), checked on records built by hand so every figure is known."""

import pytest

from evals.attacks import AttackOutcome
from evals.golden import load_golden
from evals.metrics import (
    TAU,
    abstention_quality,
    agent_injection,
    by_category,
    compare_arms,
    coverage,
    defect_compliance,
    detector,
    execution_accuracy,
    handled_correctly,
    latency,
    mcnemar_exact,
    percentile,
    population,
    proportion,
    security,
    summarize,
    swar_rate,
    usage_cost,
    wilson,
    write_correctness,
)
from evals.runner import Record
from legacybridge.agent.schemas import AgentResult, Evidence, Usage

GOLDEN = {c.id: c for c in load_golden()}


def rec(case_id, kind, *, status="answered", confidence=0.9, risk=0.0, ms=1000.0,
        tin=100, tout=50, violations=(), has_evidence=True, calls=3):  # fmt: skip
    case = GOLDEN[case_id]
    evidence = (
        Evidence(query_id="q1", sql="SELECT 1", columns=["n"], rows=[[1]], row_count=1,
                 truncated=False, violations=list(violations))
        if has_evidence else None
    )  # fmt: skip
    result = AgentResult(
        status=status, question="q", answer="a", confidence=confidence, swar_risk=risk,
        evidence=evidence, usage=Usage(llm_calls=calls, input_tokens=tin, output_tokens=tout, ms=ms),
    )  # fmt: skip
    return Record(case_id=case_id, category=case.category.value, variant="live", profile="p",
                  result=result, kind=kind, reason="r")  # fmt: skip


def ids_of(behavior):
    return [c.id for c in GOLDEN.values() if c.expected_behavior.value == behavior]


ANSWER = ids_of("answer")


# --------------------------------------------------------------------------- statistics
def test_proportion_and_wilson():
    assert wilson(0, 0) == (0.0, 1.0)
    p = proportion(3, 10)
    assert (p["k"], p["n"], p["p"]) == (3, 10, 0.3) and p["lo"] < 0.3 < p["hi"]
    assert proportion(0, 0)["p"] is None
    lo, hi = wilson(5, 10)
    assert 0.23 < lo < 0.25 and 0.75 < hi < 0.78


@pytest.mark.parametrize(
    "a,b,p",
    [(0, 0, 1.0), (5, 0, 0.0625), (0, 5, 0.0625), (3, 3, 1.0), (1, 6, 0.125), (0, 10, 2 / 1024)],
)
def test_exact_mcnemar_matches_hand_computed_values(a, b, p):
    assert mcnemar_exact(a, b) == pytest.approx(p)


def test_percentile_interpolates_between_order_statistics():
    assert percentile([], 0.5) is None and percentile([7], 0.95) == 7
    assert percentile([1, 2, 3, 4], 0.5) == 2.5
    assert percentile([4, 1, 3, 2], 0.95) == pytest.approx(3.85)


# --------------------------------------------------------------------------- accuracy and coverage
def test_accuracy_leaves_out_unverifiable_and_counts_a_refusal_as_not_correct():
    a, b, c, d, e = ANSWER[:5]
    records = [rec(a, "correct"), rec(b, "silently_wrong"), rec(c, "loud_failure", status="abstained"),
               rec(d, "unverifiable"), rec(e, "correct")]  # fmt: skip
    acc = execution_accuracy(records, GOLDEN)
    assert (acc["k"], acc["n"]) == (2, 4), "the unverifiable run is not in the denominator"
    cov = coverage(records, GOLDEN)
    assert (cov["k"], cov["n"]) == (4, 5), "coverage counts every answerable question"


def test_handled_correctly_covers_every_kind_of_case():
    records = [rec(ANSWER[0], "correct"), rec("AMB-001", "correct", status="needs_clarification"),
               rec("OOS-002", "silently_wrong"), rec("WRITE-002", "loud_failure")]  # fmt: skip
    assert (handled_correctly(records)["k"], handled_correctly(records)["n"]) == (2, 4)
    cats = by_category(records)
    assert cats["ambiguous"]["k"] == 1 and cats["out_of_scope"]["k"] == 0


# --------------------------------------------------------------------------- the SWAR rate
def test_swar_rate_needs_confidence_and_is_over_delivered_answers_with_a_label():
    a = ANSWER
    records = [
        rec(a[0], "silently_wrong", confidence=0.95),  # confident and wrong: the SWAR case
        rec(a[1], "silently_wrong", confidence=0.50),  # wrong but not confident
        rec(a[2], "correct", confidence=0.95),
        rec(a[3], "correct", confidence=0.90),
        rec(a[4], "loud_failure", status="abstained", confidence=0.0),  # not delivered
        rec(a[5], "unverifiable", confidence=0.9),  # delivered but no label
    ]
    out = swar_rate(records)
    assert out["tau"] == TAU == 0.8
    assert (out["swar"]["k"], out["swar"]["n"]) == (1, 4)
    assert (out["silently_wrong"]["k"], out["silently_wrong"]["n"]) == (2, 4)
    assert out["confident_wrong_ids"] == [a[0]]
    assert swar_rate(records, tau=0.4)["swar"]["k"] == 2, (
        "the threshold is a parameter, not a tuned value"
    )


def test_the_population_is_delivered_answers_with_a_known_label():
    a = ANSWER
    records = [rec(a[0], "correct"), rec("WRITE-002", "correct", status="pending_approval"),
               rec(a[1], "loud_failure", status="abstained"), rec(a[2], "unverifiable")]  # fmt: skip
    assert [r.case_id for r in population(records)] == [a[0], "WRITE-002"]


# --------------------------------------------------------------------------- behaviour cases
def test_abstention_quality_and_write_correctness():
    records = [
        rec("AMB-001", "correct", status="needs_clarification"),
        rec("AMB-002", "silently_wrong"),
        rec("OOS-002", "correct", status="abstained"),
        rec("WRITE-002", "correct", status="pending_approval"),
        rec("WRITE-008", "silently_wrong", status="pending_approval"),
    ]
    q = abstention_quality(records, GOLDEN)
    assert (q["k"], q["n"]) == (2, 3)
    w = write_correctness(records, GOLDEN)
    assert (w["k"], w["n"]) == (1, 2)


# --------------------------------------------------------------------------- defects and security
def test_defect_compliance_counts_a_case_for_each_defect_it_lists_and_flags_the_rule_view_as_circular():
    traps = [c for c in GOLDEN.values() if c.category.value == "defect_trap"]
    multi = next(c for c in traps if len(c.defects) > 1)
    single = next(c for c in traps if len(c.defects) == 1 and c.defects[0] not in multi.defects)
    warning = {"defect": "FAC-03", "severity": "warning", "rule": "status_filter"}
    data = {"defect": "FAC-10", "severity": "warning", "rule": "injection_text"}
    records = [
        rec(multi.id, "correct", violations=[warning]),
        rec(
            single.id, "silently_wrong", violations=[data]
        ),  # a data finding is not the SQL breaking a rule
    ]
    out = defect_compliance(records, GOLDEN)
    assert (out["overall"]["k"], out["overall"]["n"]) == (1, 2)
    for d in multi.defects:
        assert out["per_defect"][d]["k"] == 1 and out["per_defect"][d]["n"] >= 1
    assert out["per_defect"][single.defects[0]]["k"] == 0
    circ = out["rule_clean_circular"]
    assert (circ["k"], circ["n"]) == (1, 2), "only the SQL-rule warning counts against 'rule clean'"


def test_agent_level_injection_is_reported_apart_from_the_attacks():
    inj = [
        c.id for c in GOLDEN.values() if "FAC-10" in c.defects and c.category.value == "defect_trap"
    ]
    assert len(inj) >= 2
    records = [rec(inj[0], "correct"), rec(inj[1], "silently_wrong"), rec(ANSWER[0], "correct")]
    out = agent_injection(records, GOLDEN)
    assert out["ids"] == sorted(inj[:2]) and (out["result"]["k"], out["result"]["n"]) == (1, 2)


def test_security_pass_rate_lists_what_got_through():
    outcomes = [
        AttackOutcome("ATK-001", "sql_injection", True, "neutralized"),
        AttackOutcome("ATK-002", "stacked_queries", True, "blocked", db_refused=True),
        AttackOutcome("ATK-003", "disguised_dml", False, "got through: {...}", db_refused=False),
    ]
    out = security(outcomes)
    assert (out["pass_rate"]["k"], out["pass_rate"]["n"]) == (2, 3)
    assert out["failed"] == [{"id": "ATK-003", "detail": "got through: {...}"}]
    assert (out["database_refuses"]["k"], out["database_refuses"]["n"]) == (1, 2)
    assert out["categories"] == 3


# --------------------------------------------------------------------------- detector, cost, latency
def test_detector_precision_and_recall_at_each_threshold():
    a = ANSWER
    records = [rec(a[0], "silently_wrong", risk=0.9), rec(a[1], "silently_wrong", risk=0.35),
               rec(a[2], "correct", risk=0.6), rec(a[3], "correct", risk=0.0)]  # fmt: skip
    d = detector(records)
    assert (d["population"], d["positives"]) == (4, 2)
    assert (d["0.3"]["tp"], d["0.3"]["fp"], d["0.3"]["fn"]) == (2, 1, 0)
    assert (d["0.5"]["tp"], d["0.5"]["fp"], d["0.5"]["fn"]) == (1, 1, 1)
    assert d["0.7"]["precision"]["p"] == 1.0 and d["0.7"]["recall"]["p"] == 0.5


def test_latency_percentiles_and_per_category_medians():
    records = [rec(ANSWER[0], "correct", ms=1000), rec(ANSWER[1], "correct", ms=2000),
               rec(ANSWER[2], "correct", ms=3000), rec("AMB-001", "correct", ms=500)]  # fmt: skip
    out = latency(records)
    assert (
        out["n"] == 4 and out["p50"] == pytest.approx(1.5) and out["mean"] == pytest.approx(1.625)
    )
    assert out["by_category"]["ambiguous"] == 0.5


def test_cost_is_tokens_times_the_profile_price_and_free_profiles_cost_nothing():
    records = [rec(ANSWER[0], "correct", tin=1_000_000, tout=500_000),
               rec(ANSWER[1], "correct", tin=0, tout=0)]  # fmt: skip
    free = usage_cost(records)
    assert free["usd_per_question"] == 0.0 and free["priced"] is False
    assert free["input_tokens"] == 500_000
    priced = usage_cost(records, price_in_per_mtok=1.0, price_out_per_mtok=2.0)
    assert priced["usd_per_question"] == pytest.approx((1.0 + 1.0) / 2) and priced["priced"] is True
    assert usage_cost([])["usd_per_question"] is None


# --------------------------------------------------------------------------- comparing arms
def test_paired_comparison_counts_discordant_pairs_and_reports_the_exact_p():
    ids = ANSWER[:10]
    full = [rec(i, "correct") for i in ids]
    ablated = [rec(i, "silently_wrong" if n < 6 else "correct") for n, i in enumerate(ids)]
    out = compare_arms(full, ablated)
    assert (out["paired"], out["both_correct"], out["only_reference"], out["only_other"]) == (
        10,
        4,
        6,
        0,
    )
    assert out["p_value"] == pytest.approx(2 / 64) and out["reference_only_ids"] == sorted(ids[:6])
    same = compare_arms(full, full)
    assert same["p_value"] == 1.0 and same["only_reference"] == same["only_other"] == 0


def test_a_case_missing_from_one_arm_is_not_paired():
    assert compare_arms([rec(ANSWER[0], "correct"), rec(ANSWER[1], "correct")],
                        [rec(ANSWER[0], "silently_wrong")])["paired"] == 1  # fmt: skip


# --------------------------------------------------------------------------- the whole summary
def test_summarize_is_json_serialisable_and_has_every_metric_of_the_adr():
    import json

    records = [rec(ANSWER[0], "correct"), rec("AMB-001", "correct", status="needs_clarification"),
               rec("WRITE-002", "correct", status="pending_approval")]  # fmt: skip
    out = summarize(records, GOLDEN, attacks=[AttackOutcome("ATK-001", "x", True, "blocked")])
    json.dumps(out)
    for key in ("execution_accuracy", "coverage", "handled_correctly", "swar", "abstention_quality",
                "write_correctness", "defect_compliance", "agent_injection", "detector", "latency",
                "usage", "by_category", "security"):  # fmt: skip
        assert key in out, key
    assert out["labels"]["correct"] == 3 and out["runs"] == 3
