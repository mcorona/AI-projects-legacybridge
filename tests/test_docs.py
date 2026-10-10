"""The documentation is checked like code (Phase 8): links resolve, what it cites exists, what it
counts is what the code counts, and the numbers in the prose are the numbers in the data."""

import re
from pathlib import Path

import pytest
import yaml

from evals.golden import load_golden
from evals.report import load_run
from scripts import check_quickstart, readme_results
from scripts.readme_results import START

ROOT = Path(__file__).resolve().parents[1]
DOCS = (
    [ROOT / "README.md", ROOT / "CHANGELOG.md", ROOT / "CLAUDE.md"]
    + sorted((ROOT / "docs").rglob("*.md"))
    + sorted((ROOT / "evals" / "notes").glob("*.md"))
    + [ROOT / "evals" / "archive" / "20261009" / "README.md"]
)
README = (ROOT / "README.md").read_text(encoding="utf-8")
THREAT = (ROOT / "docs" / "threat-model.md").read_text(encoding="utf-8")
ADRS = sorted((ROOT / "docs" / "adr").glob("ADR-*.md"))
ATTACKS = yaml.safe_load((ROOT / "evals" / "attacks.yaml").read_text(encoding="utf-8"))
ATTACKS = ATTACKS["attacks"] if isinstance(ATTACKS, dict) else ATTACKS


def rel(path: Path) -> str:
    return str(path.relative_to(ROOT))


# --------------------------------------------------------------------------- links and structure
LINK = re.compile(r"\]\(([^)\s]+)\)|src=\"([^\"]+)\"")


@pytest.mark.parametrize("doc", DOCS, ids=rel)
def test_every_relative_link_resolves(doc):
    missing = []
    for match in LINK.finditer(doc.read_text(encoding="utf-8")):
        target = (match.group(1) or match.group(2)).split("#")[0]
        if target and not target.startswith(("http://", "https://", "mailto:")):
            if not (doc.parent / target).resolve().exists():
                missing.append(target)
    assert missing == []


def test_the_adr_index_lists_every_decision_and_only_those():
    index = (ROOT / "docs" / "adr" / "README.md").read_text(encoding="utf-8")
    listed = set(re.findall(r"\]\((ADR-[^)]+\.md)\)", index))
    assert listed == {p.name for p in ADRS}
    numbers = sorted(int(p.name[4:7]) for p in ADRS)
    assert numbers == list(range(1, len(numbers) + 1)), "no gap in the numbering"
    assert f"The {len(ADRS)} decisions" in README


@pytest.mark.parametrize("doc", [d for d in DOCS if d.name != "CHANGELOG.md"], ids=rel)
def test_every_mermaid_diagram_has_a_known_type_and_balanced_blocks(doc):
    for block in re.findall(r"```mermaid\n(.*?)```", doc.read_text(encoding="utf-8"), re.S):
        lines = [line.strip() for line in block.strip().splitlines()]
        assert lines[0].split()[0] in {"flowchart", "graph", "sequenceDiagram", "stateDiagram-v2"}
        assert sum(line.startswith("subgraph") for line in lines) == lines.count("end")
        assert block.count("[") == block.count("]") and block.count("(") == block.count(")")


# --------------------------------------------------------------------------- the quickstart
def test_the_quickstart_is_five_lines_and_every_make_target_in_it_exists():
    block = check_quickstart.BLOCK.search(README).group(1).strip().splitlines()
    assert len(block) == 5, "the heading promises five commands"
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    for command in check_quickstart.commands(README):
        target = command.split()[1]
        assert re.search(rf"^{target}:", makefile, re.M), command


def test_the_readme_has_its_demo_and_stays_short():
    assert (ROOT / "docs" / "demo" / "demo.gif").stat().st_size > 50_000
    assert (ROOT / "docs" / "demo" / "demo.tape").exists()
    assert len(README.splitlines()) <= 170


# --------------------------------------------------------------------------- generated parts are current
def test_the_results_block_of_the_readme_is_what_the_archive_produces():
    assert START in README
    assert readme_results.current_block(README) == readme_results.build_block()


# --------------------------------------------------------------------------- the threat model
CITED = re.compile(
    r"`((?:tests|infra/tests|src|db|config|evals|docs|scripts|\.github)/[^`\s:]+)(?:::(\w+))?`"
)


