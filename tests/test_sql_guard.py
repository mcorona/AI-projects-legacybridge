"""SQL guard (ADR-004). Unit tests use the schema parsed from the DDL, so they need no database;
the tests marked ``integration`` check the guard against the real catalog and data."""

import pytest

from evals.golden import load_golden
from evals.hashing import run_and_hash
from legacybridge import policy
from legacybridge.mcp_server.schema import schema_from_db, schema_from_ddl
from legacybridge.mcp_server.sql_guard import validate

SCHEMA = schema_from_ddl()


def ok(sql, **kw):
    result = validate(sql, SCHEMA, **kw)
    assert result.ok, f"{result.reason}: {result.detail}"
    return result


def reject(sql, reason, **kw):
    result = validate(sql, SCHEMA, **kw)
    assert not result.ok, f"should be rejected as {reason}: {sql}"
    assert result.reason == reason, (result.reason, result.detail)
    return result


# --------------------------------------------------------------------------- accepted queries
@pytest.mark.parametrize(
    "sql",
    [
        "SELECT nombre FROM clientes WHERE status = 'A'",
        "SELECT COUNT(*) FROM faccab WHERE status = 'V'",
        "SELECT c.estado, COUNT(*) FROM clientes c JOIN faccab f ON f.cliente = c.clave "
        "WHERE c.status <> 'B' GROUP BY c.estado HAVING COUNT(*) > 3 ORDER BY 2 DESC",
        "WITH t AS (SELECT clave FROM clientes) SELECT * FROM t",
        "SELECT clave FROM clientes WHERE clave IN (SELECT cliente FROM faccab)",
        "SELECT c.clave FROM clientes c LEFT JOIN faccab f ON f.cliente = c.clave "
        "WHERE f.cliente IS NULL",
        "SELECT nombre FROM public.clientes",
        "SELECT DISTINCT ON (serie, numfac) serie, numfac FROM faccab ORDER BY serie, numfac, "
        "fcaptura DESC",
        "SELECT clave, ROW_NUMBER() OVER (PARTITION BY estado ORDER BY falta) FROM clientes",
        "SELECT clave FROM clientes UNION SELECT cliente FROM faccab",
        "SELECT COALESCE(NULLIF(TRIM(telefono), ''), 'n/d') FROM clientes",
        "SELECT TO_DATE(NULLIF(fecha, '00000000'), 'YYYYMMDD') FROM faccab",
        "SELECT DATE_TRUNC('month', CAST(fecha AS date)) FROM faccab WHERE fecha > '20050101'",
        "SELECT LPAD(TRIM(d.numfac), 10, '0'), REPLACE(f.total, ',', '') FROM facdet d "
        "JOIN faccab f ON f.numfac = LPAD(TRIM(d.numfac), 10, '0') AND f.serie = d.serie",
        "SELECT EXTRACT(YEAR FROM CAST(fecha AS date)) FROM faccab WHERE fecha ~ '^2005'",
        "SELECT clave FROM clientes FETCH FIRST 5 ROWS ONLY",
        "SELECT COUNT(*) FROM faccab f JOIN facdet d USING (serie, numfac)",
        "SELECT c.clave, x.n FROM clientes c JOIN LATERAL "
        "(SELECT COUNT(*) AS n FROM faccab f WHERE f.cliente = c.clave) x ON TRUE",
        "WITH usupwd AS (SELECT clave FROM clientes) SELECT * FROM usupwd",
        "SELECT NOMBRE FROM CLIENTES",
        "SELECT * FROM productos",
        "SELECT clave FROM clientes /* a comment */ -- another",
        "SELECT jsonb_build_object('c', clave, 'n', nombre) FROM clientes",
        "SELECT CASE WHEN status = 'A' THEN 1 ELSE 0 END AS activo FROM clientes ORDER BY activo",
    ],
)
def test_accepts_valid_selects(sql):
    result = ok(sql)
    assert "LIMIT" in result.sql.upper()


def test_cte_may_share_the_name_of_a_forbidden_table():
    result = ok("WITH usupwd AS (SELECT clave FROM clientes) SELECT * FROM usupwd")
    assert result.tables == ("clientes",)


