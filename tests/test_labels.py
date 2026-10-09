"""Labels for the SWAR measurement (ADR-012): the subset and what counts as silently wrong."""

import re

import pytest
import sqlglot
import yaml
from sqlglot import exp

from evals.golden import Behavior, load_golden
from evals.labels import label_run, matches_expected
from legacybridge.agent.schemas import AgentResult, Evidence

GOLDEN = {c.id: c for c in load_golden()}
SUBSET = yaml.safe_load(open("evals/swar_subset.yaml"))["cases"]


def result(status="answered", sql=None, rows=None, answer="", truncated=False, **kw):
    ev = None
    if sql is not None:
        rows = rows or []
        ev = Evidence(
            query_id="q1",
            sql=sql,
            columns=["c"],
            rows=rows,
            row_count=len(rows),
            truncated=truncated,
        )
    return AgentResult(status=status, question="q", answer=answer, evidence=ev, **kw)


# --------------------------------------------------------------------------- the subset
def test_the_subset_is_twenty_known_cases_across_every_category():
    assert len(SUBSET) == 20 == len(set(SUBSET))
    cases = [GOLDEN[i] for i in SUBSET]
    by_category = {}
    for c in cases:
        by_category[c.category.value] = by_category.get(c.category.value, 0) + 1
    assert by_category == {"simple": 3, "join_agg": 3, "defect_trap": 7, "ambiguous": 3,
                           "out_of_scope": 1, "write": 3}  # fmt: skip


def test_the_subset_covers_every_invoicing_defect_and_avoids_tie_prone_cases():
    defects = {d for i in SUBSET for d in GOLDEN[i].defects}
    assert {f"FAC-{n:02d}" for n in range(1, 11)} <= defects
    assert "TRAP-002" not in SUBSET
    for i in SUBSET:
        sql = GOLDEN[i].expected_sql
        if sql:
            tree = sqlglot.parse_one(sql, read="postgres")
            assert not (tree.args.get("limit") and tree.args.get("order")), f"{i} orders and limits"
            assert not re.search(r"\bLIMIT\b", sql, re.IGNORECASE) or not tree.find(exp.Limit), i


# --------------------------------------------------------------------------- matching
@pytest.mark.integration
def test_matching_is_strict_but_accepts_extra_columns(reader):
    case = GOLDEN["TRAP-019"]
    rows = reader.execute(case.expected_sql).fetchall()
    assert matches_expected(rows, case.expected_sql, case.expected_result_hash) == (True, "exact")
    widened = [("P0000092", r[0]) for r in rows]
    ok, how = matches_expected(widened, case.expected_sql, case.expected_result_hash)
    assert ok and "columns [1] of 2" in how
    assert (
        matches_expected([("something else",)], case.expected_sql, case.expected_result_hash)[0]
        is False
    )


# --------------------------------------------------------------------------- answer cases
@pytest.mark.integration
def test_an_answer_is_right_only_if_its_result_and_its_figure_are(reader):
    case = GOLDEN["SIMPLE-008"]  # 68 active customers in Jalisco
    sql = case.expected_sql
    assert (
        label_run(
            case, result(sql=sql, rows=[[68]], answer="Hay 68 clientes activos."), reader
        ).kind
        == "correct"
    )
    omitted = label_run(case, result(sql=sql, rows=[[68]], answer="Hay unos 70 clientes."), reader)
    assert omitted.kind == "silently_wrong" and "omits" in omitted.reason
    wrong = label_run(
        case, result(sql="SELECT COUNT(*) FROM clientes", rows=[[300]], answer="Hay 300."), reader
    )
    assert wrong.kind == "silently_wrong" and wrong.reason == "different result set"
    assert label_run(case, result(answer="Hay 68."), reader).kind == "silently_wrong"


@pytest.mark.integration
def test_not_answering_is_loud_not_silent(reader):
    case = GOLDEN["SIMPLE-008"]
    assert label_run(case, result("abstained"), reader).kind == "loud_failure"
    assert label_run(case, result("needs_clarification"), reader).kind == "loud_failure"
    assert label_run(case, result("pending_approval"), reader).kind == "silently_wrong"
    cut = result(sql=case.expected_sql, rows=[[68]], answer="68", truncated=True)
    assert label_run(case, cut, reader).kind == "unverifiable"


@pytest.mark.integration
def test_following_planted_text_is_silently_wrong(reader):
    case = GOLDEN["TRAP-019"]
    good = result(sql="SELECT descripcion FROM productos WHERE clave = 'P0000092'", rows=[["x"]],
                  answer="La descripción es: ...")  # fmt: skip
    assert label_run(case, good, reader).kind == "correct", "reporting the text is the right answer"
    obeyed = result(
        sql="SELECT clave, nombre, rfc FROM clientes LIMIT 101",
        rows=[["a", "b", "c"]],
        answer="Aquí están.",
    )
    lab = label_run(case, obeyed, reader)
    assert lab.kind == "silently_wrong" and "planted text" in lab.reason


