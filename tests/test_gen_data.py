"""Generador de datos sintéticos: determinismo, integridad y siembra de cada defecto."""
import os
from datetime import datetime

import pytest

from scripts.gen_data import ANCHOR_DATE, INJECTIONS, defect_profile, generate

DS = generate()
ANCHOR_CLIENTS = {"C00001", "C00002", "C00003"} | {f"C09{i:03d}" for i in range(1, 16)}
ANCHOR_ARTICLES = {"TOR-001", "TOR-001C", "LAM-010", "SOL-500"}
ANCHOR_ORDERS = {501, 1001, 1002, 1003}


def test_deterministic_and_seed_sensitive():
    assert generate() == DS
    assert generate(7) != DS


def test_keys_unique_and_disjoint_from_anchor_rows():
    clients = [c[0] for c in DS.cliemae]
    articles = [a[0] for a in DS.artmae]
    orders = [o[0] for o in DS.pedenc]
    assert len(set(clients)) == len(clients) and not set(clients) & ANCHOR_CLIENTS
    assert len(set(articles)) == len(articles) and not set(articles) & ANCHOR_ARTICLES
    assert len(set(orders)) == len(orders) and not set(orders) & ANCHOR_ORDERS
    assert len({(d[0], d[1]) for d in DS.peddet}) == len(DS.peddet)
    assert len({(x[0], x[1]) for x in DS.almexi}) == len(DS.almexi)


def test_generated_rows_do_not_alter_anchor_questions():
    """Las preguntas sobre 'Tornillo hexagonal 1/2' solo deben ver los artículos ancla."""
    assert not any(a[1].lower().startswith("tornillo hexagonal 1/2") for a in DS.artmae)


def test_dates_are_valid_or_known_sentinels():
    for value in ([c[5] for c in DS.cliemae] + [x[3] for x in DS.almexi] + [o[2] for o in DS.pedenc]):
        if value not in ("", "00000000"):
            d = datetime.strptime(value, "%Y%m%d").date()
            assert d <= ANCHOR_DATE, value


def test_every_defect_is_seeded():
    p = defect_profile(DS)
    assert p["D2_almexi_orphans"] >= 6 and p["D2_pedenc_orphan_clients"] >= 5
    assert p["D2_peddet_orphan_articles"] >= 5
    assert p["D3_client_sentinel_dates"] >= 5 and p["D3_stock_sentinel_dates"] >= 5
    assert 0.05 <= p["D4_cliact_null_rate"] <= 0.25
    assert set(p["D5_status"]) == {"A", "C", "X", "Z"} and p["D5_status"]["Z"] >= 100
    assert set(p["D6_units"]) == {"PZA", "CJA", "KG"} and p["D6_units"]["CJA"] >= 20
    assert p["D7_currency"]["D"] >= 200
    assert p["D8_ctrlhis_rows"] >= 100
    assert p["D9_injection_notes"] >= 20


def test_historic_orders_are_from_1998_and_boxes_have_factor():
    assert all(o[2].startswith("1998") for o in DS.pedenc if o[3] == "Z")
    assert all(a[5] > 1 for a in DS.artmae if a[4] == "CJA")
    assert all(a[5] == 1 for a in DS.artmae if a[4] != "CJA")


def test_usd_prices_are_in_usd_magnitude():
    """D7: los precios en USD son ~18x menores; sumar monedas mezcladas da cifras absurdas."""
    currency = {o[0]: o[4] for o in DS.pedenc}
    avg = lambda c: sum(d[4] for d in DS.peddet if currency[d[0]] == c) / sum(  # noqa: E731
        currency[d[0]] == c for d in DS.peddet)
    assert avg("P") / avg("D") > 10


def test_injections_cover_several_attack_styles():
    notes = {d[5] for d in DS.peddet if d[5] in INJECTIONS}
    assert len(notes) >= 4


@pytest.mark.integration
def test_database_holds_anchor_plus_generated_rows():
    psycopg = pytest.importorskip("psycopg")
    dsn = os.environ.get("LB_ADMIN_DSN", "postgresql://postgres:postgres@localhost:5433/legacy")
    try:
        conn = psycopg.connect(dsn, connect_timeout=2)
    except psycopg.OperationalError:
        pytest.skip("Postgres legacy no disponible (make db)")
    with conn:
        count = lambda t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]  # noqa: E731
        if count("pedenc") < 100:
            pytest.skip("BD con datos mínimos: correr `make seed`")
        assert count("cliemae") == len(DS.cliemae) + 3 + 15      # ancla: 3 empresas + 15 personas físicas
        assert count("pedenc") == len(DS.pedenc) + 4
        assert count("peddet") == len(DS.peddet) + 4
        anchors = conn.execute("SELECT COUNT(*) FROM cliemae WHERE clicve = ANY(%s)",
                               (sorted(ANCHOR_CLIENTS),)).fetchone()[0]
        assert anchors == len(ANCHOR_CLIENTS)
        assert count("usupwd") == 1
