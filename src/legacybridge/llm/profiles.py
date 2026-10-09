"""Model profiles from ``config/models.yaml`` (ADR-003) and the synthetic-data guard (ADR-010)."""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, Literal

import psycopg
import yaml
from pydantic import BaseModel

from legacybridge.db import connect as default_connect
from legacybridge.env import load_env

CONFIG_PATH = Path(__file__).resolve().parents[3] / "config" / "models.yaml"
PLACEHOLDER_KEYS = {"", "changeme", "change-me", "your-key-here"}


class ProfileError(RuntimeError):
    """A profile cannot be used: unknown name, missing setting or a refused data policy."""


class Profile(BaseModel):
    name: str
    kind: Literal["openai_compat", "bedrock_converse"]
    base_url: str = ""  # openai_compat only
    model: str
    api_key: str | None = None
    region: str | None = None  # bedrock_converse only
    aws_profile: str | None = None  # None = the default credential chain (a Lambda role, env keys)
    max_tokens: int = 4096
    temperature: float = 0.0
    timeout_s: float = 300.0
    max_concurrency: int = 1
    retries: int = 3
    backoff_base_s: float = 1.0
    synthetic_data_only: bool = False
    price_in_per_mtok: float = 0.0  # USD per million input tokens (0 = local or free tier)
    price_out_per_mtok: float = 0.0


def load_profile(
    name: str, path: Path = CONFIG_PATH, env: Mapping[str, str] | None = None
) -> Profile:
    if env is None:
        load_env()
        env = os.environ
    profiles: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))["profiles"]
    if name not in profiles:
        raise ProfileError(f"unknown profile {name!r}; available: {', '.join(sorted(profiles))}")
    raw = dict(profiles[name])

    def need(key: str) -> str:
        var = raw.pop(key)
        value = env.get(var, "")
        if not value:
            raise ProfileError(f"profile {name!r} needs {var} (see .env.example)")
        return value

    if raw.get("kind") == "bedrock_converse":
        return _bedrock_profile(name, raw, env, need("model_env"))
    base_url, model = need("base_url_env"), need("model_env")
    api_key = None
    if "api_key_env" in raw:
        var = raw["api_key_env"]
        api_key = need("api_key_env")
        if api_key.strip().lower() in PLACEHOLDER_KEYS:
            raise ProfileError(f"profile {name!r}: {var} still has the placeholder; set a real key")
    return Profile(name=name, base_url=base_url, model=model, api_key=api_key, **raw)


def _bedrock_profile(name: str, raw: dict[str, Any], env: Mapping[str, str], model: str) -> Profile:
    """Bedrock needs no URL or key: the region and the credentials come from the AWS environment
    (a named profile on a laptop, the function's role in Lambda)."""
    region = env.get(raw.pop("region_env", ""), "").strip() or raw.pop("default_region", None)
    raw.pop("default_region", None)
    aws_profile = env.get(raw.pop("aws_profile_env", ""), "").strip() or None
    return Profile(name=name, model=model, region=region, aws_profile=aws_profile, **raw)


def assert_data_allowed(profile: Profile, connect: Callable[..., Any] = default_connect) -> None:
    """ADR-010: a profile that sends data to third parties may only run against synthetic data.

    The database says whether it is synthetic (``lb.synthetic``, set by ``db/init``); a database
    that does not say so, or cannot be reached, is treated as real.
    """
    if not profile.synthetic_data_only:
        return
    try:
        with connect("reader", connect_timeout=3) as conn:
            marker = conn.execute("SELECT current_setting('lb.synthetic', true)").fetchone()[0]
    except (psycopg.Error, RuntimeError) as e:
        raise ProfileError(
            f"profile {profile.name!r} sends data to third parties and the database could not be "
            f"checked for the synthetic marker: {e}"
        ) from e
    if marker != "true":
        raise ProfileError(
            f"profile {profile.name!r} is limited to synthetic data (ADR-010) but this database "
            "is not marked synthetic (lb.synthetic); refusing to start"
        )
