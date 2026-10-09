"""The report generator (ADR-013): it reads stored results and writes figures it can account for."""

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
    assert "`review: draft`" in text and "not measure" in text.lower()
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
    monkeypatch.setattr(report, "load_run", lambda run_id: run_with(good("local-qwen", "full")))
    monkeypatch.setattr(report, "make_charts", lambda *a, **k: [])
    assert report.main(["--run-id", "t1"]) == 0
    assert "Charts were skipped" in (tmp_path / "eval-t1.md").read_text()
