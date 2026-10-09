"""Human-in-the-loop: review and apply write requests (ADR-005).

This is deliberately *not* a tool: neither the agent nor the MCP server can call it. A person
reaches it through ``POST /approvals/{id}`` or ``legacybridge approvals``.

Approving is a single transaction with ``lb_writer``: lock the request, require it to be pending,
re-validate it against the policy (the stored row is not trusted), recount the affected rows, run
a parameterised UPDATE, and mark the request executed. Audit rows are written by a database
trigger, not by this code.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb

from legacybridge import policy
from legacybridge.db import connect as default_connect
from legacybridge.jsonable import to_json_value

LIST_COLUMNS = (
    "id, created_at, status, tbl, update_values, where_values, reason, requested_by, "
    "affected_rows, decided_by, decided_at, decision_note, executed_at, result"
)
STATUSES = ("pending", "rejected", "executed", "failed")


class ApprovalError(Exception):
    """A decision that cannot be taken. ``code`` maps to an HTTP status in the API."""

    HTTP = {
        "not_found": 404,
        "already_decided": 409,
        "stale": 409,
        "self_approval": 403,
        "invalid": 422,
        "policy": 422,
    }

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code

    @property
    def http_status(self) -> int:
        return self.HTTP.get(self.code, 400)


def _where(columns: list[str]) -> sql.Composable:
    return sql.SQL(" AND ").join(
        sql.SQL("{} = {}").format(sql.Identifier(c), sql.Placeholder()) for c in columns
    )


class ApprovalService:
    def __init__(self, connect: Callable[..., psycopg.Connection] = default_connect):
        self._connect = connect

    # ------------------------------------------------------------------ reading
    def list(self, status: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        if status is not None and status not in STATUSES:
            raise ApprovalError("invalid", f"status must be one of {', '.join(STATUSES)}")
        query = f"SELECT {LIST_COLUMNS} FROM lb_ops.write_requests"
        params: list[Any] = []
        if status:
            query += " WHERE status = %s"
            params.append(status)
        query += " ORDER BY id DESC LIMIT %s"
        params.append(max(1, min(int(limit), 500)))
        with self._connect("writer") as conn:
            cursor = conn.execute(query, params)
            names = [d.name for d in cursor.description]
            rows = [dict(zip(names, row, strict=True)) for row in cursor.fetchall()]
            conn.rollback()
        return [to_json_value(r) for r in rows]

    def get(self, request_id: int) -> dict[str, Any]:
        with self._connect("writer") as conn:
            cursor = conn.execute(
                f"SELECT {LIST_COLUMNS}, preview FROM lb_ops.write_requests WHERE id = %s",
                (request_id,),
            )
            row = cursor.fetchone()
            if row is None:
                raise ApprovalError("not_found", f"request {request_id} does not exist")
            request = dict(zip([d.name for d in cursor.description], row, strict=True))
            audit = conn.execute(
                "SELECT at, db_user, event, actor, details FROM lb_ops.audit_log "
                "WHERE request_id = %s ORDER BY id",
                (request_id,),
            ).fetchall()
            conn.rollback()
        request["audit"] = [
            dict(zip(("at", "db_user", "event", "actor", "details"), a, strict=True)) for a in audit
        ]
        return to_json_value(request)

    # ------------------------------------------------------------------ deciding
    def decide(
        self, request_id: int, approve: bool, reviewer: str, note: str | None = None
    ) -> dict[str, Any]:
        reviewer = (reviewer or "").strip()
        if not reviewer or len(reviewer) > 80:
            raise ApprovalError("invalid", "reviewer is required (at most 80 characters)")
        if note is not None and len(note) > 500:
            raise ApprovalError("invalid", "note must be at most 500 characters")
        failure: ApprovalError | None = None
        with self._connect("writer") as conn:
            row = conn.execute(
                "SELECT status, tbl, update_values, where_values, requested_by, affected_rows "
                "FROM lb_ops.write_requests WHERE id = %s FOR UPDATE",
                (request_id,),
            ).fetchone()
            if row is None:
                raise ApprovalError("not_found", f"request {request_id} does not exist")
            status, table, update, where, requested_by, expected = row
            if status != "pending":
                raise ApprovalError("already_decided", f"request {request_id} is already {status}")
            if reviewer.lower() == requested_by.lower():
                raise ApprovalError(
                    "self_approval", "the reviewer cannot be the one who proposed it"
                )

            if not approve:
                self._close(conn, request_id, "rejected", reviewer, note, None)
            else:
                outcome, failure = self._apply(conn, table, update, where, expected)
                self._close(
                    conn,
                    request_id,
                    "executed" if failure is None else "failed",
                    reviewer,
                    note,
                    outcome,
                )
            conn.commit()
        if failure is not None:  # the failed state is committed above; now tell the caller
            raise failure
        return self.get(request_id)

    def _apply(
        self, conn: psycopg.Connection, table: str, update: Any, where: Any, expected: int
    ) -> tuple[dict[str, Any], ApprovalError | None]:
        """Run the stored change, or say why not. Never trusts what the database row says."""
        problem = policy.validate_change(table, update, where)
        if problem:
            message = f"the stored request violates the policy: {problem.code} ({problem.detail})"
            return {"reason": "policy", "problem": problem.code}, ApprovalError("policy", message)
        columns = {
            r[0]
            for r in conn.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = %s",
                (table,),
            ).fetchall()
        }
        unknown = sorted(set(where) - columns)
        if unknown:
            return {"reason": "policy", "problem": "unknown_where_column"}, ApprovalError(
                "policy", f"unknown column in where: {', '.join(unknown)}"
            )
        conditions, where_values = _where(list(where)), list(where.values())
        count = conn.execute(
            sql.SQL("SELECT count(*) FROM {} WHERE {}").format(sql.Identifier(table), conditions),
            where_values,
        ).fetchone()[0]
        if count != expected or not 0 < count <= policy.MAX_AFFECTED:
            return {"reason": "stale", "proposed_rows": expected, "rows_now": count}, ApprovalError(
                "stale",
                f"the data changed since it was proposed: {expected} rows then, {count} now; "
                "propose it again",
            )
        assignments = sql.SQL(", ").join(
            sql.SQL("{} = {}").format(sql.Identifier(c), sql.Placeholder()) for c in update
        )
        try:
            with conn.transaction():  # a savepoint: a wrong rowcount undoes only the UPDATE
                cursor = conn.execute(
                    sql.SQL("UPDATE {} SET {} WHERE {}").format(
                        sql.Identifier(table), assignments, conditions
                    ),
                    [*update.values(), *where_values],
                )
                if cursor.rowcount != count:
                    raise _Mismatch(cursor.rowcount)
        except _Mismatch as mismatch:
            return {
                "reason": "rowcount",
                "expected": count,
                "updated": mismatch.updated,
            }, ApprovalError(
                "stale",
                f"the update touched {mismatch.updated} rows, not {count}; nothing was changed",
            )
        except psycopg.Error as error:
            return {"reason": "database", "error": str(error).splitlines()[0]}, ApprovalError(
                "policy", f"the database refused the change: {str(error).splitlines()[0]}"
            )
        return {"rows_updated": count}, None

    @staticmethod
    def _close(
        conn: psycopg.Connection,
        request_id: int,
        status: str,
        reviewer: str,
        note: str | None,
        result: dict[str, Any] | None,
    ) -> None:
        conn.execute(
            "UPDATE lb_ops.write_requests SET status = %s, decided_by = %s, decided_at = now(), "
            "decision_note = %s, executed_at = CASE WHEN %s = 'executed' THEN now() END, "
            "result = %s WHERE id = %s",
            (
                status,
                reviewer,
                note,
                status,
                Jsonb(result) if result is not None else None,
                request_id,
            ),
        )


class _Mismatch(Exception):
    def __init__(self, updated: int):
        super().__init__(updated)
        self.updated = updated
