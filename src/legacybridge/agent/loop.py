"""The tool loop shared by the sql_writer and the verifier (ADR-002)."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from legacybridge.agent.backends import ToolBackend
from legacybridge.agent.events import Recorder
from legacybridge.llm.base import LLMClient, LLMError, Message, ToolSpec

NUDGE = "Finish by calling the tool {finish}. Do not answer with plain text."
HIDDEN = {"data_notice", "ms", "_ms", "tables", "violations", "defects_touched"}


@dataclass
class QueryRun:
    """A successful run_query, as the tool really returned it."""

    id: str
    sql: str
    columns: list[str]
    rows: list[list[Any]]
    row_count: int
    truncated: bool
    warnings: list[str]
    violations: list[dict[str, Any]]
    defects_touched: list[str] = field(default_factory=list)


@dataclass
class ToolRecord:
    name: str
    arguments: dict[str, Any]
    result: dict[str, Any]


@dataclass
class LoopResult:
    finish: dict[str, Any] | None = None
    queries: list[QueryRun] = field(default_factory=list)
    calls: list[ToolRecord] = field(default_factory=list)
    text: str = ""
    # finished | finished_by_tool | max_steps | no_finish | llm_error | early_warning
    reason: str = "no_finish"
    error: LLMError | None = None  # the model failed; what the tools already did is kept


def compact(result: dict[str, Any], rows_to_model: int, query_id: str | None = None) -> str:
    """What the model sees of a tool result: short, without bookkeeping."""
    shown = {k: v for k, v in result.items() if k not in HIDDEN}
    rows = shown.get("rows")
    if isinstance(rows, list) and len(rows) > rows_to_model:
        shown["rows"] = rows[:rows_to_model]
        shown["note"] = f"showing {rows_to_model} of {len(rows)} rows"
    if query_id:
        shown["query_id"] = query_id
    return json.dumps(shown, ensure_ascii=False, default=str)


async def run_tool_loop(
    *,
    llm: LLMClient,
    backend: ToolBackend,
    rec: Recorder,
    role: str,
    system: str,
    user: str,
    tools: list[ToolSpec],
    finish: ToolSpec,
    max_steps: int,
    rows_to_model: int,
    stop_after_ok: frozenset[str] = frozenset(),
    watch: Callable[[], bool] | None = None,
) -> LoopResult:
    """Run tools until the model calls ``finish``.

    A tool in ``stop_after_ok`` ends the loop as soon as it succeeds: its result is the whole
    outcome, so no further model call is needed. ``watch`` is asked after every tool result;
    returning True stops the loop (the early warning, ADR-012)."""
    messages = [Message.system(system), Message.user(user)]
    allowed = {t.name for t in tools}
    out = LoopResult()
    nudged = False
    for _ in range(max_steps):
        with rec.llm_call(role, llm, messages) as call:
            try:
                response = await llm.chat(messages, [*tools, finish])
            except LLMError as error:
                call.fail(error)  # a failed call is part of the trace too
                out.error, out.reason = error, "llm_error"
                return out  # keep what the tools already did; the caller decides what that means
            call.ok(response)
        out.text = response.text or out.text
        if not response.tool_calls:
            if nudged:
                out.reason = "no_finish"
                return out
            nudged = True
            messages += [
                Message.assistant(response.text),
                Message.user(NUDGE.format(finish=finish.name)),
            ]
            continue
        messages.append(Message.assistant(response.text, response.tool_calls))
        for call in response.tool_calls:
            if "_raw" in call.arguments:
                messages.append(
                    Message.tool(
                        call,
                        '{"ok": false, "reason": "arguments were not valid JSON; send them again"}',
                    )
                )
                continue
            if call.name == finish.name:
                out.finish, out.reason = call.arguments, "finished"
                return out
            if call.name not in allowed:
                messages.append(
                    Message.tool(
                        call, json.dumps({"ok": False, "reason": f"unknown tool {call.name}"})
                    )
                )
                continue
            with rec.tool_call(role, call.name, call.arguments) as tool:
                result = await backend.call(call.name, call.arguments)
                ok = tool.finish(result)
            out.calls.append(ToolRecord(call.name, call.arguments, result))
            query_id = None
            if call.name == "run_query" and ok:
                query_id = f"q{len(out.queries) + 1}"
                out.queries.append(
                    QueryRun(
                        id=query_id,
                        sql=result["sql"],
                        columns=result["columns"],
                        rows=result["rows"],
                        row_count=result["row_count"],
                        truncated=result["truncated"],
                        warnings=result.get("warnings", []),
                        violations=result.get("violations", []),
                        defects_touched=result.get("defects_touched", []),
                    )  # fmt: skip
                )
            messages.append(Message.tool(call, compact(result, rows_to_model, query_id)))
            if ok and call.name in stop_after_ok:
                out.reason = "finished_by_tool"
                return out
            if watch is not None and watch():
                out.reason = "early_warning"
                return out
    out.reason = "max_steps"
    return out
