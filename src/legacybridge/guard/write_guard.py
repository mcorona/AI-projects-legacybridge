"""Validador de propuestas de escritura (human-in-the-loop, Fase 4, ADR-006).

El sistema NUNCA ejecuta DML: este validador solo decide si una propuesta de cambio es aceptable
para registrarla y que una persona autorizada la revise. Reglas:
- una sola sentencia INSERT / UPDATE / DELETE, sobre una tabla de la allowlist (nunca D8/D10);
- sin DDL, TRUNCATE, COPY, MERGE ni sentencias múltiples; sin CTE que escriban;
- UPDATE y DELETE exigen WHERE (no se proponen cambios masivos sin condición);
- toda tabla o función usada en subconsultas pasa por las reglas del guard de lectura.
`impact_sql` produce el SELECT COUNT(*) de solo lectura que estima cuántas filas afectaría.
"""
from __future__ import annotations

from dataclasses import dataclass

import sqlglot
from sqlglot import exp

from legacybridge.guard.sql_guard import FORBIDDEN, validate

WRITE_NODES = (exp.Insert, exp.Update, exp.Delete)


@dataclass
class WriteProposal:
    ok: bool
    sql: str = ""
    kind: str = ""            # INSERT | UPDATE | DELETE
    table: str = ""
    reason: str = ""
    impact_sql: str | None = None


def validate_write(sql: str, allowed_tables: set[str], dialect: str = "postgres") -> WriteProposal:
    allowed = {t.lower() for t in allowed_tables}
    try:
        stmts = [s for s in sqlglot.parse(sql, read=dialect) if s is not None]
    except sqlglot.errors.ParseError as e:
        return WriteProposal(False, reason=f"parse_error: {e}".splitlines()[0])
    if len(stmts) != 1:
        return WriteProposal(False, reason="multiple_statements")
    tree = stmts[0]
    if not isinstance(tree, WRITE_NODES):
        return WriteProposal(False, reason=f"not_a_write: {type(tree).__name__}")
    if tree.args.get("with") or any(isinstance(n, (exp.With, exp.CTE)) for n in tree.walk()):
        return WriteProposal(False, reason="cte_not_allowed")
    nested = [n for n in tree.walk() if n is not tree and isinstance(n, FORBIDDEN)]
    if nested:
        return WriteProposal(False, reason=f"forbidden_node: {type(nested[0]).__name__}")

    target = tree.this if not isinstance(tree.this, exp.Schema) else tree.this.this
    if not isinstance(target, exp.Table):
        return WriteProposal(False, reason="unsupported_target")
    table, schema = target.name.lower(), (target.db or "").lower()
    if schema not in ("", "public") or target.catalog:
        return WriteProposal(False, reason=f"schema_not_allowed: {schema}")
    if table not in allowed:
        return WriteProposal(False, reason=f"table_not_allowed: {table}")
    kind = type(tree).__name__.upper()
    where = tree.args.get("where")
    if kind in ("UPDATE", "DELETE") and where is None:
        return WriteProposal(False, reason="missing_where")

    # subconsultas, funciones y tablas de las expresiones pasan por el guard de lectura
    for sub in [n for n in tree.walk() if isinstance(n, exp.Select)]:
        r = validate(sub.sql(dialect), allowed, dialect=dialect)
        if not r.ok:
            return WriteProposal(False, reason=r.reason)
    probe = exp.select("1").from_(table)
    exprs = [e for e in tree.find_all(exp.Func)]
    if exprs:
        probe = probe.where(exp.and_(*[exp.EQ(this=e.copy(), expression=e.copy()) for e in exprs]))
    r = validate(probe.sql(dialect), allowed, dialect=dialect)
    if not r.ok:
        return WriteProposal(False, reason=r.reason)

    impact = None
    if kind in ("UPDATE", "DELETE"):
        impact = exp.select("COUNT(*)").from_(table).where(where.this.copy()).sql(dialect)
    return WriteProposal(True, tree.sql(dialect), kind, table, impact_sql=impact)
