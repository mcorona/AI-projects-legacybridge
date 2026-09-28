"""run_query: serialización, rutas de rechazo y ejecución real contra Postgres (integration)."""
import datetime as dt
import os
import uuid
from decimal import Decimal

import pytest

from legacybridge.mcp_servers.sql_readonly import MAX_ROWS, ReadOnlyExecutor, name_hints, to_json_value

DSN = os.environ.get("LB_DSN", "postgresql://lb_ro:lb_ro@localhost:5433/legacy")
ADMIN_DSN = os.environ.get("LB_ADMIN_DSN", "postgresql://postgres:postgres@localhost:5433/legacy")


# ---------------------------------------------------------------- unitarias (sin BD)

@pytest.mark.parametrize("value,expected", [
    (Decimal("7400.0000"), 7400),
    (Decimal("1.10"), 1.1),
    (dt.date(2026, 9, 3), "2026-09-03"),
    (dt.datetime(2026, 9, 3, 10, 30), "2026-09-03T10:30:00"),
    (dt.timedelta(days=3), "3 days, 0:00:00"),
    (uuid.UUID(int=1), "00000000-0000-0000-0000-000000000001"),
    (b"\x00\x01", {"base64": "AAE="}),
    ([Decimal("1"), None], [1, None]),
    ({"a": Decimal("2.5")}, {"a": 2.5}),
    ("texto", "texto"),
])
def test_to_json_value(value, expected):
    assert to_json_value(value) == expected


def test_guard_rejection_never_connects():
    ex = ReadOnlyExecutor(dsn="postgresql://nadie@127.0.0.1:1/x")
    out = ex.run("SELECT * FROM usupwd")
    assert out == {"ok": False, "rejected": True, "stage": "guard",
                   "reason": "table_not_allowed: usupwd"}


def test_connection_failure_is_structured():
    out = ReadOnlyExecutor(dsn="postgresql://lb_ro:x@127.0.0.1:1/legacy").run("SELECT clinom FROM cliemae")
    assert out["ok"] is False and out["stage"] == "connection"
    assert out["error_type"] == "connection_failed" and out["message"]


# ---------------------------------------------------------------- integración (Postgres)

@pytest.fixture(scope="module")
def ex():
    psycopg = pytest.importorskip("psycopg")
    try:
        psycopg.connect(DSN, connect_timeout=2).close()
    except psycopg.OperationalError:
        pytest.skip("Postgres legacy no disponible (make db)")
    return ReadOnlyExecutor(dsn=DSN)



@pytest.mark.integration
def test_golden_e001_active_customers(ex):
    """Restringida a las filas ancla de 02_seed.sql: independiente del volumen generado."""
    out = ex.run("SELECT COUNT(*) FROM cliemae WHERE cliact = 'S' AND clicve IN ('C00001','C00002','C00003')")
    assert out["ok"] and out["rows"] == [[1]] and out["tables"] == ["cliemae"]
    assert out["sql"].upper().endswith("LIMIT 100") and out["truncated"] is False


@pytest.mark.integration
def test_golden_m001_amount_by_currency(ex):
    out = ex.run("SELECT e.pedmon, SUM(d.detcant*d.detprec) FROM pedenc e JOIN peddet d "
                 "ON d.pednum=e.pednum WHERE e.pednum IN (501,1001,1002,1003) "
                 "AND e.pedest IN ('A','C') AND TO_DATE(e.pedfec,'YYYYMMDD') "
                 "BETWEEN DATE '2026-09-01' AND DATE '2026-09-30' GROUP BY e.pedmon ORDER BY 1")
    assert out["ok"], out
    assert out["rows"] == [["D", 490], ["P", 7400]]
    assert out["tables"] == ["peddet", "pedenc"]


@pytest.mark.integration
def test_golden_d001_orphans(ex):
    out = ex.run("SELECT x.almcve, x.artcve, x.exicant FROM almexi x LEFT JOIN artmae a "
                 "ON a.artcve=x.artcve WHERE a.artcve IS NULL AND x.artcve = 'XXX-999'")
    assert out["ok"] and out["columns"] == ["almcve", "artcve", "exicant"]
    assert out["rows"] == [["02", "XXX-999", 5]]


@pytest.mark.integration
def test_free_text_is_returned_as_data(ex):
    """D9: la inyección en detobs se devuelve como dato; neutralizarla es tarea de la Fase 4."""
    out = ex.run("SELECT detobs FROM peddet WHERE pednum = 1002")
    assert out["ok"] and "IGNORA LAS INSTRUCCIONES" in out["rows"][0][0]


