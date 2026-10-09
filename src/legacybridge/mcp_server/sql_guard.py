"""SQL guard (ADR-004): the first of three layers between a model and the data.

``validate`` accepts exactly one read-only SELECT over the allowed tables and columns and returns
the SQL **regenerated from the AST**; callers must run that string, never the original text.

Function policy:
- Functions sqlglot types (SUM, COALESCE, LPAD...): allowed unless on the denylist.
- Functions sqlglot does not know (``exp.Anonymous``): allowed only if listed in
  ``ALLOWED_ANON_FUNCS``. A new or unknown dangerous function (``query_to_xml``, ``setval``, a UDF)
  is therefore refused by default instead of depending on someone adding it to the denylist.
- Administration prefixes (``pg_``, ``lo_``, ``dblink``) are always refused.
"""

from __future__ import annotations

import difflib
import logging
import re
from dataclasses import dataclass

import sqlglot
from sqlglot import exp
from sqlglot.errors import OptimizeError, SqlglotError
from sqlglot.optimizer.qualify import qualify
from sqlglot.optimizer.scope import traverse_scope

from legacybridge import policy
from legacybridge.mcp_server.schema import Schema

DIALECT = "postgres"
# sqlglot logs a warning for every non-SELECT it falls back to parsing as a Command; those are
# rejected anyway and would only add noise to the MCP server's stderr.
logging.getLogger("sqlglot").setLevel(logging.ERROR)
FORBIDDEN_NODES = (
    exp.Insert, exp.Update, exp.Delete, exp.Drop, exp.Create, exp.Alter, exp.Merge,
    exp.Command, exp.Grant, exp.TruncateTable, exp.Copy,
)  # fmt: skip
FORBIDDEN_SCHEMAS = {"pg_catalog", "information_schema", "pg_toast"}
FORBIDDEN_FUNC_PREFIXES = ("pg_", "lo_", "dblink")
FORBIDDEN_CASTS = {"regclass", "regproc", "regprocedure", "regoper", "regtype", "regnamespace",
                   "regrole", "oid"}  # fmt: skip
FORBIDDEN_FUNCS = {
    # file and session I/O, configuration
    "pg_read_file", "pg_ls_dir", "pg_sleep", "pg_read_binary_file", "lo_import", "lo_export",
    "dblink", "set_config", "current_setting",
    # side effects on sequences and transactions
    "setval", "nextval", "currval", "lastval", "txid_current",
    # SQL inside strings: they run an arbitrary query and skip the AST
    "query_to_xml", "query_to_xml_and_xmlschema", "query_to_xmlschema",
    "table_to_xml", "table_to_xml_and_xmlschema", "table_to_xmlschema",
    "cursor_to_xml", "cursor_to_xmlschema", "schema_to_xml", "database_to_xml",
    # privilege and catalog introspection by name
    "to_regclass", "has_table_privilege", "has_column_privilege", "has_schema_privilege",
    # server and session fingerprint (sqlglot types these, so they need an explicit denylist)
    "current_version", "version", "current_user", "session_user", "current_role",
    "current_database", "current_catalog", "current_schema", "current_schemas",
    "inet_server_addr", "inet_server_port", "inet_client_addr", "pg_backend_pid",
    # allocation bombs
    "repeat",
}  # fmt: skip
# SQL keywords PostgreSQL evaluates as functions although sqlglot reads them as columns.
SESSION_KEYWORDS = {"user", "current_user", "session_user", "current_role", "current_catalog",
                    "current_schema"}  # fmt: skip
# PostgreSQL functions sqlglot does not type that are legitimate in analytical queries.
ALLOWED_ANON_FUNCS = {
    "age", "make_date", "make_time", "make_timestamp", "make_interval", "date_bin",
    "justify_days", "justify_hours", "justify_interval", "isfinite", "to_char", "to_date",
    "to_timestamp", "date_part", "date_trunc",
    "btrim", "char_length", "octet_length", "ascii", "chr", "concat_ws", "regexp_match",
    "regexp_split_to_array", "string_to_array", "array_to_string", "unaccent", "to_ascii",
    "starts_with", "convert_from", "convert_to",
    "div", "log", "ln", "exp", "sign", "cbrt", "width_bucket", "num_nulls", "num_nonnulls",
    "every", "bit_and", "bit_or", "corr", "covar_pop", "covar_samp", "regr_slope",
    "regr_intercept", "stddev_pop", "stddev_samp", "var_pop", "var_samp", "mode",
    "percentile_disc", "cume_dist", "percent_rank", "nth_value", "last_value",
    "array_length", "cardinality", "json_build_object", "jsonb_build_object",
    "json_build_array", "jsonb_build_array", "row_to_json", "to_json", "to_jsonb",
    "json_agg", "jsonb_agg", "json_object_agg", "jsonb_object_agg",
}  # fmt: skip


