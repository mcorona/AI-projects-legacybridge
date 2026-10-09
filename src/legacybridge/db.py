"""Connections to the legacy Postgres, one DSN per database role.

Roles: ``admin`` (schema owner, used only by the seed script), ``reader`` (``lb_reader``, SELECT
only), ``writer`` (``lb_writer``, approved writes only) and ``proposer`` (``lb_proposer``, can only
queue write requests). DSNs come from the environment or from ``.env``; nothing is hard-coded here.
"""

from __future__ import annotations

import os
from pathlib import Path

import psycopg
from dotenv import find_dotenv, load_dotenv

REPO_ENV = Path(__file__).resolve().parents[2] / ".env"
ENV_VARS = {
    "admin": "LB_ADMIN_DSN",
    "reader": "LB_READER_DSN",
    "writer": "LB_WRITER_DSN",
    "proposer": "LB_PROPOSER_DSN",
}


def dsn(role: str) -> str:
    """Return the DSN for ``role``; load ``.env`` first without overriding the environment."""
    # The cwd of an MCP client is not always the repo root, so fall back to the repo's own .env.
    load_dotenv(find_dotenv(usecwd=True) or REPO_ENV, override=False)
    var = ENV_VARS[role]
    value = os.environ.get(var)
    if not value:
        raise RuntimeError(f"{var} is not set. Copy .env.example to .env (make db-up does it).")
    return value


def connect(role: str, **kwargs) -> psycopg.Connection:
    return psycopg.connect(dsn(role), **kwargs)
