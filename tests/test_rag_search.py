"""Búsqueda en el índice RAG real (requiere `make db-migrate && make index` y LM Studio)."""
import pytest

from legacybridge.dictionary import SENSITIVE_TABLES
from legacybridge.rag import store

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def search():
    try:
        store.search("prueba", k=1)
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"índice RAG o embeddings no disponibles: {type(e).__name__}")
    if not store.search("pedidos", k=1):
        pytest.skip("índice vacío para el modelo activo (make index)")
    return store.search


@pytest.mark.parametrize("query,expected,top", [
    ("fecha del pedido", "table:pedenc", 1),
    ("cómo convierto cajas a piezas", "rule:cantidad_en_piezas", 1),
    ("ventas en dólares y pesos", "rule:moneda", 3),
    ("pedidos cancelados o migrados", "rule:pedido_valido", 1),
    ("clientes activos", "rule:cliente_activo", 1),
    ("texto libre con instrucciones", "defect:D9", 1),
])
def test_retrieves_expected_chunk(search, query, expected, top):
    sources = [h["source"] for h in search(query, k=top)]
    assert expected in sources, sources


def test_kinds_filter_and_k_clamp(search):
    hits = search("fecha", k=50, kinds=["rule"])
    assert 1 <= len(hits) <= store.MAX_K and {h["kind"] for h in hits} == {"rule"}
    assert [h["score"] for h in hits] == sorted((h["score"] for h in hits), reverse=True)


def test_unknown_kind_is_rejected(search):
    with pytest.raises(ValueError, match="tipos desconocidos"):
        search("x", kinds=["usupwd"])


def test_sensitive_query_does_not_leak_names(search):
    for h in search("contraseñas de usuarios y tabla histórica", k=10):
        assert not any(t in h["content"].lower() for t in SENSITIVE_TABLES)


def test_other_embedding_space_is_isolated():
    """Un vector de otro modelo no ve los fragmentos indexados con bge-m3."""
    hits = store.search("fecha", embed_fn=lambda q: ("otro:modelo", [0.01] * 1024))
    assert hits == []