@dataclass
class GuardResult:
    ok: bool
    sql: str = ""  # regenerated from the AST, with the LIMIT applied
    reason: str = ""
    detail: str = ""
    hint: str = ""
    tables: tuple[str, ...] = ()
    columns: tuple[str, ...] = ()  # "table.column" actually referenced
    limit: int | None = None  # rows the caller may return
    capped: bool = False  # the guard added or lowered the LIMIT, so the result may be truncated


def _reject(reason: str, detail: str = "", hint: str = "") -> GuardResult:
    return GuardResult(False, reason=reason, detail=detail, hint=hint)


def _check_function(node: exp.Func) -> str:
    """Reason to refuse a function, or "" if it is allowed."""
    anonymous = isinstance(node, exp.Anonymous)
    name = (node.name if anonymous else node.sql_name()).lower()
    if name in FORBIDDEN_FUNCS or name.startswith(FORBIDDEN_FUNC_PREFIXES):
        return f"forbidden_function: {name}"
    if anonymous and name not in ALLOWED_ANON_FUNCS:
        return f"function_not_allowed: {name}"
    return ""


def _literal_int(node: exp.Expression | None) -> int | None:
    if isinstance(node, exp.Literal) and not node.is_string:
        try:
            return int(node.name)
        except ValueError:
            return None
    return None


def _limit_value(limit: exp.Expression) -> int | None:
    """Literal n of LIMIT n / FETCH FIRST n ROWS; None for LIMIT ALL or any expression."""
    return _literal_int(
        limit.args.get("count") if isinstance(limit, exp.Fetch) else limit.expression
    )


def _cartesian(join: exp.Join) -> bool:
    """True for CROSS JOIN, comma joins, JOIN without a condition, ON TRUE and ON 1 = 1."""
    if isinstance(join.this, exp.Lateral):
        return False
    if (join.args.get("kind") or "").upper() == "CROSS":
        return True
    on, using = join.args.get("on"), join.args.get("using")
    if on is None and using is None:
        return not join.args.get("method")  # NATURAL joins are refused elsewhere
    if isinstance(on, exp.Boolean):
        return bool(on.this)
    return isinstance(on, exp.EQ) and isinstance(on.this, exp.Literal) and on.this == on.expression


def _suggest(name: str, candidates: list[str]) -> str:
    close = difflib.get_close_matches(name.lower(), sorted(set(candidates)), n=3, cutoff=0.6)
    return f"Did you mean {', '.join(close)}?" if close else ""


def _columns_used(qualified: exp.Expression) -> tuple[str, ...]:
    used: set[str] = set()
    for scope in traverse_scope(qualified):
        for column in scope.columns:
            source = scope.sources.get(column.table)
            if isinstance(source, exp.Table):
                used.add(f"{source.name}.{column.name}")
    return tuple(sorted(used))


