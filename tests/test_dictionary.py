"""Diccionario de negocio: validación estructural, invariantes de seguridad y contraste con la BD."""
import copy
import os
import re
from pathlib import Path

import pytest
import yaml

from legacybridge.dictionary import (DEFAULT_PATH, SENSITIVE_TABLES, DictionaryError, load,
                                     normalize, parse)

RAW = yaml.safe_load(DEFAULT_PATH.read_text(encoding="utf-8"))
ROOT = Path(__file__).resolve().parents[1]


def _raw():
    return copy.deepcopy(RAW)


def test_real_dictionary_loads():
    d = load()
    assert d.allowed_tables == {"cliemae", "artmae", "almexi", "pedenc", "peddet"}
    assert d.tables["cliemae"].columns["cliact"].defects == ("D4",)
    assert any(j.involves("almexi") and "D2" in j.note for j in d.joins)


def test_sensitive_tables_never_allowed():
    assert not load().allowed_tables & SENSITIVE_TABLES


@pytest.mark.parametrize("table", sorted(SENSITIVE_TABLES))
def test_rejects_sensitive_table_in_allowlist(table):
    raw = _raw()
    raw["allowed_tables"].append(table)
    with pytest.raises(DictionaryError, match="sensibles"):
        parse(raw)


@pytest.mark.parametrize("mutate,message", [
    (lambda r: r["allowed_tables"].append("artmae2"), "allowed_tables"),
    (lambda r: r["tables"]["artmae"]["columns"]["artuni"].update(defects=["D99"]), "defecto desconocido"),
    (lambda r: r["tables"]["artmae"]["columns"]["artuni"].update(rule="no_existe"), "regla desconocida"),
    (lambda r: r["tables"]["artmae"]["columns"]["artuni"].update(meaning=""), "falta 'meaning'"),
    (lambda r: r["tables"]["artmae"].update(key=["artxxx"]), "llave artxxx"),
    (lambda r: r["joins"].append("peddet.artcve = artmae.nope"), "artmae.nope no existe"),
    (lambda r: r["joins"].append("peddet artcve artmae"), "join ilegible"),
    (lambda r: r["catalogs"].update(nocol={"a": "b"}), "catálogo nocol"),
])
def test_detects_inconsistencies(mutate, message):
    raw = _raw()
    mutate(raw)
    with pytest.raises(DictionaryError, match=message):
        parse(raw)


def test_defect_ids_match_public_doc():
    doc = (ROOT / "docs" / "LEGACY_DEFECTS.md").read_text(encoding="utf-8")
    assert set(load().defects) == set(re.findall(r"^\| (D\d+) \|", doc, re.M))


@pytest.mark.parametrize("text,expected", [
    ("Línea", "linea"), ("  RAZÓN   social ", "razon social"), ("Año", "ano"), ("ÑANDÚ", "nandu"),
])
def test_normalize(text, expected):
    assert normalize(text) == expected


@pytest.mark.integration
def test_dictionary_matches_database_columns():
    """Cada columna documentada existe en la BD y cada columna de la BD está documentada."""
    psycopg = pytest.importorskip("psycopg")
    dsn = os.environ.get("LB_DSN", "postgresql://lb_ro:lb_ro@localhost:5433/legacy")
    try:
        conn = psycopg.connect(dsn, connect_timeout=2)
    except psycopg.OperationalError:
        pytest.skip("Postgres legacy no disponible (make db)")
    with conn:
        rows = conn.execute(
            "SELECT table_name, column_name FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = ANY(%s)",
            (sorted(load().allowed_tables),)).fetchall()
    in_db = {(t, c) for t, c in rows}
    documented = {(c.table, c.name) for c in load().columns()}
    assert documented == in_db


def test_agent_facing_text_never_names_sensitive_tables():
    """Los términos pueden reconocer 'ctrlhis', pero ningún texto que ve el agente lo revela."""
    d = load()
    texts = [r.text for r in d.rules.values()]
    texts += [f"{v['title']} {v['handling']}" for v in d.defects.values()]
    texts += [f"{c.meaning} {' '.join(c.synonyms)}" for c in d.columns()]
    texts += [f"{t.concept} {t.description} {' '.join(t.synonyms)}" for t in d.tables.values()]
    for text in texts:
        assert not any(name in text.lower() for name in SENSITIVE_TABLES), text
