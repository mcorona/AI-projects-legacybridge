"""Lógica de las tools de schema_explorer (diccionario real, BD simulada)."""
import pytest

from legacybridge.mcp_servers.schema_explorer import SchemaExplorer, fetch_db_columns

FAKE_DB = {
    "pedenc": [{"name": "pednum", "type": "integer", "nullable": True},
               {"name": "clicve", "type": "character(6)", "nullable": True},
               {"name": "pedfec", "type": "character varying(8)", "nullable": True},
               {"name": "pedest", "type": "character(1)", "nullable": True},
               {"name": "pedmon", "type": "character(1)", "nullable": True}],
}


@pytest.fixture
def ex():
    return SchemaExplorer(db_columns=lambda t: FAKE_DB[t])


def test_list_tables(ex):
    out = ex.list_tables()
    names = [t["table"] for t in out["tables"]]
    assert names == sorted(names) == ["almexi", "artmae", "cliemae", "peddet", "pedenc"]
    assert "D2" in out["note"]


def test_describe_table_merges_db_and_dictionary(ex):
    out = ex.describe_table("pedenc")
    cols = {c["name"]: c for c in out["columns"]}
    assert list(cols) == ["pednum", "clicve", "pedfec", "pedest", "pedmon"]
    assert cols["pedfec"]["type"] == "character varying(8)"
    assert cols["pedfec"]["defects"] == ["D3"] and cols["pedfec"]["rule"] == "fecha"
    assert cols["pedest"]["catalog"]["Z"].startswith("Migrado")
    assert {"on": "pedenc.clicve = cliemae.clicve", "note": ""} in out["joins"]
    assert set(out["rules"]) == {"fecha", "pedido_valido", "moneda"}
    assert list(out["defect_warnings"]) == ["D2", "D3", "D5", "D7"]
    assert "db_error" not in out


@pytest.mark.parametrize("name", ["PEDENC", " public.pedenc "])
def test_describe_table_normalizes_name(ex, name):
    assert ex.describe_table(name)["table"] == "pedenc"


@pytest.mark.parametrize("name", ["usupwd", "ctrlhis", "pg_catalog.pg_user", "no_existe"])
def test_describe_table_hides_forbidden_and_unknown_tables(ex, name):
    out = ex.describe_table(name)
    assert out["error"] == "table_not_allowed"
    assert "usupwd" not in out["allowed_tables"] and "ctrlhis" not in out["allowed_tables"]
    assert set(out) == {"error", "table", "allowed_tables"}   # sin columnas ni pistas


def test_describe_table_degrades_without_db():
    def down(_):
        raise ConnectionError("connection refused\nmore detail")
    out = SchemaExplorer(db_columns=down).describe_table("almexi")
    assert out["db_error"] == "ConnectionError: connection refused"
    cols = {c["name"]: c for c in out["columns"]}
    assert set(cols) == {"almcve", "artcve", "exicant", "exiult"}
    assert cols["exicant"]["type"] is None and cols["artcve"]["defects"] == ["D2"]


@pytest.mark.parametrize("concept,expected", [
    ("fecha del pedido", "pedenc.pedfec"),
    ("moneda", "pedenc.pedmon"),
    ("dólares", "pedenc.pedmon"),
    ("RFC", "cliemae.clirfc"),
    ("piezas por caja", "artmae.artfac"),
    ("existencia", "almexi.exicant"),
    ("linea", "artmae.artlin"),            # sin acento
    ("cliente activo", "cliemae.cliact"),
    ("pedidos cancelados", "pedenc.pedest"),
    ("clinom", "cliemae.clinom"),          # nombre críptico exacto
    ("precio", "peddet.detprec"),
    ("almacén", "almexi.almcve"),
    ("observaciones", "peddet.detobs"),
])
def test_find_columns_top_match(ex, concept, expected):
    top = ex.find_columns(concept)["matches"][0]
    assert f"{top['table']}.{top['column']}" == expected


def test_find_columns_ranks_and_limits(ex):
    out = ex.find_columns("fecha", limit=3)
    scores = [m["score"] for m in out["matches"]]
    assert len(scores) == 3 and scores == sorted(scores, reverse=True)
    assert all(m["defects"] == ["D3"] for m in out["matches"])


@pytest.mark.parametrize("concept", ["contraseña", "password", "usupwd", "zzz"])
def test_find_columns_no_match(ex, concept):
    out = ex.find_columns(concept)
    assert out["matches"] == [] and "hint" in out


@pytest.mark.parametrize("term,rules,catalogs", [
    ("cliente activo", {"cliente_activo"}, set()),
    ("pedido válido", {"pedido_valido"}, {"pedest"}),
    ("estatus", {"pedido_valido"}, {"pedest"}),
    ("moneda", {"moneda", "importe_partida"}, {"pedmon"}),
    ("fechas", {"fecha"}, set()),
    ("piezas", {"cantidad_en_piezas"}, {"artuni"}),
    ("Monterrey", set(), {"almcve"}),
    ("línea", set(), {"artlin"}),
])
def test_get_business_rule(ex, term, rules, catalogs):
    out = ex.get_business_rule(term)
    assert out["matched"] is True
    assert set(out["rules"]) == rules and set(out["catalogs"]) == catalogs


def test_get_business_rule_no_match_lists_options(ex):
    out = ex.get_business_rule("contraseña")
    assert out["matched"] is False
    assert "pedido_valido" in out["available_rules"] and "pedest" in out["available_catalogs"]
    assert "rules" not in out


@pytest.mark.integration
def test_fetch_db_columns_against_postgres():
    try:
        cols = fetch_db_columns("pedenc")
    except Exception:  # noqa: BLE001
        pytest.skip("Postgres legacy no disponible (make db)")
    assert [c["name"] for c in cols] == ["pednum", "clicve", "pedfec", "pedest", "pedmon"]
    assert cols[2]["type"] == "character varying(8)"


@pytest.mark.integration
def test_lb_ro_cannot_see_sensitive_columns():
    """Aunque se pida por nombre, information_schema no muestra tablas sin grant a lb_ro."""
    try:
        cols = fetch_db_columns("usupwd")
    except Exception:  # noqa: BLE001
        pytest.skip("Postgres legacy no disponible (make db)")
    assert cols == []
