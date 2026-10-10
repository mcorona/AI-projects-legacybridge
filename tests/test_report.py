"""The report generator (ADR-013): it reads stored results and writes figures it can account for."""

from pathlib import Path

import pytest

from evals import report
from evals.attacks import AttackOutcome, save_outcomes
from evals.report import (
    Run,
    build,
    corrections,
    latest_run_id,
    load_run,
    prices,
)
from tests.support.records import GOLDEN, rec


def ids(behavior):
    return [c.id for c in GOLDEN.values() if c.expected_behavior.value == behavior]


ANSWER = ids("answer")
ATTACKS = [AttackOutcome("ATK-001", "sql_injection", True, "neutralized"),
           AttackOutcome("ATK-002", "stacked_queries", True, "blocked", db_refused=True)]  # fmt: skip


def run_with(*arms):
    run = Run("t1", attacks=ATTACKS)
    for (profile, arm), records in arms:
        run.arms[(profile, arm)] = records
    return run


def good(profile, arm, n_wrong=0):
    return ((profile, arm), [rec(c, "silently_wrong" if i < n_wrong else "correct", confidence=0.95)
                             for i, c in enumerate(ANSWER[:10])])  # fmt: skip


# --------------------------------------------------------------------------- loading
def test_result_files_are_grouped_by_profile_and_arm_even_with_hyphens(tmp_path):
    from evals.runner import load_records  # noqa: F401

    for name in ("local-qwen-full", "local-qwen-no-verify", "omniroute-free-full"):
        (tmp_path / f"eval-t1-{name}.jsonl").write_text(
            rec(ANSWER[0], "correct").model_dump_json() + "\n"
        )
    (tmp_path / "eval-t1-garbage.jsonl").write_text("")
    save_outcomes(ATTACKS, tmp_path / "attacks-t1.json")
    run = load_run("t1", tmp_path)
    assert sorted(run.arms) == [
        ("local-qwen", "full"),
        ("local-qwen", "no-verify"),
        ("omniroute-free", "full"),
    ]
    assert run.profiles == ["local-qwen", "omniroute-free"]
    assert [o.attack_id for o in run.attacks] == ["ATK-001", "ATK-002"]
    assert latest_run_id(tmp_path) == "t1"


def test_there_is_no_latest_run_when_nothing_was_run(tmp_path):
    assert latest_run_id(tmp_path) is None


def test_prices_come_from_the_config_and_default_to_free(tmp_path):
    cfg = tmp_path / "models.yaml"
    cfg.write_text("profiles:\n  a: {price_in_per_mtok: 1.5, price_out_per_mtok: 7}\n  b: {}\n")
    assert (
        prices("a", cfg) == (1.5, 7.0)
        and prices("b", cfg) == (0.0, 0.0)
        and prices("zzz", cfg) == (0.0, 0.0)
    )
    assert prices("local-qwen") == (0.0, 0.0) and prices("omniroute-free") == (0.0, 0.0)


# --------------------------------------------------------------------------- the text
def test_the_report_has_every_section_and_accounts_for_its_figures():
    run = run_with(good("local-qwen", "full", n_wrong=2), good("omniroute-free", "full", n_wrong=1))
    text = build(run)
    for heading in ("## Headline", "## Security", "## By category", "## Defect compliance",
                    "## The SWAR detector", "## Ablation", "## Appendix", "## How to read this"):  # fmt: skip
        assert heading in text, heading
    assert "| Metric (full arm) | local-qwen | omniroute-free |" in text
    assert "0.20 (2/10; CI" in text, "2 confident silently wrong answers out of 10 delivered"
    assert "0.10 (1/10; CI" in text
    assert "**Security pass rate: 1.00 (2/2" in text
    assert "unpriced: local or free tier" in text
    assert "`review: approved`" in text and "not measure" in text.lower()
    assert "not an independent benchmark" in text
    assert "No ablation arms were run" in text


def test_every_run_that_is_not_correct_is_listed_with_its_reason():
    run = run_with(good("local-qwen", "full", n_wrong=2))
    text = build(run)
    appendix = text.split("## Appendix")[1].split("## How to read")[0]
    assert (
        appendix.count("silently_wrong") == 2
        and ANSWER[0] in appendix
        and "**local-qwen / full**" in appendix
    )


