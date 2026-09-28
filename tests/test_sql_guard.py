import pytest

from legacybridge.guard.sql_guard import validate
from legacybridge.llm.router import extract_sql, strip_think

ALLOWED = {"cliemae", "artmae", "almexi", "pedenc", "peddet"}


@pytest.mark.parametrize("sql", [
    "SELECT clinom FROM cliemae WHERE cliact = 'S'",
    "SELECT e.pednum, SUM(d.detcant*d.detprec) FROM pedenc e JOIN peddet d ON d.pednum=e.pednum "
    "WHERE e.pedest IN ('A','C') GROUP BY e.pednum",
    "WITH t AS (SELECT artcve FROM artmae) SELECT * FROM t",
    "SELECT a.artcve FROM almexi a LEFT JOIN artmae m ON m.artcve=a.artcve WHERE m.artcve IS NULL",
])
def test_allows_valid_selects(sql):
    r = validate(sql, ALLOWED)
    assert r.ok, r.reason
    assert "LIMIT" in r.sql.upper()


@pytest.mark.parametrize("sql,reason", [
    ("DELETE FROM cliemae", "not_select"),
    ("DROP TABLE artmae", "not_select"),
    ("SELECT 1; DROP TABLE artmae", "multiple_statements"),
    ("SELECT * FROM usupwd", "table_not_allowed"),
    ("SELECT * FROM ctrlhis", "table_not_allowed"),
    ("SELECT * FROM pg_catalog.pg_user", "catalog_access"),
    ("SELECT * FROM information_schema.tables", "catalog_access"),
    ("SELECT pg_sleep(10)", "forbidden_function"),
    ("SELECT pg_read_file('/etc/passwd')", "forbidden_function"),
    ("SELECT * INTO copia FROM cliemae", "select_into"),
    ("SELECT * FROM cliemae WHERE clicve IN (SELECT usucve FROM usupwd)", "table_not_allowed"),
])
def test_blocks_attacks(sql, reason):
    r = validate(sql, ALLOWED)
    assert not r.ok
    assert r.reason.startswith(reason), r.reason


def test_caps_large_limit():
    r = validate("SELECT * FROM artmae LIMIT 100000", ALLOWED, max_limit=50)
    assert r.ok and r.sql.upper().endswith("LIMIT 50")


def test_strip_think_and_extract_sql():
    raw = "<think>razonando...</think>\n```sql\nSELECT 1;\n```"
    assert extract_sql(strip_think(raw)) == "SELECT 1"
    assert strip_think("respuesta <think>sin cerrar") == "respuesta"
