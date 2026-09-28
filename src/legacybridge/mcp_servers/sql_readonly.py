"""MCP server: ejecución de SQL de solo lectura, siempre validada por sql_guard.

Defensa en profundidad para cada consulta:
1. `sql_guard.validate`: un solo SELECT, allowlist, sin catálogos ni funciones peligrosas, LIMIT.
2. Verificación previa, sin la cual no se ejecuta nada: el usuario conectado es `lb_ro` y
   la transacción es de solo lectura.
3. `statement_timeout` local a la transacción (5 s por defecto), además del del rol.
4. La transacción siempre termina en ROLLBACK.

Arranque por stdio: `python -m legacybridge.mcp_servers.sql_readonly`.
"""
from __future__ import annotations

import base64
import datetime as dt
import os
import time
import uuid
from decimal import Decimal
from typing import Annotated, Any

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field

from legacybridge.dictionary import load as load_dictionary
from legacybridge.guard.sql_guard import validate

INSTRUCTIONS = (
    "Ejecuta un único SELECT sobre el ERP legacy con un rol de solo lectura. Toda SQL pasa "
    "por un validador AST; si se rechaza o falla, la respuesta trae `stage` y `reason`/"
    "`message` para corregirla. Consulta primero legacybridge-schema para nombres y reglas."
)
READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False,
                            idempotent_hint=True, open_world_hint=False)
DEFAULT_DSN = "postgresql://lb_ro:lb_ro@localhost:5433/legacy"
MAX_ROWS = 200


def to_json_value(v: Any) -> Any:
    """Convierte tipos de psycopg a JSON sin perder información relevante."""
    if isinstance(v, Decimal):
        return int(v) if v == v.to_integral_value() and v.is_finite() else float(v)
    if isinstance(v, (dt.date, dt.datetime, dt.time)):
        return v.isoformat()
    if isinstance(v, dt.timedelta):
        return str(v)
    if isinstance(v, uuid.UUID):
        return str(v)
    if isinstance(v, (bytes, bytearray, memoryview)):
        return {"base64": base64.b64encode(bytes(v)).decode()}
    if isinstance(v, (list, tuple)):
        return [to_json_value(x) for x in v]
    if isinstance(v, dict):
        return {k: to_json_value(x) for k, x in v.items()}
    return v


class ReadOnlyExecutor:
    def __init__(self, dsn: str | None = None, allowed_tables: set[str] | None = None,
                 expected_role: str | None = None, timeout_ms: int | None = None):
        self.dsn = dsn or os.environ.get("LB_DSN", DEFAULT_DSN)
        self.allowed = allowed_tables or set(load_dictionary().allowed_tables)
        self.expected_role = expected_role or os.environ.get("LB_EXPECTED_ROLE", "lb_ro")
        self.timeout_ms = int(timeout_ms or os.environ.get("LB_STATEMENT_TIMEOUT_MS", 5000))

    def run(self, sql: str, max_rows: int = 100) -> dict:
        max_rows = max(1, min(int(max_rows), MAX_ROWS))
        g = validate(sql, self.allowed, max_limit=max_rows)
        if not g.ok:
            return {"ok": False, "rejected": True, "stage": "guard", "reason": g.reason}
        out = self._execute(g.sql, max_rows)
        if out["ok"]:
            out["tables"] = list(g.tables)
        return out

    def _execute(self, sql: str, max_rows: int) -> dict:
        """Ejecuta SQL YA validada. Privado: nunca exponer sin pasar por el guard."""
        import psycopg

        base = {"ok": False, "rejected": False, "sql": sql}
        t0 = time.perf_counter()
        try:
            conn = psycopg.connect(self.dsn, connect_timeout=3)
        except psycopg.Error as e:
            return {**base, "stage": "connection", "error_type": "connection_failed",
                    "message": _first_line(e)}
        try:
            conn.read_only = True   # BEGIN READ ONLY en cada transacción
            role, ro = conn.execute(
                "SELECT current_user, current_setting('transaction_read_only')").fetchone()
            if role != self.expected_role or ro != "on":
                return {**base, "stage": "preflight", "error_type": "unsafe_session",
                        "message": f"sesión rechazada: user={role} read_only={ro}; "
                                   f"se esperaba user={self.expected_role} read_only=on"}
            conn.execute("SELECT set_config('statement_timeout', %s, true)", (str(self.timeout_ms),))
            cur = conn.execute(sql)
            columns = [d.name for d in cur.description or []]
            rows = [[to_json_value(v) for v in r] for r in cur.fetchmany(max_rows)]
        except psycopg.Error as e:
            return {**base, "stage": "execution", "error_type": _error_type(e),
                    "sqlstate": getattr(e, "sqlstate", None), "message": _first_line(e)}
        finally:
            try:
                conn.rollback()   # nunca COMMIT
            finally:
                conn.close()
        # el guard ya fuerza LIMIT <= max_rows: llegar al tope significa que puede haber más
        return {"ok": True, "rejected": False, "sql": sql, "columns": columns, "rows": rows,
                "row_count": len(rows), "truncated": len(rows) >= max_rows,
                "ms": round((time.perf_counter() - t0) * 1000, 1)}


