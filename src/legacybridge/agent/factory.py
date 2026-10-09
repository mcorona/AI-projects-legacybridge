"""Builds an agent from a profile name (CLI and API share this)."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from legacybridge.agent.backends import McpToolBackend
from legacybridge.agent.orchestrator import Agent
from legacybridge.agent.schemas import AgentConfig
from legacybridge.llm.base import LLMClient
from legacybridge.llm.openai_compat import OpenAICompatClient
from legacybridge.llm.profiles import assert_data_allowed, load_profile


def build_llm(profile_name: str) -> LLMClient:
    """The client for a profile. Raises ProfileError for an unusable profile, including a profile
    that sends data to third parties being pointed at a database that is not synthetic (ADR-010)."""
    profile = load_profile(profile_name)
    assert_data_allowed(profile)
    return OpenAICompatClient(profile)


@asynccontextmanager
async def open_agent(
    profile: str,
    *,
    backend: str = "mcp",
    config: AgentConfig | None = None,
    llm: LLMClient | None = None,
    escalation_profile: str | None = None,
    escalation_llm: LLMClient | None = None,
) -> AsyncIterator[Agent]:
    client = llm or build_llm(profile)
    name = escalation_profile or os.environ.get("LB_ESCALATION_PROFILE", "").strip() or None
    second = escalation_llm or (build_llm(name) if name and name != profile else None)
    tools = McpToolBackend.stdio() if backend == "mcp" else McpToolBackend.in_memory()
    async with tools as opened:
        yield Agent(client, opened, config=config, escalation_llm=second)
