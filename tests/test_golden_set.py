"""Integridad del golden set: formato, SQL de referencia válida y ataques bloqueados por el guard.

No evalúa al agente (eso es evals/run.py, Fase 3): garantiza que el golden set mismo es
correcto y que cada ataque adversarial con `attack_sql` lo rechaza el guard con el motivo esperado.
"""
import json
from pathlib import Path

import pytest

from legacybridge.dictionary import load
from legacybridge.guard.sql_guard import validate

GOLDEN = Path(__file__).resolve().parents[1] / "evals" / "questions" / "golden_v1.jsonl"
ITEMS = [json.loads(line) for line in GOLDEN.read_text(encoding="utf-8").splitlines() if line.strip()]
PREFIX = {"easy": "e", "medium": "m", "defect": "d", "adversarial": "a"}
ALLOWED = set(load().allowed_tables)
ANSWERABLE = [q for q in ITEMS if q["level"] != "adversarial"]
ATTACKS = [q for q in ITEMS if "attack_sql" in q]


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


def test_every_defect_is_covered():
    """Cada defecto D1..D10 tiene al menos una pregunta que lo ejercita."""
    covered = {d for q in ITEMS for d in q["defects"]}
    assert covered == set(load().defects), sorted(set(load().defects) - covered)


def test_adversarial_items_declare_expected_behavior():
    for q in ITEMS:
        if q["level"] == "adversarial":
            assert q.get("expect"), q["id"]
            assert "gold_sql" not in q, q["id"]
            assert ("attack_sql" in q) == ("guard_reason" in q) == ("attack" in q), q["id"]


@pytest.mark.parametrize("q", ANSWERABLE, ids=lambda q: q["id"])
def test_gold_sql_passes_guard(q):
    r = validate(q["gold_sql"], ALLOWED)
    assert r.ok, f"{q['id']}: {r.reason}"


@pytest.mark.parametrize("q", ATTACKS, ids=lambda q: q["id"])
def test_attack_sql_is_blocked_by_guard(q):
    r = validate(q["attack_sql"], ALLOWED)
    assert not r.ok, f"{q['id']}: el guard dejó pasar {q['attack_sql']!r}"
    assert r.reason.startswith(q["guard_reason"]), f"{q['id']}: {r.reason}"


@pytest.mark.integration
@pytest.mark.parametrize("q", ANSWERABLE, ids=lambda q: q["id"])
def test_gold_sql_executes_against_seed(q):
    from legacybridge.mcp_servers.sql_readonly import ReadOnlyExecutor

    out = ReadOnlyExecutor().run(q["gold_sql"])
    if out.get("stage") == "connection":
        pytest.skip("Postgres legacy no disponible (make db)")
    assert out["ok"], f"{q['id']}: {out}"
    assert out["row_count"] >= 1, f"{q['id']}: sin filas con los datos semilla"
