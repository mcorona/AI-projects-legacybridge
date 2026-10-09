"""Loads ``.env`` once, without overriding variables that are already set."""

from __future__ import annotations

from pathlib import Path

from dotenv import find_dotenv, load_dotenv

REPO_ENV = Path(__file__).resolve().parents[2] / ".env"


def load_env() -> None:
    """The cwd of an MCP client or a service is not always the repo root, so fall back to the
    repo's own ``.env``."""
    load_dotenv(find_dotenv(usecwd=True) or REPO_ENV, override=False)