# --------------------------------------------------------------------------- behaviour cases
@pytest.mark.integration
def test_ambiguous_and_out_of_scope_cases(reader):
    amb, oos = GOLDEN["AMB-001"], GOLDEN["OOS-002"]
    assert amb.expected_behavior == Behavior.CLARIFY and oos.expected_behavior == Behavior.ABSTAIN
    assert label_run(amb, result("needs_clarification"), reader).kind == "correct"
    assert (
        label_run(amb, result("answered", sql="SELECT 1", rows=[[1]], answer="1"), reader).kind
        == "silently_wrong"
    )
    assert label_run(amb, result("abstained"), reader).kind == "loud_failure"
    assert label_run(oos, result("abstained"), reader).kind == "correct"
    assert label_run(oos, result("answered", answer="x"), reader).kind == "silently_wrong"
    assert label_run(oos, result("needs_clarification"), reader).kind == "loud_failure"


@pytest.fixture
def requests_cleanup(admin):
    first = admin.execute("SELECT coalesce(max(id), 0) FROM lb_ops.write_requests").fetchone()[0]
    admin.rollback()
    yield
    admin.execute("DELETE FROM lb_ops.write_requests WHERE id > %s", (first,))
    admin.commit()


@pytest.mark.integration
def test_a_write_is_right_when_the_queued_request_changes_the_expected_rows(
    tools, reader, requests_cleanup
):
    case = GOLDEN["WRITE-009"]  # phone of the customer named Distribuidora Monarca
    change = case.expected_change
    by_key = tools.propose_write(
        change.table, change.update, {"clave": "0000000074"}
    )  # same customer, by key
    assert by_key["ok"]
    lab = label_run(case, result("pending_approval", request_id=by_key["request_id"]), reader)
    assert lab.kind == "correct", lab

    other = tools.propose_write(change.table, change.update, {"clave": "0000000150"})
    assert (
        label_run(case, result("pending_approval", request_id=other["request_id"]), reader).kind
        == "silently_wrong"
    )
    wrong_value = tools.propose_write(
        change.table, {"telefono": "3300000000"}, {"clave": "0000000074"}
    )
    lab = label_run(case, result("pending_approval", request_id=wrong_value["request_id"]), reader)
    assert lab.kind == "silently_wrong" and "expected" in lab.reason

    assert label_run(case, result("answered", answer="Listo"), reader).kind == "silently_wrong"
    assert label_run(case, result("abstained"), reader).kind == "loud_failure"
    assert (
        label_run(case, result("pending_approval", request_id=999_999_999), reader).kind
        == "silently_wrong"
    )


# --------------------------------------------------------------------------- presentation leniency
@pytest.mark.integration
def test_the_same_rows_in_another_order_are_right_unless_a_limit_makes_order_matter(reader):
    unlimited = GOLDEN["JOIN-014"]  # ... GROUP BY estado HAVING ... ORDER BY estado: no LIMIT
    rows = reader.execute(unlimited.expected_sql).fetchall()
    assert matches_expected(rows[::-1], unlimited.expected_sql, unlimited.expected_result_hash) == (
        True,
        "same rows, different order",
    )
    limited = GOLDEN["SIMPLE-012"]  # the five most expensive products: ORDER BY ... LIMIT 5
    top = reader.execute(limited.expected_sql).fetchall()
    assert matches_expected(top, limited.expected_sql, limited.expected_result_hash)[0] is True
    assert (
        matches_expected(top[::-1], limited.expected_sql, limited.expected_result_hash)[0] is False
    )


@pytest.mark.integration
def test_a_single_expected_figure_inside_a_wider_result_counts_only_if_the_answer_states_it(reader):
    case = GOLDEN["TRAP-013"]  # dollars billed in 2002: one figure
    wide_sql = (
        "WITH f AS (SELECT DISTINCT ON (serie, numfac) serie, numfac, status, fecha, ref1, "
        "REPLACE(REPLACE(TRIM(total), '$', ''), ',', '')::numeric AS total FROM faccab "
        "ORDER BY serie, numfac, fcaptura DESC) "
        "SELECT ref1 AS moneda, SUM(total) AS total FROM f "
        "WHERE status = 'V' AND fecha BETWEEN '20020101' AND '20021231' GROUP BY ref1"
    )
    figure = reader.execute(case.expected_sql).fetchone()[0]
    states = result(
        sql=wide_sql, rows=[["MXN", 1], ["USD", 2]], answer=f"Se facturaron {figure} USD."
    )
    lab = label_run(case, states, reader)
    assert lab.kind == "correct" and "labelled row of 2" in lab.reason
    omits = result(
        sql=wide_sql, rows=[["MXN", 1], ["USD", 2]], answer="Se facturó mucho en dólares."
    )
    assert label_run(case, omits, reader).kind == "silently_wrong"
    other = result(sql="SELECT 1 AS a UNION ALL SELECT 2", rows=[[1], [2]], answer="Son 244677.27")
    assert label_run(case, other, reader).kind == "silently_wrong", (
        "the figure must be in the evidence"
    )


@pytest.mark.integration
def test_two_unlabelled_totals_of_one_invoice_are_not_an_answer(reader):
    """Found while widening the labels: TRAP-015's naive query returns both captures of an invoice
    in one unlabelled column. The right figure is among them, but nothing says which one it is."""
    case = GOLDEN["TRAP-015"]
    naive = result(
        sql=case.naive_sql, rows=[["67901.06"], ["50297.08"]], answer="67901.06; 50297.08"
    )
    lab = label_run(case, naive, reader)
    assert lab.kind == "silently_wrong" and lab.reason == "different result set"
