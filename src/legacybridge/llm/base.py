"""Provider-neutral contract between the agent and any model (ADR-003).

The agent depends on this module only; no provider SDK is imported outside ``llm/``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]  # {"_raw": "..."} when the model sent invalid JSON


@dataclass
class Message:
    role: str  # system | user | assistant | tool
    content: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
    tool_call_id: str | None = None

    @classmethod
    def system(cls, content: str) -> Message:
        return cls("system", content)

    @classmethod
    def user(cls, content: str) -> Message:
        return cls("user", content)

    @classmethod
    def assistant(cls, content: str = "", tool_calls: tuple[ToolCall, ...] = ()) -> Message:
        return cls("assistant", content, tool_calls)

    @classmethod
    def tool(cls, call: ToolCall, content: str) -> Message:
        return cls("tool", content, tool_call_id=call.id)


@dataclass
class LLMResponse:
    text: str
    tool_calls: tuple[ToolCall, ...] = ()
    input_tokens: int = 0
    output_tokens: int = 0
    latency_s: float = 0.0
    stop_reason: str = ""
    model: str = ""
    retries: list[str] = field(default_factory=list)  # why earlier attempts were repeated

    @property
    def truncated(self) -> bool:
        return self.stop_reason in ("length", "max_tokens")


class LLMError(RuntimeError):
    """A model call failed in a way the caller should report, not hide."""


class EmptyCompletion(LLMError):
    """The model answered with no text and no tool call (e.g. it spent its budget thinking)."""


class LLMUnavailable(LLMError):
    """The provider kept failing after every retry."""


class InvalidModelOutput(LLMError):
    """The model's output could not be parsed into what the role needs."""


class LLMClient(Protocol):
    name: str

    async def chat(
        self,
        messages: list[Message],
        tools: list[ToolSpec] | None = None,
        *,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> LLMResponse: ...