# --------------------------------------------------------------------------- rejected queries
@pytest.mark.parametrize(
    "sql,reason",
    [
        ("DELETE FROM clientes", "not_select"),
        ("DROP TABLE clientes", "not_select"),
        ("UPDATE clientes SET status = 'B'", "not_select"),
        ("INSERT INTO clientes (clave) VALUES ('x')", "not_select"),
        ("TRUNCATE clientes", "not_select"),
        ("CREATE TABLE x (a int)", "not_select"),
        ("COPY clientes TO '/tmp/x'", "not_select"),
        ("SET ROLE postgres", "not_select"),
        ("SHOW all", "not_select"),
        ("EXPLAIN ANALYZE SELECT * FROM clientes", "not_select"),
        ("SELECT 1; DROP TABLE clientes", "multiple_statements"),
        ("SELECT clave FROM clientes; SELECT 1", "multiple_statements"),
        ("", "empty_sql"),
        ("   ", "empty_sql"),
        ("SELECT", "empty_select"),
        ("SELECT * FROM", "parse_error"),
        ("WITH x AS (DELETE FROM clientes RETURNING *) SELECT * FROM x", "forbidden_node"),
        (
            "WITH x AS (UPDATE clientes SET status = 'B' RETURNING clave) SELECT * FROM x",
            "forbidden_node",
        ),
        ("SELECT * INTO backup FROM clientes", "select_into"),
        ("SELECT * FROM clientes FOR UPDATE", "locking_clause"),
        ("SELECT * FROM pg_shadow", "catalog_access"),
        ("SELECT * FROM pg_catalog.pg_tables", "catalog_access"),
        ("SELECT * FROM public.pg_class", "catalog_access"),
        ("SELECT * FROM information_schema.tables", "catalog_access"),
        ("SELECT * FROM lb_ops.write_requests", "schema_not_allowed"),
        ("SELECT * FROM other_db.public.clientes", "schema_not_allowed"),
        ("SELECT * FROM usupwd", "table_not_allowed"),
        ('SELECT clave FROM "Clientes"', "table_not_allowed"),
        ("SELECT * FROM clientes WHERE clave IN (SELECT clave FROM usupwd)", "table_not_allowed"),
        ("SELECT password FROM clientes", "unknown_column"),
        ("SELECT x.clave FROM clientes", "unknown_column"),
        ("SELECT clicve FROM clientes", "unknown_column"),
        ("SELECT numfac FROM faccab f JOIN facdet d ON f.serie = d.serie", "ambiguous_column"),
        ("SELECT pg_sleep(10)", "forbidden_function"),
        ("SELECT pg_read_file('/etc/passwd')", "forbidden_function"),
        ("SELECT dblink('host=x', 'select 1')", "forbidden_function"),
        ("SELECT query_to_xml('select 1', true, true, '')", "forbidden_function"),
        ("SELECT set_config('a', 'b', false)", "forbidden_function"),
        ("SELECT current_setting('server_version')", "forbidden_function"),
        ("SELECT lo_import('/etc/passwd')", "forbidden_function"),
        ("SELECT nextval('s')", "forbidden_function"),
        ("SELECT version()", "forbidden_function"),
        ("SELECT current_user", "forbidden_function"),
        ("SELECT user", "forbidden_function"),
        ("SELECT repeat('a', 1000000000)", "forbidden_function"),
        ("SELECT some_udf(clave) FROM clientes", "function_not_allowed"),
        ("SELECT lpad(nombre, 999999999, 'x') FROM clientes", "memory_bomb"),
        ("SELECT 'x'::regclass", "forbidden_cast"),
        ("SELECT * FROM unnest(ARRAY[1, 2])", "table_function_not_allowed"),
        ("SELECT * FROM generate_series(1, 100000000)", "table_function_not_allowed"),
        ("SELECT * FROM clientes c, faccab f", "cartesian_product"),
        ("SELECT * FROM clientes c CROSS JOIN faccab f", "cartesian_product"),
        ("SELECT * FROM clientes c JOIN faccab f ON TRUE", "cartesian_product"),
        ("SELECT * FROM clientes c JOIN faccab f ON 1 = 1", "cartesian_product"),
        ("SELECT * FROM clientes NATURAL JOIN faccab", "natural_join_not_allowed"),
        ("SELECT * FROM clientes LIMIT (SELECT 5)", "non_literal_limit"),
        ("SELECT " + "1+" * 3000 + "1", "sql_too_long"),
    ],
)
def test_rejects_with_a_specific_reason(sql, reason):
    reject(sql, reason)


