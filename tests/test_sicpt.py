"""Stand-in sicpt inventory: deterministic generator (no database) and the database itself
(planted data, role privileges; marked ``integration``)."""

import pytest
from psycopg import errors

from scripts.seed import generate
from scripts.seed_sicpt import (
    INJECTIONS,
    LATEST_CUT,
    SICPT_TABLES,
    defect_profile,
    generate_sicpt,
)

PRODUCTS = generate().productos
DS = generate_sicpt(42, PRODUCTS)
PROFILE = defect_profile(DS)

# (column index, max length) per text column, from db/init/02_sicpt.sql
LIMITS = {
    "invart": [(0, 12), (1, 60), (2, 3), (4, 12), (5, 1), (6, 8)],
    "invalm": [(0, 3), (1, 30), (2, 1)],
    "invexi": [(0, 3), (1, 12), (3, 6)],
    "invprv": [(0, 6), (1, 60), (2, 13), (3, 1)],
    "invmov": [(1, 8), (2, 3), (3, 12), (4, 1), (6, 3), (7, 6), (8, 20), (9, 1), (10, 100)],
}


def test_deterministic_and_seed_sensitive():
    assert generate_sicpt(42, PRODUCTS) == DS
    assert generate_sicpt(7, PRODUCTS) != DS


def test_does_not_disturb_the_invoicing_data():
    assert generate() == generate()
    assert generate_sicpt(42) == DS


@pytest.mark.parametrize("table", SICPT_TABLES)
def test_text_values_fit_their_columns(table):
    for row in getattr(DS, table):
        for idx, limit in LIMITS[table]:
            value = row[idx]
            assert value is None or len(value) <= limit, (table, idx, value)


def test_all_eighteen_defects_are_planted():
    assert sorted(PROFILE) == [f"SIC-{n:02d}" for n in range(1, 19)]
    for defect, counts in PROFILE.items():
        assert all(n > 0 for n in counts.values()), (defect, counts)


def test_every_cancelled_movement_has_one_reversal():
    cancelled = {m[0] for m in DS.invmov if m[9] == "C"}
    reversed_ = {int(m[8].split("-")[1]) for m in DS.invmov if (m[8] or "").startswith("CANC-")}
    assert cancelled == reversed_


def test_type_codes_and_sign_convention_change_on_different_dates():
    """Type letters switch I/O -> E/S in 2001; the sign of issues switches in 2002 (SIC-11/12)."""
    for m in DS.invmov:
        if (m[8] or "").startswith("CANC-"):
            continue
        year = m[1][:4]
        assert (m[4] in ("I", "O")) == (year < "2001") or m[4] in ("T", "A"), m
        if m[4] in ("O", "S"):
            assert (m[5] < 0) == (year <= "2001"), m


def test_stock_has_old_cuts_and_a_latest_cut():
    assert {e[3] for e in DS.invexi} >= {LATEST_CUT, "200510"}


def test_injections_are_planted_as_data():
    assert PROFILE["SIC-17"]["movements_with_instructions"] == 25
    assert all(len(text) <= 100 for text in INJECTIONS)


# --- database (integration) --------------------------------------------------------------


@pytest.mark.integration
def test_database_holds_the_sicpt_seed(reader):
    for table in SICPT_TABLES:
        n = reader.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        assert n == len(getattr(DS, table)), f"{table}: run `make seed`"


@pytest.mark.integration
def test_sicpt_tables_have_no_constraints(reader):
    n = reader.execute(
        "SELECT count(*) FROM pg_constraint c JOIN pg_class t ON t.oid = c.conrelid "
        "WHERE t.relname LIKE 'inv%'"
    ).fetchone()[0]
    assert n == 0


@pytest.mark.integration
def test_cross_system_key_needs_a_conversion(reader):
    direct = reader.execute("SELECT count(*) FROM invart a JOIN productos p ON p.clave = a.clave")
    mapped = reader.execute(
        "SELECT count(*) FROM invart a JOIN productos p "
        "ON a.clave = 'AR' || LPAD(SUBSTR(p.clave, 2)::int::text, 5, '0')"
    )
    assert direct.fetchone()[0] == 0
    assert mapped.fetchone()[0] == len(PRODUCTS)


@pytest.mark.integration
def test_writer_may_only_toggle_article_activity(writer):
    writer.execute("UPDATE invart SET activo = 'N' WHERE clave = 'AR00001'")
    writer.rollback()
    for sql in (
        "UPDATE invart SET costo = '1' WHERE clave = 'AR00001'",
        "UPDATE invmov SET cant = 0",
        "UPDATE invexi SET cant = 0",
        "DELETE FROM invmov",
        "INSERT INTO invprv (clave) VALUES ('X')",
    ):
        with pytest.raises(errors.InsufficientPrivilege):
            writer.execute(sql)
        writer.rollback()
