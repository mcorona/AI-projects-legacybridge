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
    # esquema public explícito
    "SELECT clinom FROM public.cliemae",
    # funciones habituales en el manejo de defectos D3/D5/D6/D7
    "SELECT pedmon, SUM(detcant*detprec) FROM pedenc e JOIN peddet d ON d.pednum=e.pednum "
    "WHERE TO_DATE(NULLIF(e.pedfec,'00000000'),'YYYYMMDD') >= DATE '2026-09-01' GROUP BY pedmon",
    "SELECT DATE_TRUNC('month', TO_DATE(pedfec,'YYYYMMDD')), COUNT(*) FROM pedenc GROUP BY 1",
    "SELECT AGE(MAKE_DATE(2026,9,27), TO_DATE(clifalta,'YYYYMMDD')) FROM cliemae",
    "SELECT artcve, SUM(detcant * CASE WHEN artuni='CJA' THEN artfac ELSE 1 END) "
    "FROM peddet JOIN artmae USING (artcve) GROUP BY artcve",
    "SELECT UPPER(TRIM(clinom)), COALESCE(cliact,'N'), LENGTH(clirfc) FROM cliemae",
    "SELECT STRING_AGG(artcve, ',') FROM artmae",
    "SELECT pednum, ROW_NUMBER() OVER (PARTITION BY clicve ORDER BY pedfec DESC) FROM pedenc",
    "SELECT jsonb_build_object('cve', clicve, 'nom', clinom) FROM cliemae",
    # subconsulta correlacionada y LATERAL
    "SELECT c.clicve, x.n FROM cliemae c, LATERAL (SELECT COUNT(*) n FROM pedenc p "
    "WHERE p.clicve=c.clicve) x",
    # un CTE puede llamarse como una tabla prohibida: referencia el CTE, no la tabla
    "WITH usupwd AS (SELECT clicve FROM cliemae) SELECT * FROM usupwd",
    "SELECT clicve FROM cliemae UNION SELECT clicve FROM pedenc",
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
    # D10 por otras rutas: subconsulta escalar, CTE que envuelve, esquema calificado
    ("SELECT (SELECT usupwd FROM usupwd)", "table_not_allowed"),
    ("WITH x AS (SELECT * FROM usupwd) SELECT * FROM x", "table_not_allowed"),
    ("WITH usupwd AS (SELECT 1) SELECT * FROM public.usupwd", "table_not_allowed"),
    # esquemas y bases distintas de public
    ("SELECT * FROM otro.cliemae", "schema_not_allowed"),
    ("SELECT * FROM legacy.public.cliemae", "schema_not_allowed"),
    ("SELECT * FROM pg_toast.x", "catalog_access"),
    # el índice RAG lo lee solo código fijo; el SQL del LLM no puede tocarlo
    ("SELECT content FROM rag.chunks", "schema_not_allowed"),
    ("SELECT * FROM pg_user", "catalog_access"),
    # SQL dentro de strings: se saltaría el AST
    ("SELECT query_to_xml('select * from usupwd', true, true, '')", "forbidden_function"),
    ("SELECT table_to_xml('usupwd', true, true, '')", "forbidden_function"),
    # efectos secundarios y administración
    ("SELECT setval('s', 1)", "forbidden_function"),
    ("SELECT nextval('s')", "forbidden_function"),
    ("SELECT pg_terminate_backend(1)", "forbidden_function"),
    ("SELECT pg_catalog.pg_sleep(1)", "forbidden_function"),
    ("SELECT dblink_exec('host=x', 'drop table t')", "forbidden_function"),
    ("SELECT lo_unlink(1)", "forbidden_function"),
    ("SELECT set_config('statement_timeout', '0', false)", "forbidden_function"),
    ("SELECT current_setting('data_directory')", "forbidden_function"),
    ("SELECT has_table_privilege('usupwd', 'select')", "forbidden_function"),
    # funciones desconocidas o definidas por el usuario: rechazo por defecto
    ("SELECT public.mi_udf(clicve) FROM cliemae", "function_not_allowed"),
    ("SELECT inet_server_addr()", "function_not_allowed"),
    # funciones de tabla en FROM
    ("SELECT * FROM generate_series(1, 1000000000)", "table_function_not_allowed"),
    # bloqueos
    ("SELECT * FROM artmae FOR UPDATE", "locking_clause"),
    ("SELECT * FROM artmae FOR SHARE", "locking_clause"),
    # DML escondido en un CTE (data-modifying CTE)
    ("WITH d AS (DELETE FROM pedenc RETURNING *) SELECT * FROM d", "forbidden_node"),
    ("WITH u AS (UPDATE artmae SET artcos=0 RETURNING *) SELECT 1", "forbidden_node"),
    # otras sentencias
    ("EXPLAIN ANALYZE SELECT * FROM artmae", "not_select"),
    ("TABLE usupwd", "not_select"),
    ("SET statement_timeout = 0", "not_select"),
    ("COPY cliemae TO '/tmp/x'", "not_select"),
    ("SELECT 1 /* ok */; SELECT * FROM usupwd", "multiple_statements"),
    # LIMIT no literal
    ("SELECT * FROM artmae LIMIT (SELECT 1000000)", "non_literal_limit"),
    ("SELECT * FROM artmae LIMIT", "parse_error"),
])
def test_blocks_attacks(sql, reason):
    r = validate(sql, ALLOWED)
    assert not r.ok
    assert r.reason.startswith(reason), r.reason


def test_caps_large_limit():
    r = validate("SELECT * FROM artmae LIMIT 100000", ALLOWED, max_limit=50)
    assert r.ok and r.sql.upper().endswith("LIMIT 50")


def test_keeps_small_limit_and_offset():
    r = validate("SELECT * FROM artmae LIMIT 5 OFFSET 10", ALLOWED)
    assert r.ok and "LIMIT 5" in r.sql.upper() and "OFFSET 10" in r.sql.upper()


@pytest.mark.parametrize("sql,expected", [
    ("SELECT * FROM artmae FETCH FIRST 1000 ROWS ONLY", "LIMIT 50"),
    ("SELECT * FROM artmae FETCH FIRST 3 ROWS ONLY", "LIMIT 3"),
    ("SELECT * FROM artmae LIMIT ALL", "LIMIT 50"),
])
def test_normalizes_fetch_and_limit_all(sql, expected):
    r = validate(sql, ALLOWED, max_limit=50)
    assert r.ok, r.reason
    assert r.sql.upper().endswith(expected) and "FETCH" not in r.sql.upper()


def test_reports_tables_used_without_ctes():
    r = validate("WITH t AS (SELECT artcve FROM artmae) SELECT * FROM t JOIN almexi x "
                 "ON x.artcve = t.artcve", ALLOWED)
    assert r.ok and r.tables == ("almexi", "artmae")


def test_allowlist_is_case_insensitive():
    assert validate("SELECT * FROM CLIEMAE", {"CliEmae"}).ok


def test_strip_think_and_extract_sql():
    raw = "<think>razonando...</think>\n```sql\nSELECT 1;\n```"
    assert extract_sql(strip_think(raw)) == "SELECT 1"
    assert strip_think("respuesta <think>sin cerrar") == "respuesta"
