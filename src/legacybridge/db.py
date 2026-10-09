"""Connections to the legacy Postgres, one DSN per database role.

Roles: ``admin`` (schema owner, used only by the seed script), ``reader`` (``lb_reader``, SELECT
only) and ``writer`` (``lb_writer``, approved writes only). DSNs come from the environment or
from ``.env``; nothing is hard-coded here.
"""

from __future__ import annotations

import os

import psycopg
from dotenv import find_dotenv, load_dotenv

ENV_VARS = {"admin": "LB_ADMIN_DSN", "reader": "LB_READER_DSN", "writer": "LB_WRITER_DSN"}


def dsn(role: str) -> str:
    """Return the DSN for ``role``; load ``.env`` first without overriding the environment."""
    load_dotenv(find_dotenv(usecwd=True), override=False)
    var = ENV_VARS[role]
    value = os.environ.get(var)
    if not value:
        raise RuntimeError(f"{var} is not set. Copy .env.example to .env (make db-up does it).")
    return value


def connect(role: str, **kwargs) -> psycopg.Connection:
    return psycopg.connect(dsn(role), **kwargs)
