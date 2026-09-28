"""Propuestas de cambio (human-in-the-loop, Fase 4). El sistema NUNCA ejecuta DML.

Flujo: el agente llama `propose_change` -> `write_guard` valida y se estima el impacto con un
COUNT(*) de solo lectura -> el agente se pausa (`confirmation_required`) -> el usuario confirma ->
la propuesta queda como PENDING_REVIEW (rol `lb_proposals`, solo INSERT) -> un revisor la aprueba
o rechaza con `python -m legacybridge.agent.proposals` (rol `lb_reviewer`). Aprobar significa que un
DBA puede aplicarla por fuera; nada de este sistema la ejecuta.

Revisión humana: `python -m legacybridge.agent.review_cli` (ver ese módulo).
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Protocol

DEFAULT_PROPOSALS_DSN = "postgresql://lb_proposals:lb_proposals@localhost:5433/legacy"
DEFAULT_REVIEWER_DSN = "postgresql://lb_reviewer:lb_reviewer@localhost:5433/legacy"


@dataclass
class ProposalPreview:
    sql: str
    kind: str
    table: str
    rationale: str
    affected_rows_est: int | None = None
    call_id: str = ""


@dataclass
class Proposal:
    preview: ProposalPreview
    question: str
    requested_by: str
    confirmed_by: str
    id: int | None = None
    status: str = "PENDING_REVIEW"
    extra: dict = field(default_factory=dict)


class ProposalStore(Protocol):
    def save(self, p: Proposal) -> int: ...


class ListProposalStore:
    """Para pruebas y evaluaciones: en memoria."""

    def __init__(self):
        self.items: list[Proposal] = []

    def save(self, p: Proposal) -> int:
        p.id = len(self.items) + 1
        self.items.append(p)
        return p.id


class DbProposalStore:
    def __init__(self, dsn: str | None = None):
        self.dsn = dsn or os.environ.get("LB_PROPOSALS_DSN", DEFAULT_PROPOSALS_DSN)

    def save(self, p: Proposal) -> int:
        import psycopg
        with psycopg.connect(self.dsn, connect_timeout=3) as conn:
            # lb_proposals no puede leer la tabla: el id sale de la secuencia, no de RETURNING
            pid = conn.execute("SELECT nextval('ops.change_proposals_id_seq')").fetchone()[0]
            conn.execute(
                "INSERT INTO ops.change_proposals (id, requested_by, confirmed_by, question, rationale, sql, kind, "
                "target_table, affected_rows_est) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (pid, p.requested_by, p.confirmed_by, p.question, p.preview.rationale, p.preview.sql,
                 p.preview.kind, p.preview.table, p.preview.affected_rows_est))
            return pid