def test_everything_the_threat_model_cites_exists():
    missing = []
    for path, test in CITED.findall(THREAT):
        target = ROOT / path
        if not target.exists():
            missing.append(path)
        elif test and f"def {test}(" not in target.read_text(encoding="utf-8"):
            missing.append(f"{path}::{test}")
    assert missing == []
    assert len(CITED.findall(THREAT)) >= 25, "it cites its evidence, not just claims"


def test_the_threat_model_lists_every_attack_category_with_its_real_count():
    counts = {}
    for attack in ATTACKS:
        counts[attack["category"]] = counts.get(attack["category"], 0) + 1
    rows = dict(re.findall(r"^\| `(\w+)` \| (\d+) \|", THREAT, re.M))
    assert {k: int(v) for k, v in rows.items()} == counts
    assert sum(counts.values()) == 45 and len(counts) == 26
    assert "The 45 attacks" in THREAT and "All 45 passed" in THREAT


def test_the_threat_model_covers_every_owasp_llm_risk_and_every_stride_boundary():
    for n in range(1, 11):
        assert f"| LLM{n:02d} |" in THREAT, f"LLM{n:02d}"
    assert len(re.findall(r"^### \d\. ", THREAT, re.M)) == 7


# --------------------------------------------------------------------------- what the docs count
def test_the_counts_the_docs_state_are_the_codes():
    from legacybridge.semantic.loader import load_ontology

    assert len(load_golden()) == 80 and len(ATTACKS) == 45
    assert len(load_ontology().defects) == 28
    assert "28 defect rules" in (ROOT / "docs" / "architecture" / "c2-containers.md").read_text()
    assert "28 planted defects" in README and "80 questions and 45 attacks" in README


# --------------------------------------------------------------------------- the prose against the data
@pytest.fixture(scope="module")
def run():
    return load_run("20261009", ROOT / "evals" / "archive" / "20261009")


def test_the_numbers_quoted_in_the_prose_are_the_numbers_in_the_data(run):
    from evals.metrics import compare_arms

    opus = compare_arms(run.records("bedrock-claude-opus"), run.records("bedrock-claude"))
    sonnet = compare_arms(run.records("bedrock-claude-sonnet"), run.records("bedrock-claude"))
    assert (opus["only_reference"], opus["only_other"]) == (
        15,
        2,
    ) and "15 cases against 2" in README
    assert round(sonnet["p_value"], 3) == 0.049 and "p = 0.049" in README
    assert sonnet["p_value"] >= 0.025, (
        "so 'not distinguishable' is what the preregistered level says"
    )


def test_the_guardrail_and_knowledge_base_claims_match_their_reports():
    guardrails = (ROOT / "evals" / "reports" / "guardrails-20261009.md").read_text()
    assert "1.00 (18/18" in guardrails and "18 of 18" in README
    kb = (ROOT / "evals" / "reports" / "kb-20261009.md").read_text()
    assert "hit@3: 1.00 (20/20" in kb and "would hit 20 of 20" in kb and "20 of 20" in README


def test_the_cost_claim_in_the_readme_matches_the_model():
    from legacybridge.observability.emf import UNITS, series_per_profile

    first = len(UNITS) * 5  # every metric by profile and by each of the four statuses, and alone
    assert first / series_per_profile(4) == pytest.approx(3.67, abs=0.01), "'nearly four times'"
    assert "nearly\n  four times" in README


def test_the_mix_of_questions_the_readme_describes_is_the_golden_sets():
    import collections

    mix = collections.Counter(c.category.value for c in load_golden())
    assert dict(mix) == {
        "simple": 15, "join_agg": 20, "defect_trap": 20, "ambiguous": 10, "out_of_scope": 5, "write": 10,
    }  # fmt: skip
    flat = " ".join(README.split())
    assert (
        "15 simple, 20 joins and aggregates, 20 traps, 10 ambiguous, 5 out of scope and 10 writes"
        in flat
    )


def test_every_timing_in_the_results_table_is_explained_in_the_measurement_environment():
    doc = (ROOT / "docs" / "measurement-environment.md").read_text(encoding="utf-8")
    block = README.split("<!-- results:start -->")[1].split("<!-- results:end -->")[0]
    table = [ln for ln in block.splitlines() if ln.startswith("| `")][:5]
    latencies = [row.strip("| ").split("|")[-1].strip() for row in table]
    assert len(latencies) == 5 and all(x.endswith(" s") for x in latencies), latencies
    for value in latencies:
        assert value in doc, (
            f"{value} is in the README table but not in docs/measurement-environment.md"
        )
    assert "Apple M5 Max" in doc and "Apple M5 Max" in README