def test_unrun_arms_and_profiles_are_said_not_filled_in():
    run = run_with(good("local-qwen", "full"))
    run.arms[("omniroute-free", "no-verify")] = [
        rec(ANSWER[0], "correct")
    ]  # a profile with no full arm
    assert "not run" in build(run)


# --------------------------------------------------------------------------- what the ablation caveats say
def test_the_ablation_counts_runs_that_ran_out_of_steps_per_arm():
    full = good("local-qwen", "full")
    stuck = ((("local-qwen", "no-semantic")), [
        rec(c, "loud_failure", status="abstained", confidence=0.0, abstain_reason="no_answer")
        for c in ANSWER[:4]] + [rec(c, "correct") for c in ANSWER[4:10]])  # fmt: skip
    text = build(run_with(full, stuck))
    assert "full 0/10, no-semantic 4/10" in text and "steps, not wrong answers" in text


def test_a_verifier_free_arm_that_cannot_reach_the_threshold_says_its_swar_rate_is_zero_by_construction():
    capped = good("local-qwen", "no-verify", n_wrong=3)
    capped[1][:] = [rec(r.case_id, r.kind, confidence=0.55) for r in capped[1]]
    text = build(run_with(good("local-qwen", "full"), capped))
    assert "highest confidence `no-verify` delivered is 0.55" in text and "by construction" in text
    confident = good("local-qwen", "no-verify", n_wrong=3)  # confidence 0.95: no such caveat
    assert "by construction" not in build(run_with(good("local-qwen", "full"), confident))


# --------------------------------------------------------------------------- where profiles fail together
def test_wrong_answers_two_profiles_share_are_listed_and_identical_result_sets_are_counted():
    a, b = ANSWER[0], ANSWER[1]
    qwen = [rec(a, "silently_wrong", rows=((240,),)), rec(b, "silently_wrong", rows=((2,),)),
            rec(ANSWER[2], "silently_wrong", rows=((9,),)), rec(ANSWER[3], "correct")]  # fmt: skip
    omni = [rec(a, "silently_wrong", rows=((240,),)), rec(b, "silently_wrong", rows=((3,),)),
            rec(ANSWER[2], "correct"), rec(ANSWER[3], "silently_wrong")]  # fmt: skip
    run = run_with((("local-qwen", "full"), qwen), (("omniroute-free", "full"), omni))
    row = [ln for ln in report.agreement_section(run) if ln.startswith("| local-qwen / ")]
    assert row == [f"| local-qwen / omniroute-free | 2 | {a}, {b} | 1: {a} |"]


def test_one_profile_has_nothing_to_compare():
    assert "nothing to compare" in " ".join(
        report.agreement_section(run_with(good("local-qwen", "full")))
    )


# --------------------------------------------------------------------------- the hand-written reading
def test_the_hand_written_reading_is_marked_as_interpretation_and_only_added_when_given():
    run = run_with(good("local-qwen", "full"))
    assert "## Reading the failures" not in build(run)
    text = build(run, commentary="**1. Something I saw.**")
    assert "## Reading the failures" in text and "**1. Something I saw.**" in text
    assert "interpretation and can be wrong" in text


def test_the_committed_reading_of_run_20261009_exists():
    path = report.NOTES / "20261009.md"
    assert path.exists() and path.read_text().startswith("**1.")


# --------------------------------------------------------------------------- the ablation
def test_the_ablation_reports_discordant_pairs_and_the_exact_p_without_adjectives_it_cannot_back():
    full = good("local-qwen", "full")
    no_verify = good("local-qwen", "no-verify", n_wrong=6)  # 6 cases lost: p = 2/64
    no_semantic = good("local-qwen", "no-semantic", n_wrong=1)  # 1 case lost: p = 1.0
    text = build(run_with(full, no_verify, no_semantic))
    assert (
        "| full vs no-verify | 10 | 6 / 0 | 0.0312 | distinguishable (p < 0.05); the reference handled more cases correctly |"
        in text
    )
    assert "| full vs no-semantic | 10 | 1 / 0 | 1.0000 | not distinguishable at 0.05 |" in text
    assert "the detector also loses its semantic signals" in text
    assert f"Correct only with `full` (vs `no-verify`): {', '.join(sorted(ANSWER[:6]))}" in text