def test_unknown_names_come_with_a_suggestion():
    assert "clave" in reject("SELECT clicve FROM clientes", "unknown_column").hint
    assert "clientes" in reject("SELECT * FROM clientess", "table_not_allowed").hint


def test_deeply_nested_sql_fails_closed():
    result = validate("SELECT " + "(" * 400 + "1" + ")" * 400, SCHEMA)
    assert not result.ok


# --------------------------------------------------------------------------- limits
def test_missing_limit_is_added_and_flags_possible_truncation():
    result = ok("SELECT clave FROM clientes", max_rows=50)
    assert result.sql.endswith("LIMIT 51")
    assert (result.limit, result.capped) == (50, True)


def test_small_explicit_limit_is_kept():
    result = ok("SELECT clave FROM clientes LIMIT 5", max_rows=50)
    assert result.sql.endswith("LIMIT 5")
    assert (result.limit, result.capped) == (5, False)


def test_huge_limit_is_lowered_to_the_cap():
    result = ok("SELECT clave FROM clientes LIMIT 1000000000", max_rows=100)
    assert result.sql.endswith("LIMIT 101")
    assert result.capped


def test_limit_all_is_not_unbounded():
    result = ok("SELECT clave FROM clientes LIMIT ALL")
    assert "LIMIT" in result.sql.upper() and result.capped


def test_max_rows_cannot_exceed_the_hard_cap():
    result = ok("SELECT clave FROM clientes", max_rows=10_000)
    assert result.limit == policy.MAX_ROWS == 500
    assert result.sql.endswith("LIMIT 501")


def test_fetch_first_is_normalised_to_limit():
    result = ok("SELECT clave FROM clientes FETCH FIRST 7 ROWS ONLY")
    assert "FETCH" not in result.sql.upper() and result.sql.endswith("LIMIT 7")


def test_limit_applies_to_the_whole_union():
    result = ok("SELECT clave FROM clientes UNION ALL SELECT cliente FROM faccab ORDER BY 1")
    assert result.sql.endswith("ORDER BY 1 LIMIT 101")


# --------------------------------------------------------------------------- what runs
def test_regenerated_sql_has_no_comments_or_trailing_statements():
    result = ok("SELECT clave FROM clientes /* ; DROP TABLE clientes */ -- tail")
    assert "DROP" not in result.sql.upper() and "/*" not in result.sql and "--" not in result.sql


def test_reports_tables_and_columns_actually_used():
    result = ok("SELECT c.nombre FROM clientes c JOIN faccab f ON f.cliente = c.clave")
    assert result.tables == ("clientes", "faccab")
    assert result.columns == ("clientes.clave", "clientes.nombre", "faccab.cliente")


def test_the_ddl_schema_lists_the_nine_business_tables():
    assert sorted(SCHEMA) == sorted(
        ["clientes", "productos", "faccab", "facdet", "invart", "invalm", "invexi", "invprv",
         "invmov"]
    )  # fmt: skip


# --------------------------------------------------------------------------- regression corpus
GOLDEN_SQL = [
    (c.id, label, sql)
    for c in load_golden()
    for label, sql in (("expected", c.expected_sql), ("naive", c.naive_sql))
    if sql
]


@pytest.mark.parametrize("case_id,label,sql", GOLDEN_SQL, ids=lambda v: str(v)[:24])
def test_every_golden_set_query_passes_the_guard(case_id, label, sql):
    ok(sql, max_rows=policy.MAX_ROWS)


@pytest.mark.integration
def test_regenerated_sql_returns_the_same_result_as_the_golden_hash(reader):
    stale = []
    for case in load_golden():
        if case.expected_sql:
            guarded = ok(case.expected_sql, max_rows=policy.MAX_ROWS)
            if run_and_hash(reader, guarded.sql) != case.expected_result_hash:
                stale.append(case.id)
    assert not stale, f"the guard changed the meaning of {stale}"


@pytest.mark.integration
def test_ddl_schema_matches_what_lb_reader_can_read(reader):
    live = schema_from_db(reader)
    assert {t: list(c) for t, c in live.items()} == {t: list(c) for t, c in SCHEMA.items()}
