"""Índice vectorial en pgvector (`rag.chunks`).

- Escritura: `sync_index` con el rol `lb_rag_rw` (LB_RAG_DSN). Incremental por hash de contenido:
  solo se embeben los fragmentos nuevos o modificados y se borran los obsoletos.
- Lectura: `search` con `lb_ro` y una consulta FIJA parametrizada. No pasa por la normalización
  del guard: sqlglot reescribe el operador `<=>` de pgvector como `IS NOT DISTINCT FROM`
  (lección portada de inventory-copilot), lo que rompería la búsqueda sin error.
"""
from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import dataclass

from legacybridge.rag.chunking import Chunk

DEFAULT_RAG_DSN = "postgresql://lb_rag_rw:lb_rag_rw@localhost:5433/legacy"
DEFAULT_READ_DSN = "postgresql://lb_ro:lb_ro@localhost:5433/legacy"
EmbedFn = Callable[[list[str]], list[list[float]]]


def to_vector_literal(vec: list[float]) -> str:
    return "[" + ",".join(f"{x:.7g}" for x in vec) + "]"


@dataclass
class IndexReport:
    model_id: str
    added: int = 0
    updated: int = 0
    deleted: int = 0
    unchanged: int = 0

    @property
    def embedded(self) -> int:
        return self.added + self.updated


def plan_sync(chunks: list[Chunk], existing: dict[tuple[str, int], str]) -> tuple[list[Chunk], list[Chunk], list[tuple[str, int]]]:
    """(nuevos, modificados, llaves obsoletas) comparando hashes con lo ya indexado."""
    keys = {(c.source, c.index) for c in chunks}
    if len(keys) != len(chunks):
        raise ValueError("fragmentos duplicados (source, index)")
    new = [c for c in chunks if (c.source, c.index) not in existing]
    changed = [c for c in chunks
               if (c.source, c.index) in existing and existing[(c.source, c.index)] != c.content_hash]
    stale = sorted(k for k in existing if k not in keys)
    return new, changed, stale


def sync_index(chunks: list[Chunk], embed_fn: EmbedFn, model_id: str, dsn: str | None = None,
               dry_run: bool = False) -> IndexReport:
    import psycopg

    report = IndexReport(model_id)
    with psycopg.connect(dsn or os.environ.get("LB_RAG_DSN", DEFAULT_RAG_DSN)) as conn:
        existing = {(s, i): h for s, i, h in conn.execute(
            "SELECT source, chunk_index, content_hash FROM rag.chunks WHERE embed_model = %s",
            (model_id,)).fetchall()}
        new, changed, stale = plan_sync(chunks, existing)
        report.added, report.updated, report.deleted = len(new), len(changed), len(stale)
        report.unchanged = len(chunks) - len(new) - len(changed)
        if dry_run:
            conn.rollback()
            return report
        todo = new + changed
        vectors = embed_fn([c.content for c in todo]) if todo else []
        with conn.cursor() as cur:
            if stale:
                cur.executemany("DELETE FROM rag.chunks WHERE embed_model = %s AND source = %s "
                                "AND chunk_index = %s", [(model_id, s, i) for s, i in stale])
            cur.executemany(
                "INSERT INTO rag.chunks (embed_model, source, kind, ref, chunk_index, content, "
                "content_hash, metadata, embedding) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s::vector) "
                "ON CONFLICT (embed_model, source, chunk_index) DO UPDATE SET kind = EXCLUDED.kind, "
                "ref = EXCLUDED.ref, content = EXCLUDED.content, content_hash = EXCLUDED.content_hash, "
                "metadata = EXCLUDED.metadata, embedding = EXCLUDED.embedding, indexed_at = now()",
                [(model_id, c.source, c.kind, c.ref, c.index, c.content, c.content_hash,
                  json.dumps(c.metadata, ensure_ascii=False), to_vector_literal(v))
                 for c, v in zip(todo, vectors, strict=True)])
    return report
