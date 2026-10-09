"""Golden set integrity (evals/golden_set.yaml): structure, safety of the reference SQL and,
against the database, reproducible hashes and traps that really bite (ADR-011)."""

import re
from collections import Counter
from pathlib import Path

import pytest
import sqlglot
from sqlglot import exp

from evals.golden import (
    EXPECTED_COUNTS,
    WRITABLE,
    Behavior,
    Category,
    load_golden,
)
from evals.hashing import has_top_level_order
from scripts.golden_hash import compute, naive_bites

CASES = load_golden()
ANSWERS = [c for c in CASES if c.expected_behavior == Behavior.ANSWER]
TRAPS = [c for c in CASES if c.category == Category.DEFECT_TRAP]
TABLES = {"clientes", "productos", "faccab", "facdet"}
DEFECTS_DOC = Path(__file__).resolve().parents[1] / "docs" / "legacy-defects.md"


def catalog_ids() -> set[str]:
    return set(re.findall(r"\b(?:FAC|SIC)-\d{2}\b", DEFECTS_DOC.read_text(encoding="utf-8")))


def test_there_are_80_cases_with_the_planned_distribution():
    assert len(CASES) == 80
    assert Counter(c.category for c in CASES) == EXPECTED_COUNTS


def test_ids_and_questions_are_unique():
    assert len({c.id for c in CASES}) == len(CASES)
    assert len({c.question for c in CASES}) == len(CASES)


def test_defects_exist_in_the_catalogue_and_each_invoicing_defect_has_a_trap():
    catalogue = catalog_ids()
    used = {d for c in CASES for d in c.defects}
    assert used <= catalogue, used - catalogue
    # The golden set covers the invoicing schema; sicpt (SIC-xx) cases are a follow-up.
    assert {d for c in TRAPS for d in c.defects} == {d for d in catalogue if d.startswith("FAC")}


def test_every_answer_case_has_a_hash():
    assert all(c.expected_result_hash for c in ANSWERS)
    assert not [c.id for c in ANSWERS if c.expected_result_hash == "auto"], (
        "run: uv run python -m scripts.golden_hash --write"
    )


@pytest.mark.parametrize("case", [c for c in CASES if c.expected_sql], ids=lambda c: c.id)
def test_reference_sql_is_one_read_only_select_over_known_tables(case):
    for sql in filter(None, (case.expected_sql, case.naive_sql)):
        statements = sqlglot.parse(sql, read="postgres")
        assert len(statements) == 1
        tree = statements[0]
        assert isinstance(tree, exp.Query), case.id
        ctes = {cte.alias for cte in tree.find_all(exp.CTE)}
        assert {t.name for t in tree.find_all(exp.Table)} - ctes <= TABLES, case.id
    if case.expected_sql and sqlglot.parse_one(case.expected_sql, read="postgres").args.get(
        "limit"
    ):
        assert has_top_level_order(case.expected_sql), f"{case.id}: LIMIT needs an ORDER BY"


def test_write_cases_stay_inside_what_lb_writer_may_update():
    for case in (c for c in CASES if c.category == Category.WRITE):
        change = case.expected_change
        assert change.table in WRITABLE, case.id
        assert set(change.update) <= WRITABLE[change.table], case.id


def test_ambiguous_and_out_of_scope_cases_say_why():
    for case in CASES:
        if case.category in (Category.AMBIGUOUS, Category.OUT_OF_SCOPE):
            assert case.notes, case.id


@pytest.mark.integration
def test_stored_hashes_match_the_live_data(reader):
    live = compute(reader)
    stale = [c.id for c in ANSWERS if c.expected_result_hash != live[c.id]]
    assert not stale, f"stale hashes {stale}: run `make seed` or `golden_hash --write --refresh`"


@pytest.mark.integration
@pytest.mark.parametrize("case", TRAPS, ids=lambda c: c.id)
def test_every_trap_bites(reader, case):
    assert naive_bites(reader, case), f"{case.id}: naive_sql gives the expected result"
