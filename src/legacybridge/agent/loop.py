"""The tool loop shared by the sql_writer and the verifier (ADR-002)."""

from __future__ import annotations

import json
import time
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
    reason: str = "no_finish"  # finished | max_steps | no_finish


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
) -> LoopResult:
    messages = [Message.system(system), Message.user(user)]
    allowed = {t.name for t in tools}
    out = LoopResult()
    nudged = False
    for _ in range(max_steps):
        try:
            response = await llm.chat(messages, [*tools, finish])
        except LLMError as error:
            rec.llm_error(role, error)  # a failed call is part of the trace too
            raise
        rec.llm(role, response)
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
            started = time.perf_counter()
            result = await backend.call(call.name, call.arguments)
            ms = round((time.perf_counter() - started) * 1000, 1)
            ok = result.get("ok") is True
            rec.tool(role, call.name, ok, ms, "" if ok else str(result.get("reason", "")))
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
                    )  # fmt: skip
                )
            messages.append(Message.tool(call, compact(result, rows_to_model, query_id)))
    out.reason = "max_steps"
    return out
