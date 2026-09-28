"""Validador de SQL por AST: solo SELECT, allowlist de tablas, sin catálogos, LIMIT forzado.

Política de funciones (ver ADR-003):
- Funciones que sqlglot tipa (SUM, COALESCE, TO_DATE…): permitidas salvo denylist.
- Funciones que sqlglot no conoce (`exp.Anonymous`): solo si están en `ALLOWED_ANON_FUNCS`.
  Así una función peligrosa nueva o desconocida (`query_to_xml`, `setval`, UDFs) se rechaza
  por defecto en vez de depender de que alguien la agregue a la denylist.
- Prefijos de administración (`pg_`, `lo_`, `dblink`) siempre prohibidos.

El guard es la primera barrera; el rol `lb_ro` (sin grants fuera de la allowlist,
transacción read-only, statement_timeout) es la segunda.
"""
from __future__ import annotations

from dataclasses import dataclass

import sqlglot
from sqlglot import exp

FORBIDDEN = (exp.Insert, exp.Update, exp.Delete, exp.Drop, exp.Create, exp.Alter,
             exp.Merge, exp.Command, exp.Grant, exp.TruncateTable, exp.Copy)
FORBIDDEN_SCHEMAS = {"pg_catalog", "information_schema", "pg_toast"}
ALLOWED_SCHEMAS = {"", "public"}
FORBIDDEN_FUNC_PREFIXES = ("pg_", "lo_", "dblink")
FORBIDDEN_FUNCS = {
    # E/S de archivos, sesión y configuración
    "pg_read_file", "pg_ls_dir", "pg_sleep", "pg_read_binary_file", "lo_import", "lo_export",
    "dblink", "set_config", "current_setting",
    # efectos secundarios en secuencias / transacciones
    "setval", "nextval", "currval", "lastval", "txid_current",
    # SQL dentro de strings: ejecutan una consulta arbitraria saltándose el AST
    "query_to_xml", "query_to_xml_and_xmlschema", "query_to_xmlschema",
    "table_to_xml", "table_to_xml_and_xmlschema", "table_to_xmlschema",
    "cursor_to_xml", "cursor_to_xmlschema", "schema_to_xml", "database_to_xml",
    # introspección de privilegios / catálogo por nombre
    "to_regclass", "has_table_privilege", "has_column_privilege", "has_schema_privilege",
}
# Funciones de Postgres que sqlglot no tipa y que son legítimas en consultas analíticas.
ALLOWED_ANON_FUNCS = {
    # fechas
    "age", "make_date", "make_time", "make_timestamp", "make_interval", "date_bin",
    "justify_days", "justify_hours", "justify_interval", "isfinite", "to_char", "to_date",
    "to_timestamp", "date_part", "date_trunc",
    # texto
    "btrim", "char_length", "octet_length", "ascii", "chr", "repeat", "concat_ws",
    "regexp_match", "regexp_split_to_array", "string_to_array", "array_to_string",
    "unaccent", "to_ascii", "starts_with",
    # números y estadística
    "div", "log", "ln", "exp", "sign", "cbrt", "width_bucket", "num_nulls", "num_nonnulls",
    "every", "bit_and", "bit_or", "corr", "covar_pop", "covar_samp", "regr_slope",
    "regr_intercept", "stddev_pop", "stddev_samp", "var_pop", "var_samp", "mode",
    "percentile_disc", "cume_dist", "percent_rank", "nth_value", "last_value",
    # arreglos / JSON para dar forma al resultado
    "array_length", "cardinality", "json_build_object", "jsonb_build_object",
    "json_build_array", "jsonb_build_array", "row_to_json", "to_json", "to_jsonb",
    "json_agg", "jsonb_agg", "json_object_agg", "jsonb_object_agg",
}


@dataclass
class GuardResult:
    ok: bool
    sql: str = ""
    reason: str = ""
    tables: tuple[str, ...] = ()


def _check_function(node: exp.Func) -> str:
    """Devuelve el motivo de rechazo de una función o "" si está permitida."""
    anonymous = isinstance(node, exp.Anonymous)
    name = (node.name if anonymous else node.sql_name()).lower()
    if name in FORBIDDEN_FUNCS or name.startswith(FORBIDDEN_FUNC_PREFIXES):
        return f"forbidden_function: {name}"
    if anonymous and name not in ALLOWED_ANON_FUNCS:
        return f"function_not_allowed: {name}"
    return ""


def _limit_value(limit: exp.Expression) -> int | None:
    """Valor literal de LIMIT n / FETCH FIRST n ROWS; None si no es un entero literal."""
    node = limit.args.get("count") if isinstance(limit, exp.Fetch) else limit.expression
    if isinstance(node, exp.Literal) and not node.is_string:
        try:
            return int(node.name)
        except ValueError:
            return None
    return None


def validate(sql: str, allowed_tables: set[str], max_limit: int = 200,
             dialect: str = "postgres") -> GuardResult:
    allowed = {t.lower() for t in allowed_tables}
    try:
        stmts = [s for s in sqlglot.parse(sql, read=dialect) if s is not None]
    except sqlglot.errors.ParseError as e:
        return GuardResult(False, reason=f"parse_error: {e}".splitlines()[0])
    if len(stmts) != 1:
        return GuardResult(False, reason="multiple_statements")
    tree = stmts[0]
    if not isinstance(tree, (exp.Select, exp.Union, exp.Intersect, exp.Except)):
        return GuardResult(False, reason=f"not_select: {type(tree).__name__}")
    for node in tree.walk():
        if isinstance(node, FORBIDDEN):
            return GuardResult(False, reason=f"forbidden_node: {type(node).__name__}")
        if isinstance(node, exp.Into):
            return GuardResult(False, reason="select_into")
        if isinstance(node, exp.Lock):
            return GuardResult(False, reason="locking_clause")
        if isinstance(node, exp.Func) and (why := _check_function(node)):
            return GuardResult(False, reason=why)

    cte_names = {c.alias_or_name.lower() for c in tree.find_all(exp.CTE)}
    tables = []
    for t in tree.find_all(exp.Table):
        if not isinstance(t.this, exp.Identifier):   # generate_series(), unnest(), ROWS FROM…
            return GuardResult(False, reason=f"table_function_not_allowed: {t.this.sql(dialect)}")
        name, schema, catalog = t.name.lower(), (t.db or "").lower(), (t.catalog or "").lower()
        if schema in FORBIDDEN_SCHEMAS or name.startswith("pg_"):
            return GuardResult(False, reason=f"catalog_access: {schema or name}")
        if catalog or schema not in ALLOWED_SCHEMAS:
            return GuardResult(False, reason=f"schema_not_allowed: {'.'.join(filter(None, (catalog, schema)))}")
        if name in cte_names and not schema:
            continue
        if name not in allowed:
            return GuardResult(False, reason=f"table_not_allowed: {name}")
        tables.append(name)

    limit = tree.args.get("limit")
    if limit is None:
        tree = tree.limit(max_limit)
    else:
        n = _limit_value(limit)
        if n is None:
            return GuardResult(False, reason="non_literal_limit")
        if n > max_limit or isinstance(limit, exp.Fetch):   # FETCH se normaliza a LIMIT
            tree = tree.limit(min(n, max_limit))
    return GuardResult(True, tree.sql(dialect=dialect), tables=tuple(sorted(set(tables))))
