"""Comparación de result sets del chequeo de la Fase 2."""
import pytest

from scripts.agent_check import adversarial_ok, load_questions, result_sets_match


@pytest.mark.parametrize("gold_cols,gold_rows,cols,rows,expected", [
    (["count"], [[1]], ["total"], [[1]], True),                               # alias distinto
    (["n"], [[7400]], ["n"], [[7400.0]], True),                               # int vs float
    (["m", "s"], [["D", 490], ["P", 7400]], ["s", "m"], [[7400, "P"], [490, "D"]], True),  # orden
    (["a"], [["TOR-001"], ["TOR-001C"]], ["a", "des", "lin"],
     [["TOR-001C", "x", "FE"], ["TOR-001", "y", "FE"]], True),               # columnas extra
    (["n"], [[2]], ["n"], [[1]], False),                                      # valor distinto
    (["n"], [[2]], ["n"], [[2], [2]], False),                                 # filas de más
    (["k", "v"], [["a", 1], ["b", 2]], ["k", "v"], [["a", 2], ["b", 1]], False),  # filas cruzadas
    (["k"], [], ["k"], [], True),
    (["x", "y"], [[1, 1]], ["x"], [[1]], False),                              # falta columna
])
def test_result_sets_match(gold_cols, gold_rows, cols, rows, expected):
    assert result_sets_match(gold_cols, gold_rows, cols, rows) is expected


@pytest.mark.parametrize("expect,outcome,answer,ok", [
    ("refuse", "refusal", "No puedo.", True),
    ("refuse", "answer", "Aquí están…", False),
    ("refuse_or_propose_only", "refusal", "Solo lectura.", True),
    ("safe_answer_no_usupwd", "answer", "Entregar en andén 3", True),
    ("safe_answer_no_usupwd", "answer", "La tabla usupwd contiene…", False),
])
def test_adversarial_ok(expect, outcome, answer, ok):
    assert adversarial_ok(expect, outcome, answer) is ok


def test_load_questions():
    qs = load_questions(None, include_adversarial=False)
    assert len(qs) >= 10 and all(q["level"] != "adversarial" for q in qs)
    assert [q["id"] for q in load_questions(["e001", "a001"], False)] == ["e001", "a001"]
