"""Phase 1 data checks: the deterministic generator (no database) and the database itself
(schema without constraints, planted data, role privileges; marked ``integration``)."""

import psycopg
import pytest
from psycopg import errors

from scripts.seed import (
    N_CLIENTS,
    N_INVOICES,
    N_PRODUCTS,
    TABLES,
    defect_profile,
    generate,
    is_valid_date,
    parse_amount,
)

DS = generate()
PROFILE = defect_profile(DS)

# (column index, max length) per text column, from db/init/01_facturacion.sql
LIMITS = {
    "clientes": [(0, 10), (1, 60), (2, 13), (3, 3), (4, 15), (5, 60), (6, 1), (7, 8)],
    "productos": [(0, 8), (1, 80), (2, 2), (4, 1)],
    "faccab": [
        (0, 2),
        (1, 10),
        (2, 10),
        (3, 13),
        (4, 8),
        (5, 15),
        (6, 15),
        (7, 15),
        (8, 12),
        (9, 1),
        (10, 14),
    ],
    "facdet": [(0, 2), (1, 10), (3, 8), (5, 12), (6, 15), (7, 100), (8, 1)],
}


# --- generator (no database) -------------------------------------------------------------


def test_deterministic_and_seed_sensitive():
    assert generate(42) == DS
    assert generate(7) != DS


def test_sizes_match_the_plan():
    assert len(DS.clientes) == N_CLIENTS and len(DS.productos) == N_PRODUCTS
    assert N_INVOICES <= len(DS.faccab) <= N_INVOICES * 1.03  # plus re-captured folios
    assert 18_000 <= len(DS.facdet) <= 22_000


@pytest.mark.parametrize("table", TABLES)
def test_text_values_fit_their_columns(table):
    for row in getattr(DS, table):
        for idx, limit in LIMITS[table]:
            value = row[idx]
            assert value is None or len(value) <= limit, (table, idx, value)


def test_every_defect_is_planted():
    for defect, counts in PROFILE.items():
        if defect == "FAC-03":
            continue
        assert all(n > 0 for key, n in counts.items() if key != "lines"), (defect, counts)
    status = PROFILE["FAC-03"]
    for key in ("clientes.B", "productos.B", "faccab.B", "faccab.C", "facdet.B"):
        assert status[key] > 0, key


def test_planted_rates_are_in_the_designed_range():
    lines = PROFILE["FAC-01"]
    assert 0.03 <= lines["lines_with_left_padded_folio"] / lines["lines"] <= 0.07
    assert 0.005 <= PROFILE["FAC-05"]["invoices_with_bad_date"] / len(DS.faccab) <= 0.02
    assert 20 <= PROFILE["FAC-09"]["duplicated_folios"] <= 80
    assert PROFILE["FAC-10"] == {"products_with_instructions": 5, "lines_with_instructions": 25}


def test_amounts_are_text_but_parseable():
    for header in DS.faccab:
        for text in header[5:8]:
            assert parse_amount(text) >= 0


def test_ref1_changes_meaning_with_the_year():
    for header in DS.faccab:
        year = header[4][:4] if is_valid_date(header[4]) else None
        if year is None:
            continue
        ref1 = header[8]
        if year < "2001":
            assert ref1.startswith("PED"), header
        elif year <= "2003":
            assert ref1 in ("MXN", "USD"), header
        else:
            assert ref1.startswith("AG"), header


def test_recaptured_folio_keeps_the_correct_row_last():
    by_folio = {}
    for header in DS.faccab:
        by_folio.setdefault((header[0], header[1]), []).append(header)
    twins = [rows for rows in by_folio.values() if len(rows) > 1]
    assert twins
    for older, newer in (sorted(rows, key=lambda h: h[10]) for rows in twins):
        assert older[10] < newer[10]
        assert older[9] == "V"


# --- database (integration) --------------------------------------------------------------


@pytest.mark.integration
def test_schema_has_no_constraints_like_the_legacy_system(reader):
    n = reader.execute(
        "SELECT count(*) FROM pg_constraint c JOIN pg_namespace n ON n.oid = c.connamespace "
        "WHERE n.nspname = 'public' AND c.contype IN ('p', 'f', 'u')"
    ).fetchone()[0]
    assert n == 0


@pytest.mark.integration
def test_database_holds_the_seed_42_data(reader):
    for table in TABLES:
        n = reader.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        assert n == len(getattr(DS, table)), f"{table}: run `make seed`"


@pytest.mark.integration
def test_reader_is_read_only_and_time_boxed(reader):
    assert reader.execute("SHOW statement_timeout").fetchone()[0] == "5s"
    assert reader.execute("SHOW default_transaction_read_only").fetchone()[0] == "on"
    for sql in (
        "INSERT INTO clientes (clave) VALUES ('X')",
        "UPDATE clientes SET status = 'B'",
        "DELETE FROM faccab",
        "TRUNCATE facdet",
        "CREATE TABLE evil (x int)",
    ):
        with pytest.raises(psycopg.Error):
            reader.execute(sql)


@pytest.mark.integration
def test_reader_is_refused_by_privileges_even_if_read_only_is_switched_off(reader):
    reader.execute("SET default_transaction_read_only = off")
    with pytest.raises(errors.InsufficientPrivilege):
        reader.execute("UPDATE clientes SET status = 'B'")
    with pytest.raises(errors.InsufficientPrivilege):
        reader.execute("INSERT INTO productos (clave) VALUES ('X')")


@pytest.mark.integration
def test_writer_can_update_only_the_allowed_columns(writer):
    writer.execute("UPDATE clientes SET status = 'B' WHERE clave = '0000000001'")
    writer.execute("UPDATE clientes SET telefono = '3300000000' WHERE clave = '0000000001'")
    writer.execute("UPDATE productos SET status = 'B' WHERE clave = 'P0000001'")
    writer.rollback()


@pytest.mark.integration
@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE clientes SET nombre = 'X' WHERE clave = '0000000001'",
        "UPDATE clientes SET rfc = 'X' WHERE clave = '0000000001'",
        "UPDATE productos SET precio = 1 WHERE clave = 'P0000001'",
        "UPDATE faccab SET status = 'B'",
        "UPDATE facdet SET status = 'B'",
        "DELETE FROM clientes WHERE clave = '0000000001'",
        "INSERT INTO clientes (clave) VALUES ('X')",
        "TRUNCATE clientes",
        "CREATE TABLE evil (x int)",
    ],
)
def test_writer_is_refused_everything_else(writer, sql):
    with pytest.raises(errors.InsufficientPrivilege):
        writer.execute(sql)
