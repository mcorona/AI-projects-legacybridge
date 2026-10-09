"""The six tools against the real database (Phase 2). Attacks live in test_attacks.py."""

import pytest

from evals.golden import Category, load_golden
from legacybridge import policy
from legacybridge.db import connect
from legacybridge.mcp_server.tools import LegacyTools

pytestmark = pytest.mark.integration


def test_list_tables_shows_both_systems_with_row_counts(tools):
    out = tools.list_tables()
    by_name = {t["name"]: t for t in out["tables"]}
    assert out["ok"] and len(by_name) == 9
    assert by_name["clientes"]["rows"] == 300 and by_name["clientes"]["system"] == "facturacion"
    assert by_name["invmov"]["system"] == "sicpt-synthetic"


def test_describe_table_lists_types_defects_and_writable_columns(tools):
    out = tools.describe_table("clientes")
    columns = {c["name"]: c for c in out["columns"]}
    assert columns["clave"]["type"] == "character(10)"
    assert [d["id"] for d in columns["status"]["known_defects"]] == ["FAC-03"]
    assert out["writable_columns"] == ["email", "status", "telefono"]
    assert tools.describe_table("faccab")["writable_columns"] == []


def test_describe_table_accepts_a_public_prefix_and_suggests_on_typos(tools):
    assert tools.describe_table("public.clientes")["ok"]
    out = tools.describe_table("cliente")
    assert not out["ok"] and "clientes" in out["hint"]


def test_sample_rows_returns_the_requested_rows(tools):
    out = tools.sample_rows("productos", 3)
    assert out["ok"] and out["row_count"] == 3 and out["columns"][0] == "clave"
    assert not tools.sample_rows("productos", 0)["ok"]


def test_run_query_success_shape(tools):
    out = tools.run_query("SELECT COUNT(*) AS n FROM clientes WHERE status = 'I'")
    assert out["ok"] and out["columns"] == ["n"] and out["rows"] == [[36]]
    assert out["tables"] == ["clientes"] and out["truncated"] is False
    assert out["sql"].endswith("LIMIT 101")
    assert out["data_notice"] == policy.DATA_NOTICE
    # The query handles status explicitly, so the rules have nothing to say (Phase 3).
    assert out["violations"] == [] and out["warnings"] == []
    assert "FAC-03" in out["defects_touched"]


def test_run_query_warns_when_a_defect_rule_is_broken(tools):
    out = tools.run_query("SELECT COUNT(*) AS n FROM clientes")
    assert out["ok"], "rules warn, they never block"
    assert out["rows"] == [[300]]
    assert [v["defect"] for v in out["violations"]] == ["FAC-03"]
    assert any(w.startswith("FAC-03:") and "Fix:" in w for w in out["warnings"])


def test_run_query_reports_truncation(tools):
    out = tools.run_query("SELECT clave FROM clientes", max_rows=10)
    assert out["row_count"] == 10 and out["truncated"] is True
    assert any("truncated" in w for w in out["warnings"])


def test_run_query_does_not_flag_truncation_when_everything_fits(tools):
    out = tools.run_query("SELECT estado FROM clientes GROUP BY estado", max_rows=100)
    assert out["row_count"] == 12 and out["truncated"] is False


def test_run_query_rejection_tells_the_model_how_to_fix_it(tools):
    out = tools.run_query("SELECT clicve FROM clientes")
    assert (out["ok"], out["stage"], out["reason"]) == (False, "guard", "unknown_column")
    assert "clave" in out["hint"]


def test_run_query_without_a_database_reports_a_connection_failure():
    def broken(role, **kwargs):
        raise RuntimeError("LB_READER_DSN is not set")

    out = LegacyTools(connect=broken).run_query("SELECT 1 FROM clientes")
    assert (out["ok"], out["stage"]) == (False, "connection")


def test_executor_refuses_a_session_that_is_not_lb_reader():
    """Layer 3 (ADR-004): even a validated query does not run under the wrong role."""
    as_writer = LegacyTools(connect=lambda role, **kw: connect("writer", **kw))
    as_writer._schema = {"clientes": {"clave": "text"}}
    out = as_writer.run_query("SELECT clave FROM clientes")
    assert (out["ok"], out["stage"], out["reason"]) == (False, "preflight", "unsafe_session")


def test_explain_business_term_answers_from_the_ontology(tools):
    out = tools.explain_business_term("Ventas")
    assert out["ok"] and out["term"] == "sales" and out["source"] == "ontology"
    assert {d["id"] for d in out["defects"]} >= {"FAC-09", "FAC-03"}
    assert tools.explain_business_term("factura vigente")["term"] == "invoice"


def test_explain_business_term_suggests_on_a_typo(tools):
    out = tools.explain_business_term("ventass")
    assert not out["ok"] and out["suggestions"] == ["sales"]


# --------------------------------------------------------------------------- propose_write
@pytest.fixture
def queue(admin):
    """Deletes the requests a test created (only the admin can; nobody else may DELETE)."""
    created: list[int] = []
    yield created
    if created:
        admin.execute("DELETE FROM lb_ops.write_requests WHERE id = ANY(%s)", (created,))
        admin.commit()


def _business_state(conn):
    return conn.execute(
        "SELECT md5(string_agg(c::text, '|' ORDER BY c::text)) FROM clientes c"
    ).fetchone()[0]


def test_propose_write_queues_a_pending_request_and_changes_nothing(tools, admin, queue):
    before = _business_state(admin)
    out = tools.propose_write("clientes", {"status": "I"}, {"clave": "0000000100"}, "test")
    queue.append(out["request_id"])
    assert out["ok"] and out["status"] == "pending_approval" and out["affected_rows"] == 1
    assert out["preview"] == [{"clave": "0000000100", "nombre": "Herrajes Central S de RL",
                               "status": "A"}]  # fmt: skip
    row = admin.execute(
        "SELECT status, tbl, update_values, where_values, requested_by "
        "FROM lb_ops.write_requests WHERE id = %s", (out["request_id"],)
    ).fetchone()  # fmt: skip
    assert row == ("pending", "clientes", {"status": "I"}, {"clave": "0000000100"}, "mcp")
    assert _business_state(admin) == before, "propose_write must not touch business data"


def test_propose_write_counts_every_matching_row(tools, queue):
    out = tools.propose_write("clientes", {"status": "B"}, {"estado": "GTO", "status": "I"})
    queue.append(out["request_id"])
    assert out["ok"] and out["affected_rows"] == 2 and len(out["preview"]) == 2


def test_every_golden_write_case_is_accepted_as_a_pending_request(tools, queue):
    cases = [c for c in load_golden() if c.category == Category.WRITE]
    assert len(cases) == 10
    for case in cases:
        change = case.expected_change
        out = tools.propose_write(change.table, change.update, change.where)
        assert out["ok"], (case.id, out)
        queue.append(out["request_id"])
        assert out["status"] == "pending_approval"


def test_a_request_cannot_be_approved_by_the_proposer(admin, queue):
    import psycopg

    with connect("proposer") as conn:
        request_id = conn.execute(
            "INSERT INTO lb_ops.write_requests (tbl, update_values, where_values, affected_rows) "
            "VALUES ('clientes', '{}', '{}', 0) RETURNING id"
        ).fetchone()[0]
        conn.commit()
        queue.append(request_id)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("UPDATE lb_ops.write_requests SET status = 'approved'")