def test_a_single_arm_is_not_an_ablation():
    assert "No ablation arms were run" in build(run_with(good("local-qwen", "full")))


# --------------------------------------------------------------------------- corrections
def test_corrections_list_the_cases_whose_label_or_risk_changed():
    a = [rec(ANSWER[0], "silently_wrong", risk=0.9), rec(ANSWER[1], "correct", risk=0.1),
         rec(ANSWER[2], "correct", risk=0.2)]  # fmt: skip
    b = [rec(ANSWER[0], "correct", risk=0.9), rec(ANSWER[1], "correct", risk=0.1),
         rec(ANSWER[2], "correct", risk=0.5)]  # fmt: skip
    out = corrections(a, b)
    assert len(out) == 2
    assert f"{ANSWER[0]}" in out[0] and "silently_wrong -> correct" in out[0]
    assert f"{ANSWER[2]}" in out[1] and "risk 0.20 -> 0.50" in out[1]
    assert corrections(a, a) == ["- none"]
    text = build(run_with(good("local-qwen", "full")), corrected=out)
    assert "## Corrected after the run" in text and "silently_wrong -> correct" in text


# --------------------------------------------------------------------------- charts
def test_charts_are_written_when_matplotlib_is_available(tmp_path):
    pytest.importorskip("matplotlib")
    run = run_with(good("local-qwen", "full", 1), good("local-qwen", "no-verify", 3),
                   good("local-qwen", "no-semantic", 2), good("omniroute-free", "full", 2))  # fmt: skip
    from evals.metrics import summarize

    summaries = {k: summarize(v, GOLDEN) for k, v in run.arms.items()}
    written = report.make_charts(run, summaries, tmp_path)
    assert {p.name for p in written} == {"accuracy_by_category.png", "ablation.png",
                                         "risk_distribution.png", "latency.png"}  # fmt: skip
    assert all(p.stat().st_size > 5_000 for p in written)
    text = build(run, charts=written)
    assert "## Charts" in text and "![ablation]" in text


