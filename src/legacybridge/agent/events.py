"""Collects the events and token usage of one agent run."""

from __future__ import annotations

import time

from legacybridge.agent.schemas import Event, Usage
from legacybridge.llm.base import LLMResponse


class Recorder:
    def __init__(self) -> None:
        self.events: list[Event] = []
        self._started = time.perf_counter()

    def llm(self, role: str, response: LLMResponse) -> None:
        self.events.append(
            Event(
                role=role,
                kind="llm",
                name=response.model,
                ok=True,
                ms=round(response.latency_s * 1000, 1),
                input_tokens=response.input_tokens,
                output_tokens=response.output_tokens,
                detail=response.stop_reason,
                retries=response.retries,
            )  # fmt: skip
        )

    def llm_error(self, role: str, error: Exception, ms: float = 0.0) -> None:
        self.events.append(
            Event(role=role, kind="llm", name="-", ok=False, ms=ms, detail=str(error)[:300])
        )

    def tool(self, role: str, name: str, ok: bool, ms: float, detail: str = "") -> None:
        self.events.append(
            Event(role=role, kind="tool", name=name, ok=ok, ms=ms, detail=detail[:300])
        )

    def usage(self) -> Usage:
        llm = [e for e in self.events if e.kind == "llm"]
        return Usage(
            llm_calls=len(llm),
            tool_calls=sum(1 for e in self.events if e.kind == "tool"),
            input_tokens=sum(e.input_tokens for e in llm),
            output_tokens=sum(e.output_tokens for e in llm),
            ms=round((time.perf_counter() - self._started) * 1000, 1),
        )