def _first_line(e: Exception) -> str:
    return (str(e).strip().splitlines() or [type(e).__name__])[0][:300]


def _error_type(e: Exception) -> str:
    import psycopg
    from psycopg import errors

    if isinstance(e, errors.QueryCanceled):
        return "timeout"
    if isinstance(e, errors.InsufficientPrivilege):
        return "permission_denied"
    if isinstance(e, errors.ReadOnlySqlTransaction):
        return "read_only_violation"
    if isinstance(e, (errors.UndefinedColumn, errors.UndefinedTable, errors.UndefinedFunction)):
        return "undefined_object"
    if isinstance(e, (psycopg.DataError, psycopg.ProgrammingError)):   # clase 22 y 42 (DB-API)
        return "invalid_query"
    return type(e).__name__


def protect_result(result: dict, guardrails) -> dict:
    """Canal de salida hacia un cliente MCP (otro LLM): retira instrucciones incrustadas en los datos
    (D9) y enmascara PII/DLP en cada celda de texto. El ejecutor no se toca: las evaluaciones y la
    auditoría usan las filas íntegras."""
    clean, findings = guardrails.sanitize_tool_result("run_query", result)
    if clean.get("rows"):
        clean["rows"] = [[guardrails.check_output(v).text if isinstance(v, str) else v for v in row]
                         for row in clean["rows"]]
    if findings:
        clean["guardrail_note"] = f"{len(findings)} valor(es) retirado(s) por contener instrucciones"
    return clean


def build_server(executor: ReadOnlyExecutor | None = None, log_level: str = "INFO",
                 guardrails=None) -> MCPServer:
    from legacybridge.guardrails import GuardrailPipeline

    ex = executor or ReadOnlyExecutor()
    gr = guardrails if guardrails is not None else GuardrailPipeline.from_env()
    gr.actor = "mcp"
    server = MCPServer(name="legacybridge-sql", instructions=INSTRUCTIONS,
                       log_level=log_level)  # type: ignore[arg-type]

    @server.tool(annotations=READ_ONLY)
    def run_query(
        sql: Annotated[str, Field(description="Un único SELECT (PostgreSQL) sobre las tablas permitidas")],
        max_rows: Annotated[int, Field(ge=1, le=MAX_ROWS, description="Máximo de filas a devolver")] = 100,
    ) -> dict:
        """Valida y ejecuta una consulta SELECT sobre el sistema legacy (rol de solo lectura).

        Éxito: `ok=true`, la SQL normalizada que realmente se ejecutó, columnas, filas, tablas
        usadas y `truncated` si se alcanzó `max_rows`. Falla: `ok=false` con `stage`
        (guard | connection | preflight | execution) y `reason`/`message` para autocorregirse.
        """
        return protect_result(ex.run(sql, max_rows), gr)

    return server


if __name__ == "__main__":
    build_server().run("stdio")
