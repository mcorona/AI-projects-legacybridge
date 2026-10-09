"""How the agent reaches the tools (ADR-002).

Both backends speak MCP, so the agent sees the same tool schemas, validation and errors either
way. ``stdio`` starts the real server as a subprocess, exactly as Claude Code does; ``in_memory``
connects to a server object in the same process (tests, evals that do not need isolation, Lambda).
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Protocol

from mcp import Client, StdioServerParameters
from mcp.server.mcpserver import MCPServer

from legacybridge.env import load_env
from legacybridge.llm.base import ToolSpec
from legacybridge.mcp_server.server import build_server
from legacybridge.mcp_server.tools import LegacyTools

ROOT = Path(__file__).resolve().parents[3]


class ToolBackend(Protocol):
    async def list_tools(self) -> list[ToolSpec]: ...

    async def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]: ...


def stdio_params() -> StdioServerParameters:
    """The command Claude Code uses, read from ``.mcp.json`` so there is a single definition."""
    import os

    load_env()
    spec = json.loads((ROOT / ".mcp.json").read_text(encoding="utf-8"))["mcpServers"][
        "legacybridge"
    ]
    return StdioServerParameters(
        command=spec["command"], args=spec["args"], env={**os.environ}, cwd=str(ROOT)
    )


class McpToolBackend:
    def __init__(self, target: StdioServerParameters | MCPServer, timeout_s: float = 60.0):
        self._target, self._timeout = target, timeout_s
        self._client: Client | None = None

    @classmethod
    def stdio(cls) -> McpToolBackend:
        return cls(stdio_params())

    @classmethod
    def in_memory(cls, tools: LegacyTools | None = None) -> McpToolBackend:
        return cls(build_server(tools or LegacyTools()))

    async def __aenter__(self) -> McpToolBackend:
        self._client = Client(self._target, read_timeout_seconds=self._timeout)
        await self._client.__aenter__()
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._client is not None:
            await self._client.__aexit__(*exc)
            self._client = None

    async def list_tools(self) -> list[ToolSpec]:
        client = self._need()
        return [
            ToolSpec(t.name, (t.description or "").strip(), t.input_schema)
            for t in (await client.list_tools()).tools
        ]

    async def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        started = time.perf_counter()
        try:
            response = await self._need().call_tool(name, arguments)
        except Exception as error:  # noqa: BLE001 - a broken tool call is data for the model
            return {
                "ok": False,
                "stage": "mcp",
                "reason": "call_failed",
                "detail": str(error)[:300],
            }
        text = response.content[0].text if response.content else ""
        if response.is_error:
            return {"ok": False, "stage": "mcp", "reason": "tool_error", "detail": text[:300]}
        try:
            out = json.loads(text)
        except json.JSONDecodeError:
            return {"ok": False, "stage": "mcp", "reason": "not_json", "detail": text[:300]}
        out["_ms"] = round((time.perf_counter() - started) * 1000, 1)
        return out

    def _need(self) -> Client:
        if self._client is None:
            raise RuntimeError("use the backend as an async context manager")
        return self._client
