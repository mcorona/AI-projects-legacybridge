"""Validador de propuestas de escritura (el sistema nunca las ejecuta)."""
import pytest

from legacybridge.guard.sql_guard import validate
from legacybridge.guard.write_guard import validate_write

ALLOWED = {"cliemae", "artmae", "almexi", "pedenc", "peddet"}


@pytest.mark.parametrize("sql,kind,table", [
    ("DELETE FROM pedenc WHERE pedest = 'X'", "DELETE", "pedenc"),
    ("UPDATE artmae SET artcos = 0 WHERE artbaja = 'S'", "UPDATE", "artmae"),
    ("UPDATE cliemae SET cliact = 'N' WHERE clicve IN (SELECT clicve FROM pedenc WHERE pedest = 'Z')",
     "UPDATE", "cliemae"),
    ("INSERT INTO almexi (almcve, artcve, exicant, exiult) VALUES ('03', 'TOR-001', 10, '20260927')",
     "INSERT", "almexi"),
    ("UPDATE pedenc SET pedfec = TO_CHAR(TO_DATE(pedfec, 'YYYYMMDD'), 'YYYYMMDD') WHERE pednum = 2001",
     "UPDATE", "pedenc"),
])
def test_accepts_scoped_writes(sql, kind, table):
    w = validate_write(sql, ALLOWED)
    assert w.ok, w.reason
    assert (w.kind, w.table) == (kind, table)


def test_impact_query_is_a_valid_read_only_count():
    w = validate_write("DELETE FROM pedenc WHERE pedest = 'X' AND pednum > 2000", ALLOWED)
    assert w.impact_sql.upper().startswith("SELECT COUNT(*) FROM PEDENC WHERE")
    assert validate(w.impact_sql, ALLOWED).ok
    assert validate_write("INSERT INTO almexi VALUES ('03','X',1,'')", ALLOWED).impact_sql is None


@pytest.mark.parametrize("sql,reason", [
    ("DELETE FROM pedenc", "missing_where"),
    ("UPDATE artmae SET artcos = 0", "missing_where"),
    ("DELETE FROM usupwd WHERE usucve = 'admin'", "table_not_allowed"),
    ("UPDATE ctrlhis SET pedest = 'C' WHERE 1 = 1", "table_not_allowed"),
    ("DELETE FROM otro.pedenc WHERE pednum = 1", "schema_not_allowed"),
    ("DROP TABLE pedenc", "not_a_write"),
    ("TRUNCATE pedenc", "not_a_write"),
    ("SELECT * FROM pedenc", "not_a_write"),
    ("DELETE FROM pedenc WHERE pednum = 1; DROP TABLE artmae", "multiple_statements"),
    ("WITH x AS (SELECT 1) DELETE FROM pedenc WHERE pednum IN (SELECT 1 FROM x)", "cte_not_allowed"),
    ("UPDATE cliemae SET clinom = 'x' WHERE clicve IN (SELECT usucve FROM usupwd)", "table_not_allowed"),
    ("UPDATE cliemae SET clinom = pg_read_file('/etc/passwd') WHERE clicve = 'C00001'", "forbidden_function"),
    ("DELETE FROM pedenc WHERE pednum = (SELECT setval('s', 1))", "forbidden_function"),
    ("INSERT INTO pedenc SELECT * FROM ctrlhis", "table_not_allowed"),
])
def test_rejects_unsafe_writes(sql, reason):
    w = validate_write(sql, ALLOWED)
    assert not w.ok and w.reason.startswith(reason), w.reason
