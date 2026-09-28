"""Validador de SQL por AST: solo SELECT, allowlist de tablas, sin catálogos, LIMIT forzado."""
from __future__ import annotations

from dataclasses import dataclass

import sqlglot
from sqlglot import exp

FORBIDDEN = (exp.Insert, exp.Update, exp.Delete, exp.Drop, exp.Create, exp.Alter,
             exp.Merge, exp.Command, exp.Grant, exp.TruncateTable, exp.Copy)
FORBIDDEN_SCHEMAS = {"pg_catalog", "information_schema", "pg_toast"}
FORBIDDEN_FUNCS = {"pg_read_file", "pg_ls_dir", "pg_sleep", "dblink", "lo_import",
                   "lo_export", "pg_read_binary_file", "set_config", "current_setting"}


@dataclass
class GuardResult:
    ok: bool
    sql: str = ""
    reason: str = ""
    tables: tuple[str, ...] = ()


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
        if isinstance(node, (exp.Anonymous, exp.Func)):
            name = (node.name if isinstance(node, exp.Anonymous) else node.sql_name()).lower()
            if name in FORBIDDEN_FUNCS:
                return GuardResult(False, reason=f"forbidden_function: {name}")

    cte_names = {c.alias_or_name.lower() for c in tree.find_all(exp.CTE)}
    tables = []
    for t in tree.find_all(exp.Table):
        name, schema = t.name.lower(), (t.db or "").lower()
        if schema in FORBIDDEN_SCHEMAS or name.startswith("pg_"):
            return GuardResult(False, reason=f"catalog_access: {schema or name}")
        if name in cte_names:
            continue
        if name not in allowed:
            return GuardResult(False, reason=f"table_not_allowed: {name}")
        tables.append(name)

    limit = tree.args.get("limit")
    if limit is None:
        tree = tree.limit(max_limit)
    else:
        try:
            n = int(limit.expression.name)
        except (AttributeError, ValueError):
            return GuardResult(False, reason="non_literal_limit")
        if n > max_limit:
            tree = tree.limit(max_limit)
    return GuardResult(True, tree.sql(dialect=dialect), tables=tuple(sorted(set(tables))))
