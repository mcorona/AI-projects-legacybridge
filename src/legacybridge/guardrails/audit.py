"""Bitácora de auditoría append-only (`ops.audit_log`, rol `lb_audit` con solo INSERT).

Portado de inventory-copilot (`src/audit.py`). Un fallo de auditoría no tumba la respuesta al
usuario, pero tampoco pasa en silencio: se reporta por stderr.
"""
from __future__ import annotations

import json
import os
import sys
from typing import Protocol


class AuditSink(Protocol):
    def log(self, actor: str, event: str, details: dict | None = None) -> None: ...


class NullAuditSink:
    def log(self, actor, event, details=None):
        pass


class ListAuditSink:
    """Para pruebas y evaluaciones: guarda los eventos en memoria."""

    def __init__(self):
        self.events: list[dict] = []

    def log(self, actor, event, details=None):
        self.events.append({"actor": actor, "event": event, "details": details or {}})


class DbAuditSink:
    def __init__(self, dsn: str):
        self.dsn = dsn

    def log(self, actor, event, details=None):
        import psycopg
        try:
            with psycopg.connect(self.dsn, connect_timeout=3) as conn:
                conn.execute("INSERT INTO ops.audit_log (actor, event, details) VALUES (%s, %s, %s)",
                             (actor, event, json.dumps(details or {}, ensure_ascii=False, default=str)))
        except Exception as e:  # noqa: BLE001
            print(f"[audit] no se pudo registrar {event}: {type(e).__name__}: {e}", file=sys.stderr)


def get_audit_sink() -> AuditSink:
    dsn = os.environ.get("LB_AUDIT_DSN")
    return DbAuditSink(dsn) if dsn else NullAuditSink()