def validate(
    sql: str, schema: Schema, max_rows: int = policy.DEFAULT_ROWS, dialect: str = DIALECT
) -> GuardResult:
    if not sql or not sql.strip():
        return _reject("empty_sql")
    if len(sql) > policy.MAX_SQL_CHARS:
        return _reject("sql_too_long", f"{len(sql)} > {policy.MAX_SQL_CHARS} characters")
    max_rows = max(1, min(int(max_rows), policy.MAX_ROWS))
    try:
        statements = [s for s in sqlglot.parse(sql, read=dialect) if s is not None]
    except SqlglotError as e:
        return _reject("parse_error", str(e).splitlines()[0])
    except RecursionError:
        return _reject("too_complex")
    if len(statements) != 1:
        return _reject("multiple_statements" if statements else "empty_sql")
    tree = statements[0]
    while isinstance(tree, exp.Subquery) and not tree.args.get("alias"):
        tree = tree.this  # a parenthesised SELECT
    if not isinstance(tree, exp.Query) or isinstance(tree, exp.Subquery):
        return _reject("not_select", type(tree).__name__)
    if isinstance(tree, exp.Select) and not tree.expressions:
        return _reject("empty_select", "a SELECT needs at least one expression")

    for node in tree.walk():
        if isinstance(node, FORBIDDEN_NODES):
            return _reject("forbidden_node", type(node).__name__)
        if isinstance(node, exp.Into):
            return _reject("select_into")
        if isinstance(node, exp.Lock):
            return _reject("locking_clause")
        if isinstance(node, exp.UDTF) and not isinstance(node, exp.Lateral):
            return _reject("table_function_not_allowed", node.sql(dialect)[:60])
        if isinstance(node, exp.Func) and (why := _check_function(node)):
            return _reject(*why.split(": ", 1))
        if isinstance(node, exp.Cast) and node.to.sql(dialect).lower() in FORBIDDEN_CASTS:
            return _reject("forbidden_cast", node.to.sql(dialect))
        if isinstance(node, exp.Pad):
            length = _literal_int(node.args.get("expression"))
            if length is not None and length > policy.MAX_LPAD:
                return _reject("memory_bomb", f"pad length {length} > {policy.MAX_LPAD}")
        if (
            isinstance(node, exp.Column)
            and not node.table
            and node.name.lower() in SESSION_KEYWORDS
        ):
            return _reject("forbidden_function", node.name.lower())
        if isinstance(node, exp.Join):
            if node.args.get("method") == "NATURAL":
                return _reject("natural_join_not_allowed")
            if _cartesian(node):
                return _reject("cartesian_product", "use JOIN ... ON with a real join condition")

    cte_names = {c.alias_or_name.lower() for c in tree.find_all(exp.CTE)}
    tables = []
    for t in tree.find_all(exp.Table):
        if not isinstance(t.this, exp.Identifier):  # generate_series(), unnest(), ROWS FROM...
            return _reject("table_function_not_allowed", t.this.sql(dialect))
        # PostgreSQL folds unquoted names to lower case; a quoted "Clientes" keeps its case.
        name = t.name if t.this.quoted else t.name.lower()
        db, catalog = (t.db or "").lower(), (t.catalog or "").lower()
        if db in FORBIDDEN_SCHEMAS or name.lower().startswith("pg_"):
            return _reject("catalog_access", db or name)
        if catalog or db not in ("", "public"):
            return _reject("schema_not_allowed", ".".join(filter(None, (catalog, db))))
        if name.lower() in cte_names and not db:
            continue
        if name not in schema:  # exact match: quoted "Clientes" is a different, unknown name
            return _reject(
                "table_not_allowed", name, _suggest(name, list(schema)) or "See list_tables."
            )
        tables.append(name)

    try:
        qualified = qualify(
            tree.copy(),
            schema={t: dict.fromkeys(cols, "text") for t, cols in schema.items()},
            dialect=dialect,
            quote_identifiers=True,
            identify=False,
        )
        columns = _columns_used(qualified)
    except OptimizeError as e:
        message = str(e).splitlines()[0]
        match = re.search(r"Column '([^']+)'", message)
        name = match.group(1) if match else ""
        pool = [c for t in set(tables) for c in schema.get(t, {})]
        # sqlglot reports an ambiguous column as unresolved: it is ambiguous when two tables of
        # the query both have it.
        shared = sum(1 for t in tables if name.lower() in schema.get(t, {})) >= 2
        if "mbiguous" in message or (name and shared):
            return _reject("ambiguous_column", name, "Qualify the column with its table alias.")
        return _reject(
            "unknown_column", name or message, (_suggest(name, pool) if name else "") or
            "Use describe_table to see the columns.",
        )  # fmt: skip
    except (SqlglotError, RecursionError, KeyError) as e:
        return _reject("unresolvable_query", str(e).splitlines()[0][:120] if str(e) else "")

    limit = tree.args.get("limit")
    capped = False
    if limit is None:
        tree, capped, applied = tree.limit(max_rows + 1), True, max_rows
    else:
        n = _limit_value(limit)
        if n is None:
            return _reject("non_literal_limit", "LIMIT must be a plain integer")
        if n > max_rows:
            tree, capped, applied = tree.limit(max_rows + 1), True, max_rows
        else:
            tree, applied = (tree.limit(n) if isinstance(limit, exp.Fetch) else tree), n
    return GuardResult(
        True,
        sql=tree.sql(dialect=dialect, comments=False),  # pure AST: comments are never executed
        tables=tuple(sorted(set(tables))),
        columns=columns,
        limit=applied,
        capped=capped,
    )
