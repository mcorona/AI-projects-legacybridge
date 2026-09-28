"""Integridad del golden set: formato, SQL de referencia válida y ataques bloqueados por el guard.

No evalúa al agente (eso es evals/run.py, Fase 3): garantiza que el golden set mismo es
correcto y que cada ataque adversarial con `attack_sql` lo rechaza el guard con el motivo esperado.
"""
import pytest

from evals.dataset import LEVELS, load as load_questions
from legacybridge.dictionary import load
from legacybridge.guard.sql_guard import validate

ITEMS = load_questions("all")
PREFIX = dict(zip(LEVELS, "emda"))
DEFECT_ITEMS = [q for q in ITEMS if q["level"] == "defect"]
ALLOWED = set(load().allowed_tables)
ANSWERABLE = [q for q in ITEMS if q["level"] != "adversarial"]
ATTACKS = [q for q in ITEMS if "attack_sql" in q]
MAX_GOLD_ROWS = 50   # = agent.core.MAX_ROWS_TO_MODEL


def test_splits_are_disjoint():
    dev, test = ({q["id"] for q in ITEMS if q["split"] == s} for s in ("dev", "test"))
    assert dev and test and not dev & test


def test_ids_unique_and_consistent_with_level():
    ids = [q["id"] for q in ITEMS]
    assert len(ids) == len(set(ids))
    for q in ITEMS:
        assert q["level"] in PREFIX, q["id"]
        assert q["id"].startswith(PREFIX[q["level"]]), q["id"]
        assert q["question"].strip(), q["id"]


def test_defects_reference_known_catalog():
    known = set(load().defects)
    for q in ITEMS:
        assert set(q["defects"]) <= known, q["id"]


def test_composition_matches_plan():
    """120 preguntas: 40 fáciles, 40 joins/reglas, 25 defectos, 15 adversariales (docs/PLAN.md)."""
    from collections import Counter
    from evals.dataset import TARGETS
    for split, target in TARGETS.items():
        assert Counter(q["level"] for q in ITEMS if q["split"] == split) == target, split
    assert len(ITEMS) == 120


def test_every_defect_is_covered():
    """Cada defecto D1..D10 tiene al menos una pregunta que lo ejercita."""
    covered = {d for q in ITEMS for d in q["defects"]}
    assert covered == set(load().defects), sorted(set(load().defects) - covered)


def test_adversarial_items_declare_expected_behavior():
    for q in ITEMS:
        if q["level"] == "adversarial":
            assert q.get("expect"), q["id"]
            assert "gold_sql" not in q, q["id"]
            assert ("attack_sql" in q) == ("guard_reason" in q), q["id"]
            assert "attack_sql" not in q or "attack" in q, q["id"]


@pytest.mark.parametrize("q", ANSWERABLE, ids=lambda q: q["id"])
def test_gold_sql_passes_guard(q):
    r = validate(q["gold_sql"], ALLOWED)
    assert r.ok, f"{q['id']}: {r.reason}"
    if "naive_sql" in q:
        n = validate(q["naive_sql"], ALLOWED)
        assert n.ok, f"{q['id']} naive: {n.reason}"


def test_defect_items_declare_naive_sql():
    for q in DEFECT_ITEMS:
        assert q.get("naive_sql"), f"{q['id']}: falta naive_sql"
        assert q["defects"], f"{q['id']}: sin defectos"


@pytest.mark.parametrize("q", ATTACKS, ids=lambda q: q["id"])
def test_attack_sql_is_blocked_by_guard(q):
    r = validate(q["attack_sql"], ALLOWED)
    assert not r.ok, f"{q['id']}: el guard dejó pasar {q['attack_sql']!r}"
    assert r.reason.startswith(q["guard_reason"]), f"{q['id']}: {r.reason}"


@pytest.mark.integration
@pytest.mark.parametrize("q", ANSWERABLE, ids=lambda q: q["id"])
def test_gold_sql_executes_against_seed(q):
    from legacybridge.mcp_servers.sql_readonly import ReadOnlyExecutor

    out = ReadOnlyExecutor().run(q["gold_sql"], max_rows=MAX_GOLD_ROWS + 1)
    if out.get("stage") == "connection":
        pytest.skip("Postgres legacy no disponible (make db)")
    assert out["ok"], f"{q['id']}: {out}"
    assert out["row_count"] >= 1, f"{q['id']}: sin filas con los datos generados"
    # el agente solo ve MAX_ROWS_TO_MODEL filas: una referencia más grande no es comparable
    assert out["row_count"] <= MAX_GOLD_ROWS, f"{q['id']}: {out['row_count']} filas (> {MAX_GOLD_ROWS})"


@pytest.mark.integration
@pytest.mark.parametrize("q", [q for q in ANSWERABLE if "naive_sql" in q], ids=lambda q: q["id"])
def test_naive_answer_differs_from_gold(q):
    """La pregunta discrimina: ignorar el defecto produce un resultado distinto."""
    from legacybridge.mcp_servers.sql_readonly import ReadOnlyExecutor
    from evals.compare import results_match

    db = ReadOnlyExecutor()
    gold, naive = db.run(q["gold_sql"], max_rows=200), db.run(q["naive_sql"], max_rows=200)
    if gold.get("stage") == "connection":
        pytest.skip("Postgres legacy no disponible (make db)")
    assert gold["ok"] and naive["ok"], (gold, naive)
    assert not results_match(gold["columns"], gold["rows"], naive["columns"], naive["rows"]), \
        f"{q['id']}: la respuesta ingenua coincide con la de referencia"
