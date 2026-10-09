"""Shared pytest setup: Postgres fixtures that skip when the database is unreachable."""

import psycopg
import pytest

from legacybridge.db import connect


@pytest.fixture(scope="session")
def db_available() -> bool:
    try:
        with connect("reader", connect_timeout=3):
            return True
    except (psycopg.OperationalError, RuntimeError):
        return False


@pytest.fixture
def reader(db_available):
    if not db_available:
        pytest.skip("legacy Postgres is not reachable (make db-up && make seed)")
    with connect("reader", autocommit=True) as conn:
        yield conn


@pytest.fixture
def writer(db_available):
    if not db_available:
        pytest.skip("legacy Postgres is not reachable (make db-up && make seed)")
    with connect("writer") as conn:
        yield conn
        conn.rollback()


@pytest.fixture
def admin(db_available):
    if not db_available:
        pytest.skip("legacy Postgres is not reachable (make db-up && make seed)")
    with connect("admin") as conn:
        yield conn
        conn.rollback()
