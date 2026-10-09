"""The policy, the ontology and the real database privileges must tell the same story."""

import re
from pathlib import Path

import pytest

from legacybridge import policy
from legacybridge.mcp_server.schema import schema_from_ddl
from legacybridge.semantic.loader import load_ontology

SCHEMA = schema_from_ddl()
ONTOLOGY = load_ontology()
DEFECTS_DOC = Path(__file__).resolve().parents[1] / "docs" / "legacy-defects.md"


def test_the_ontology_covers_every_defect_in_the_catalogue():
    documented = set(re.findall(r"\b(?:FAC|SIC)-\d{2}\b", DEFECTS_DOC.read_text("utf-8")))
    assert {d.id for d in ONTOLOGY.defects} == documented
    assert len(documented) == 28


def test_every_defect_column_exists_in_the_schema():
    for defect in ONTOLOGY.defects:
        for qualified in defect.columns:
            table, column = qualified.split(".")
            assert column in SCHEMA[table], f"{defect.id}: {qualified}"


def test_every_term_points_to_known_defects_and_every_table_is_documented():
    ids = {d.id for d in ONTOLOGY.defects}
    assert all(set(t.defects) <= ids for t in ONTOLOGY.terms)
    assert {t.name for t in ONTOLOGY.tables} == set(SCHEMA)


def test_writable_columns_exist_and_preview_columns_are_readable():
    for table, columns in policy.WRITABLE.items():
        assert columns <= set(SCHEMA[table])
        assert set(policy.PREVIEW_COLUMNS[table]) <= set(SCHEMA[table])
    assert set(policy.PREVIEW_COLUMNS) == set(policy.WRITABLE)


def test_allowed_values_cover_only_writable_columns():
    for table, column in {*policy.ALLOWED_VALUES, *policy.VALUE_PATTERNS}:
        assert column in policy.WRITABLE[table]


@pytest.mark.parametrize(
    "table,update,fragment",
    [
        ("clientes", {"status": "X"}, "must be one of"),
        ("clientes", {"telefono": "12ab"}, "invalid format"),
        ("clientes", {"email": "a@b.com'; DROP"}, "invalid format"),
        ("invart", {"activo": "Q"}, "must be one of"),
        ("clientes", {"status": 1}, "must be a string"),
    ],
)
def test_invalid_write_values_are_reported(table, update, fragment):
    assert any(fragment in p for p in policy.validate_write_values(table, update))


def test_valid_write_values_pass():
    assert policy.validate_write_values("clientes", {"status": "B", "telefono": "3312345678"}) == []
    assert policy.validate_write_values("clientes", {"email": "compras@example.com"}) == []


def test_injection_detector_flags_the_planted_texts_and_not_normal_notes():
    from scripts.seed import LINE_INJECTIONS, PRODUCT_INJECTIONS
    from scripts.seed_sicpt import INJECTIONS

    for text in [*PRODUCT_INJECTIONS, *LINE_INJECTIONS, *INJECTIONS]:
        assert policy.looks_like_instructions(text), text
    for text in ["Entregar en anden 2", "Urgente", "Precio especial autorizado por gerencia",
                 "Tornillo hexagonal 1/2", None, 42]:  # fmt: skip
        assert not policy.looks_like_instructions(text), text


# --------------------------------------------------------------------------- real privileges
@pytest.mark.integration
def test_lb_writer_can_update_exactly_the_columns_in_policy(admin):
    rows = admin.execute(
        "SELECT table_name, column_name FROM information_schema.column_privileges "
        "WHERE grantee = 'lb_writer' AND privilege_type = 'UPDATE' AND table_schema = 'public'"
    ).fetchall()
    granted: dict[str, set[str]] = {}
    for table, column in rows:
        granted.setdefault(table, set()).add(column)
    assert granted == policy.WRITABLE


@pytest.mark.integration
def test_lb_writer_has_no_insert_delete_or_truncate(admin):
    rows = admin.execute(
        "SELECT DISTINCT privilege_type FROM information_schema.table_privileges "
        "WHERE grantee = 'lb_writer'"
    ).fetchall()
    assert {r[0] for r in rows} <= {"SELECT"}  # column-level UPDATE is not listed here


@pytest.mark.integration
def test_lb_reader_has_only_select_on_the_business_tables(admin):
    rows = admin.execute(
        "SELECT table_schema, table_name, privilege_type FROM information_schema.table_privileges "
        "WHERE grantee = 'lb_reader'"
    ).fetchall()
    assert {r[2] for r in rows} == {"SELECT"}
    assert {r[0] for r in rows} == {"public"}
    assert {r[1] for r in rows} == set(SCHEMA)


@pytest.mark.integration
def test_lb_proposer_can_only_insert_and_read_the_approval_queue(admin):
    rows = admin.execute(
        "SELECT table_schema, table_name, privilege_type FROM information_schema.table_privileges "
        "WHERE grantee = 'lb_proposer'"
    ).fetchall()
    assert set(rows) == {
        ("lb_ops", "write_requests", "INSERT"),
        ("lb_ops", "write_requests", "SELECT"),
    }
