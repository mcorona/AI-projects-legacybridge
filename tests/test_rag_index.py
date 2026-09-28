"""Construcción de fragmentos, redacción, chunking de Markdown e indexado incremental."""
import os
from collections import Counter

import pytest

from legacybridge.dictionary import SENSITIVE_TABLES, load
from legacybridge.rag.chunking import Chunk, chunk_markdown
from legacybridge.rag.sources import build_chunks, redact
from legacybridge.rag.store import plan_sync, sync_index

CHUNKS = build_chunks()


def test_chunk_inventory():
    kinds = Counter(c.kind for c in CHUNKS)
    d = load()
    assert kinds == {"ddl": len(d.allowed_tables), "table": len(d.tables), "rule": len(d.rules),
                     "catalog": len(d.catalogs), "defect": len(d.defects)}
    assert {c.ref for c in CHUNKS if c.kind == "defect"} == set(d.defects)
    assert len({(c.source, c.index) for c in CHUNKS}) == len(CHUNKS)


def test_ddl_only_for_allowed_tables():
    assert {c.ref for c in CHUNKS if c.kind == "ddl"} == set(load().allowed_tables)
    for c in CHUNKS:
        assert "GRANT" not in c.content and "PASSWORD" not in c.content.upper(), c.source


def test_index_never_names_sensitive_tables():
    for c in CHUNKS:
        low = c.content.lower()
        assert not any(t in low for t in SENSITIVE_TABLES), c.source


def test_redact_is_case_insensitive_and_word_bounded():
    assert redact("SELECT * FROM UsuPwd") == "SELECT * FROM [tabla de credenciales restringida]"
    assert redact("ctrlhistorico") == "ctrlhistorico"


def test_table_card_carries_defects_rules_and_joins():
    card = next(c.content for c in CHUNKS if c.source == "table:pedenc")
    assert "pedfec: fecha del pedido" in card and "[D3; regla fecha]" in card
    assert "peddet.pednum = pedenc.pednum" in card


def test_chunk_markdown_ported_behavior():
    md = "# Manual\n> nota de control\nIntro breve.\n\n## Fechas\nUsar TO_DATE.\n\n## Vacía\n\n## Monedas\n" \
         + "\n\n".join(f"Párrafo {i} " + "x" * 80 for i in range(30))
    cs = chunk_markdown(md, "doc:manual", max_chars=500, overlap=50)
    assert cs[0].ref == "Introducción" and "nota de control" not in cs[0].content
    assert cs[1].content.startswith("Documento: Manual > Fechas")
    assert all(c.kind == "doc" for c in cs) and [c.index for c in cs] == list(range(len(cs)))
    assert sum(c.ref == "Monedas" for c in cs) > 1          # sección larga partida
    assert all(len(c.content) < 700 for c in cs)


def test_plan_sync():
    a, b, c = Chunk("s:a", "rule", "A"), Chunk("s:b", "rule", "B"), Chunk("s:c", "rule", "C")
    existing = {("s:a", 0): a.content_hash, ("s:b", 0): "hash-viejo", ("s:z", 0): "x"}
    new, changed, stale = plan_sync([a, b, c], existing)
    assert new == [c] and changed == [b] and stale == [("s:z", 0)]
    with pytest.raises(ValueError, match="duplicados"):
        plan_sync([a, a], {})


@pytest.mark.integration
def test_sync_index_incremental_roundtrip():
    """Indexa con un modelo de prueba aislado y lo limpia al final (solo toca rag.chunks)."""
    psycopg = pytest.importorskip("psycopg")
    dsn = os.environ.get("LB_RAG_DSN", "postgresql://lb_rag_rw:lb_rag_rw@localhost:5433/legacy")
    try:
        psycopg.connect(dsn, connect_timeout=2).close()
    except psycopg.OperationalError:
        pytest.skip("índice RAG no disponible (make db-migrate)")
    model, calls = "test:fake-embedder", []

    def fake_embed(texts):
        calls.append(len(texts))
        return [[0.001 * (i + 1)] * 1024 for i in range(len(texts))]

    chunks = CHUNKS[:4]
    try:
        r1 = sync_index(chunks, fake_embed, model, dsn)
        assert (r1.added, r1.unchanged) == (4, 0)
        r2 = sync_index(chunks, fake_embed, model, dsn)
        assert (r2.added, r2.updated, r2.unchanged) == (0, 0, 4) and calls == [4]
        edited = [Chunk(chunks[0].source, chunks[0].kind, chunks[0].content + " (v2)", chunks[0].ref)]
        r3 = sync_index(edited + chunks[1:3], fake_embed, model, dsn)
        assert (r3.updated, r3.deleted, r3.unchanged) == (1, 1, 2) and calls == [4, 1]
    finally:
        cleanup = sync_index([], fake_embed, model, dsn)
    assert cleanup.deleted in (3, 4)