@pytest.mark.integration
def test_truncation_flag(ex):
    out = ex.run("SELECT artcve FROM artmae ORDER BY artcve", max_rows=2)
    assert out["row_count"] == 2 and out["truncated"] is True
    assert out["sql"].upper().endswith("LIMIT 2")


@pytest.mark.integration
def test_max_rows_is_clamped(ex):
    out = ex.run("SELECT artcve FROM artmae", max_rows=10_000)
    assert out["sql"].upper().endswith(f"LIMIT {MAX_ROWS}")


@pytest.mark.integration
@pytest.mark.parametrize("sql,error_type", [
    ("SELECT nocolumna FROM cliemae", "undefined_object"),
    ("SELECT TO_DATE('abc', 'YYYYMMDD') FROM cliemae", "invalid_query"),
    ("SELECT 1/0 FROM cliemae", "invalid_query"),
])
def test_database_errors_are_structured_for_self_correction(ex, sql, error_type):
    out = ex.run(sql)
    assert out["ok"] is False and out["rejected"] is False and out["stage"] == "execution"
    assert out["error_type"] == error_type and out["sqlstate"] and out["message"]
    assert out["sql"]   # la SQL normalizada que falló, para el reintento


@pytest.mark.integration
def test_statement_timeout(ex):
    """Un producto cartesiano pasa el guard; el timeout de la transacción lo corta."""
    fast = ReadOnlyExecutor(dsn=DSN, timeout_ms=200)
    cross = ", ".join(f"peddet p{i}" for i in range(14))   # 4^14 ≈ 2.7e8 filas
    out = fast.run(f"SELECT COUNT(*) FROM {cross}")
    assert out["stage"] == "execution" and out["error_type"] == "timeout", out
    assert out["sqlstate"] == "57014"


@pytest.mark.integration
def test_db_grants_block_sensitive_table_even_without_guard(ex):
    """Segunda barrera (D10): aun si el guard fallara, lb_ro no puede leer usupwd."""
    out = ex._execute("SELECT * FROM usupwd", 10)
    assert out["ok"] is False and out["error_type"] == "permission_denied"


@pytest.mark.integration
def test_preflight_refuses_privileged_session(ex):
    """Si el DSN apunta a un rol distinto de lb_ro (p. ej. superusuario), no se ejecuta nada."""
    out = ReadOnlyExecutor(dsn=ADMIN_DSN).run("SELECT clinom FROM cliemae")
    if out["stage"] == "connection":
        pytest.skip("DSN de administrador no disponible")
    assert out["ok"] is False and out["stage"] == "preflight"
    assert out["error_type"] == "unsafe_session" and "user=postgres" in out["message"]



# ---------------------------------------------------------------- sugerencias de nombres (Fase 5)

@pytest.mark.parametrize("message,tables,expected", [
    ('column "ciedo" does not exist', ["cliemae"], ["cliemae.cliedo"]),
    ("column c.cledio does not exist", ["cliemae", "pedenc"], ["cliemae.cliedo"]),
    ("column cliemae.cleda does not exist", ["cliemae"], ["cliemae.cliedo"]),
    ('column "ardes" does not exist', ["artmae", "peddet"], ["artmae.artdes"]),
    ('relation "pedencs" does not exist', [], ["pedenc"]),
])
def test_name_hints_suggest_close_dictionary_names(message, tables, expected):
    h = name_hints(message, tables)
    assert h["suggestions"][:len(expected)] == expected and "¿Quisiste decir" in h["hint"]


def test_name_hints_other_errors():
    assert "alias" in name_hints('column reference "pednum" is ambiguous', ["pedenc", "peddet"])["hint"]
    assert "ON" in name_hints('column "artcve" specified in USING clause does not exist in left table', [])["hint"]
    assert name_hints('column "zzzzzz" does not exist', ["cliemae"]) == {"hint": "La columna 'zzzzzz' no existe; revisa describe_table."}
    assert name_hints("division by zero", ["cliemae"]) == {}


@pytest.mark.integration
def test_run_query_returns_suggestions(ex):
    out = ex.run("SELECT c.ciedo, COUNT(*) FROM cliemae c GROUP BY 1")
    assert out["error_type"] == "undefined_object" and out["suggestions"] == ["cliemae.cliedo"]
