"""Database credentials from AWS Secrets Manager (ADR-014).

The worker needs a DSN per database role, and a DSN holds a password. Each role has its own secret,
the host and port are plain settings, and the DSNs are put in the environment once at start so
``legacybridge.db`` works unchanged. Nothing is written to disk or to the logs.
"""

from __future__ import annotations

import json
import os
from collections.abc import MutableMapping
from typing import Any
from urllib.parse import quote

import boto3

ROLES = {"reader": "LB_READER_DSN", "proposer": "LB_PROPOSER_DSN"}


def secret_json(client: Any, secret_id: str) -> dict[str, str]:
    return json.loads(client.get_secret_value(SecretId=secret_id)["SecretString"])


def build_dsn(secret: dict[str, str], host: str, port: str, dbname: str) -> str:
    """A DSN that requires TLS. The password is percent-encoded: a generated one may hold ``@``."""
    user, password = quote(secret["username"], safe=""), quote(secret["password"], safe="")
    return f"postgresql://{user}:{password}@{host}:{port}/{dbname}?sslmode=require"


def load_runtime_env(env: MutableMapping[str, str] | None = None, client: Any = None) -> list[str]:
    """Set ``LB_<ROLE>_DSN`` from ``LB_<ROLE>_SECRET`` for every role that has a secret and no DSN
    yet. Returns the roles it set. A missing host is an error: a DSN without it would only fail
    later and further from the cause."""
    env = os.environ if env is None else env
    wanted = [r for r in ROLES if env.get(f"LB_{r.upper()}_SECRET") and not env.get(ROLES[r])]
    if not wanted:
        return []
    host = env.get("LB_DB_HOST", "")
    if not host:
        raise RuntimeError("LB_DB_HOST is not set; the worker cannot reach the database")
    client = client or boto3.client("secretsmanager")
    for role in wanted:
        secret = secret_json(client, env[f"LB_{role.upper()}_SECRET"])
        env[ROLES[role]] = build_dsn(
            secret, host, env.get("LB_DB_PORT", "5432"), env.get("LB_DB_NAME", "legacy")
        )
    return wanted


def read_token(env: MutableMapping[str, str] | None = None, client: Any = None) -> str | None:
    """The bearer token of the public API: its value, from the secret ``LB_ASK_TOKEN_SECRET``."""
    env = os.environ if env is None else env
    if env.get("LB_ASK_TOKEN"):
        return env["LB_ASK_TOKEN"]
    secret_id = env.get("LB_ASK_TOKEN_SECRET")
    if not secret_id:
        return None
    client = client or boto3.client("secretsmanager")
    return client.get_secret_value(SecretId=secret_id)["SecretString"].strip()
