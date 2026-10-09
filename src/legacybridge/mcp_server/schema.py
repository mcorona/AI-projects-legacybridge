"""The schema the guard allows: ``{table: {column: declared type}}``.

At run time it is whatever ``lb_reader`` can SELECT, read from the catalog, so the database grants
are the single source of truth (ADR-004). Tests build the same structure from the DDL files without
a database, and a test checks that both agree.
"""

from __future__ import annotations

from pathlib import Path

import sqlglot
from sqlglot import exp

Schema = dict[str, dict[str, str]]

DDL_DIR = Path(__file__).resolve().parents[3] / "db" / "init"
BUSINESS_DDL = ("01_facturacion.sql", "02_sicpt.sql")

_CATALOG_SQL = """
SELECT c.relname, a.attname, format_type(a.atttypid, a.atttypmod)
FROM pg_attribute a
JOIN pg_class c ON c.oid = a.attrelid
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND c.relkind = 'r' AND a.attnum > 0 AND NOT a.attisdropped
  AND has_column_privilege(c.oid, a.attnum, 'SELECT')
ORDER BY c.relname, a.attnum
"""


def schema_from_db(conn) -> Schema:
    """Tables and columns the connected role can SELECT."""
    schema: Schema = {}
    for table, column, type_ in conn.execute(_CATALOG_SQL).fetchall():
        schema.setdefault(table, {})[column] = type_
    return schema


def schema_from_ddl(directory: Path = DDL_DIR, files: tuple[str, ...] = BUSINESS_DDL) -> Schema:
    """The same structure, parsed from the CREATE TABLE statements Docker loads."""
    schema: Schema = {}
    for name in files:
        for statement in sqlglot.parse((directory / name).read_text(encoding="utf-8"), "postgres"):
            if not isinstance(statement, exp.Create) or statement.args.get("kind") != "TABLE":
                continue
            table = statement.this
            columns = {
                col.name: col.args["kind"].sql("postgres").lower()
                for col in table.expressions
                if isinstance(col, exp.ColumnDef)
            }
            schema[table.this.name] = columns
    return schema
