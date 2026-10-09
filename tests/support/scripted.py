"""A model that follows a script, to test the agent without a real LLM."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from legacybridge.llm.base import LLMResponse, Message, ToolCall, ToolSpec

ROLE_MARKERS = {
    "You are the planner": "planner",
    "You answer ONE question": "writer",
    "You are an independent verifier": "verifier",
}


def say(text: str) -> LLMResponse:
    return LLMResponse(
        text=text, input_tokens=10, output_tokens=5, model="scripted", stop_reason="stop"
    )


def call(name: str, **arguments: Any) -> LLMResponse:
    return calls((name, arguments))


def calls(*pairs: tuple[str, dict[str, Any]]) -> LLMResponse:
    tool_calls = tuple(ToolCall(f"c{i}", n, a) for i, (n, a) in enumerate(pairs))
    return LLMResponse(
        text="", tool_calls=tool_calls, input_tokens=10, output_tokens=5, model="scripted",
        stop_reason="tool_calls",
    )  # fmt: skip


def plan(action: str, **fields: Any) -> LLMResponse:
    import json

    body = {"action": action, "language": "es", "intent": "test intent", **fields}
    return say(json.dumps(body))


class ScriptedLLM:
    """One queue of replies per role. A reply is a response, an exception, or a function of the
    messages. Everything the agent sent is kept in ``calls`` for assertions."""

    name = "scripted"

    def __init__(self, planner=(), writer=(), verifier=()):
        self.queues: dict[str, list[Any]] = {
            "planner": list(planner), "writer": list(writer), "verifier": list(verifier),
        }  # fmt: skip
        self.calls: list[tuple[str, list[Message], list[ToolSpec] | None]] = []

    def role_of(self, messages: list[Message]) -> str:
        head = messages[0].content
        for marker, role in ROLE_MARKERS.items():
            if head.startswith(marker):
                return role
        raise AssertionError(f"unrecognised system prompt: {head[:60]!r}")

    async def chat(
        self,
        messages: list[Message],
        tools: list[ToolSpec] | None = None,
        *,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> LLMResponse:
        role = self.role_of(messages)
        self.calls.append((role, list(messages), tools))
        queue = self.queues[role]
        if not queue:
            raise AssertionError(f"the {role} script ran out of replies")
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item(messages) if isinstance(item, Callable) else item

    # ---- assertions helpers
    def sent(self, role: str) -> list[tuple[list[Message], list[ToolSpec] | None]]:
        return [(m, t) for r, m, t in self.calls if r == role]

    def tool_names(self, role: str) -> set[str]:
        return {t.name for _, tools in self.sent(role) for t in tools or []}

    def left_over(self) -> dict[str, int]:
        return {role: len(q) for role, q in self.queues.items() if q}