def test_the_report_says_when_charts_were_skipped(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(report, "REPORTS", tmp_path)
    monkeypatch.setattr(report, "RESULTS", tmp_path)
    monkeypatch.setattr(
        report, "load_run", lambda run_id, results: run_with(good("local-qwen", "full"))
    )
    monkeypatch.setattr(report, "make_charts", lambda *a, **k: [])
    assert report.main(["--run-id", "t1"]) == 0
    assert "Charts were skipped" in (tmp_path / "eval-t1.md").read_text()


# --------------------------------------------------------------------------- the model comparison (ADR-014)
def test_the_model_comparison_appears_only_with_bedrock_and_reads_at_the_split_significance():
    assert "## Model comparison" not in build(run_with(good("local-qwen", "full")))
    reference = good("bedrock-claude", "full")
    weaker = good("local-qwen", "full", n_wrong=7)  # 7 cases only the reference got: p = 2/128
    equal = good("omniroute-free", "full", n_wrong=2)  # 2 cases only the reference got: p = 0.5
    text = build(run_with(reference, weaker, equal))
    section = text.split("## Model comparison")[1].split("## ")[0]
    assert (
        "2 comparisons were specified in advance" in section and "each is read at 0.025" in section
    )
    assert "different models run at different times" in section
    assert (
        "| bedrock-claude vs local-qwen | 10 | 7 / 0 | 0.0156 | distinguishable (p < 0.025); bedrock-claude handled more cases correctly |"
        in section
    )
    assert (
        "| bedrock-claude vs omniroute-free | 10 | 2 / 0 | 0.5000 | not distinguishable at 0.025 |"
        in section
    )
    assert "Correct only with `bedrock-claude` (vs `local-qwen`)" in section


def test_a_comparison_whose_profile_was_not_run_is_left_out_not_invented():
    text = build(run_with(good("bedrock-claude", "full"), good("local-qwen", "full", n_wrong=3)))
    section = text.split("## Model comparison")[1].split("## ")[0]
    assert "bedrock-claude vs local-qwen" in section and "omniroute-free" not in section


def test_the_larger_models_form_their_own_family_read_against_the_reference_model():
    reference = good("bedrock-claude", "full", n_wrong=6)  # the larger model gets 6 more right
    sonnet = good("bedrock-claude-sonnet", "full")
    opus = good("bedrock-claude-opus", "full", n_wrong=5)  # one more right than the reference
    text = build(run_with(reference, sonnet, opus))
    section = text.split("## Model comparison")[1].split("## ")[0]
    assert "**Larger Claude models.**" in section and "**Models.**" not in section, (
        "no earlier profile ran"
    )
    assert (
        "| bedrock-claude-sonnet vs bedrock-claude | 10 | 6 / 0 | 0.0312 | distinguishable (p < 0.025)"
        not in section
    ), "0.0312 is above 0.025"
    assert (
        "| bedrock-claude-sonnet vs bedrock-claude | 10 | 6 / 0 | 0.0312 | not distinguishable at 0.025 |"
        in section
    )
    assert (
        "| bedrock-claude-opus vs bedrock-claude | 10 | 1 / 0 | 1.0000 | not distinguishable at 0.025 |"
        in section
    )


def test_each_family_splits_its_own_significance_independently():
    from evals.report import COMPARISON_FAMILIES, comparison_alpha

    assert [comparison_alpha(pairs) for _, pairs in COMPARISON_FAMILIES] == [0.025, 0.025]
    assert comparison_alpha([("a", "b")]) == 0.05


def test_a_model_comparison_names_the_better_model_not_a_reference():
    """In the larger-models family the first of the pair is the larger model: calling it 'the
    reference' read as if it were the Haiku baseline."""
    reference = good("bedrock-claude", "full", n_wrong=9)
    opus = good("bedrock-claude-opus", "full")  # nine more right: p = 2/512
    text = build(run_with(reference, opus))
    section = text.split("## Model comparison")[1].split("## ")[0]
    assert (
        "distinguishable (p < 0.025); bedrock-claude-opus handled more cases correctly" in section
    )
    assert (
        "the reference handled" not in section and "Right only with 1st / only with 2nd" in section
    )


# --------------------------------------------------------------------------- stored results of a clone
def test_the_report_can_be_built_from_a_results_directory_given_on_the_command_line(
    monkeypatch, tmp_path
):
    results, out = tmp_path / "archive", tmp_path / "reports"
    results.mkdir()
    (results / "eval-t9-local-qwen-full.jsonl").write_text(
        "\n".join(rec(c, "correct").model_dump_json() for c in ANSWER[:3]) + "\n"
    )
    monkeypatch.setattr(report, "REPORTS", out)
    assert report.main(["--results", str(results), "--no-charts"]) == 0, "the run id is found there"
    text = (out / "eval-t9.md").read_text()
    assert "Evaluation report t9" in text and "local-qwen" in text


def test_a_results_directory_with_nothing_in_it_says_where_it_looked(tmp_path):
    with pytest.raises(SystemExit, match=str(tmp_path)):
        report.main(["--results", str(tmp_path)])


def test_the_archived_run_is_complete_and_has_every_profile_and_arm():
    """The README and the cost model are rebuilt from evals/archive/: it must hold the whole run."""
    archive = Path(__file__).resolve().parents[1] / "evals" / "archive" / "20261009"
    run = load_run("20261009", archive)
    assert sorted(run.arms) == [
        ("bedrock-claude", "full"), ("bedrock-claude-opus", "full"), ("bedrock-claude-sonnet", "full"),
        ("local-qwen", "full"), ("local-qwen", "no-semantic"), ("local-qwen", "no-verify"),
        ("omniroute-free", "full"),
    ]  # fmt: skip
    assert all(len(records) == 80 for records in run.arms.values())
    assert run.attacks is not None and len(run.attacks) == 45 and all(o.passed for o in run.attacks)
