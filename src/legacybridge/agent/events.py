"""Collects the events of one agent run and, when tracing is on, turns them into spans (ADR-007).

The span tree of a question::

    invoke_agent legacybridge                       the whole run
      invoke_agent planner | sql_writer | verifier  one per role (and per writer attempt)
        chat <model>                                one per model call
        execute_tool <tool>                         one per tool call (MCP spans nest below)

With no tracer provider installed the OpenTelemetry API is a no-op, so none of this costs anything.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from opentelemetry.trace import Span, SpanKind, Status, StatusCode, Tracer

from legacybridge.agent.schemas import AgentResult, Event, Usage
from legacybridge.llm.base import LLMClient, LLMError, LLMResponse, Message
from legacybridge.observability import attrs as A
from legacybridge.observability.tracing import capture_content, get_tracer, truncate

INFRASTRUCTURE_STAGES = {"mcp", "connection", "preflight"}


def model_of(llm: LLMClient) -> str:
    profile = getattr(llm, "profile", None)
    return getattr(profile, "model", None) or getattr(llm, "name", "model")


def args_hash(arguments: dict[str, Any]) -> str:
    raw = json.dumps(arguments, sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha1(raw.encode()).hexdigest()[:10]


class LLMCall:
    """A model call in flight: records its event and enriches its span."""

    def __init__(self, rec: Recorder, role: str, span: Span, started: float):
        self._rec, self._role, self._span, self._started = rec, role, span, started

    def ok(self, response: LLMResponse) -> None:
        self._rec.events.append(
            Event(
                role=self._role,
                kind="llm",
                name=response.model,
                ok=True,
                ms=round(response.latency_s * 1000, 1),
                input_tokens=response.input_tokens,
                output_tokens=response.output_tokens,
                detail=response.stop_reason,
                retries=response.retries,
                attempt=self._rec.attempt,
                escalated=self._rec.escalated,
            )
        )
        s = self._span
        s.set_attribute(A.GEN_AI_RESPONSE_MODEL, response.model)
        s.set_attribute(A.GEN_AI_INPUT_TOKENS, response.input_tokens)
        s.set_attribute(A.GEN_AI_OUTPUT_TOKENS, response.output_tokens)
        s.set_attribute(A.OI_TOKENS_PROMPT, response.input_tokens)
        s.set_attribute(A.OI_TOKENS_COMPLETION, response.output_tokens)
        s.set_attribute(A.OI_TOKENS_TOTAL, response.input_tokens + response.output_tokens)
        if response.stop_reason:
            s.set_attribute(A.GEN_AI_FINISH_REASONS, [response.stop_reason])
        if response.retries:
            s.set_attribute(A.LB_LLM_RETRIES, response.retries)
        if self._rec.capture:
            calls = [{"name": c.name, "arguments": c.arguments} for c in response.tool_calls]
            output = response.text or json.dumps(calls, ensure_ascii=False, default=str)
            s.set_attribute(A.OI_OUTPUT, truncate(output))

    def fail(self, error: Exception) -> None:
        self._rec.events.append(
            Event(
                role=self._role,
                kind="llm",
                name="-",
                ok=False,
                ms=round((time.perf_counter() - self._started) * 1000, 1),
                detail=str(error)[:300],
                attempt=self._rec.attempt,
                escalated=self._rec.escalated,
            )
        )
        self._span.set_status(Status(StatusCode.ERROR, truncate(error, 200)))


class ToolCall:
    """A tool call in flight."""

    def __init__(self, rec: Recorder, role: str, name: str, args: dict[str, Any], span: Span):
        self._rec, self._role, self._name, self._args, self._span = rec, role, name, args, span
        self._started = time.perf_counter()

    def finish(self, result: dict[str, Any]) -> bool:
        ms = round((time.perf_counter() - self._started) * 1000, 1)
        ok = result.get("ok") is True
        stage = "" if ok else str(result.get("stage", ""))
        reason = "" if ok else str(result.get("reason", ""))
        sql = str(self._args.get("sql", ""))[:500] if self._name == "run_query" else ""
        self._rec.events.append(
            Event(
                role=self._role,
                kind="tool",
                name=self._name,
                ok=ok,
                ms=ms,
                detail=reason[:300],
                attempt=self._rec.attempt,
                args_hash=args_hash(self._args),
                stage=stage,
                sql=sql,
                escalated=self._rec.escalated,
            )
        )
        s = self._span
        s.set_attribute(A.LB_TOOL_OK, ok)
        if not ok:
            s.set_attribute(A.LB_TOOL_STAGE, stage)
            s.set_attribute("error.type", reason or stage or "failed")
            if stage in INFRASTRUCTURE_STAGES:  # a guard rejection is an expected outcome
                s.set_status(Status(StatusCode.ERROR, truncate(reason, 200)))
        else:
            if "row_count" in result:
                s.set_attribute(A.LB_ROW_COUNT, result["row_count"])
                s.set_attribute(A.LB_TRUNCATED, bool(result.get("truncated")))
            if result.get("defects_touched"):
                s.set_attribute(A.LB_DEFECTS_TOUCHED, list(result["defects_touched"]))
        if self._rec.capture:
            text = json.dumps(result, ensure_ascii=False, default=str)
            s.set_attribute(A.OI_OUTPUT, truncate(text))
        return ok


class Recorder:
    def __init__(self, tracer: Tracer | None = None, capture: bool | None = None) -> None:
        self.events: list[Event] = []
        self._started = time.perf_counter()
        self.tracer = tracer or get_tracer()
        self.capture = capture_content() if capture is None else capture
        self.attempt = 0  # writer attempt; set by the orchestrator
        self.escalated = False  # True while the rerun on the escalation profile is recorded
        self._root: Span | None = None

    # ------------------------------------------------------------------ spans
    @contextmanager
    def run(self, question: str, profile: str) -> Iterator[Span]:
        with self.tracer.start_as_current_span(
            f"{A.OP_INVOKE_AGENT} legacybridge", kind=SpanKind.INTERNAL
        ) as span:
            span.set_attributes(
                {
                    A.GEN_AI_OPERATION: A.OP_INVOKE_AGENT,
                    A.GEN_AI_AGENT_NAME: "legacybridge",
                    A.OI_SPAN_KIND: A.KIND_AGENT,
                    A.LB_PROFILE: profile,
                }
            )
            if self.capture:
                span.set_attribute(A.OI_INPUT, truncate(question))
            self._root = span
            yield span

    @contextmanager
    def role(self, role: str, attempt: int | None = None) -> Iterator[Span]:
        if attempt is not None:
            self.attempt = attempt
        with self.tracer.start_as_current_span(
            f"{A.OP_INVOKE_AGENT} {role}", kind=SpanKind.INTERNAL
        ) as span:
            span.set_attributes(
                {
                    A.GEN_AI_OPERATION: A.OP_INVOKE_AGENT,
                    A.GEN_AI_AGENT_NAME: role,
                    A.OI_SPAN_KIND: A.KIND_AGENT,
                    A.LB_ROLE: role,
                    A.LB_ATTEMPT: self.attempt,
                    A.LB_ESCALATED: self.escalated,
                }
            )
            yield span

    @contextmanager
    def llm_call(
        self, role: str, llm: LLMClient, messages: list[Message] | None = None
    ) -> Iterator[LLMCall]:
        model = model_of(llm)
        profile = getattr(llm, "profile", None)
        with self.tracer.start_as_current_span(
            f"{A.OP_CHAT} {model}", kind=SpanKind.CLIENT
        ) as span:
            span.set_attributes(
                {
                    A.GEN_AI_OPERATION: A.OP_CHAT,
                    A.GEN_AI_PROVIDER: "openai_compat",
                    A.GEN_AI_REQUEST_MODEL: model,
                    A.OI_SPAN_KIND: A.KIND_LLM,
                    A.OI_MODEL: model,
                    A.LB_ROLE: role,
                    A.LB_ATTEMPT: self.attempt,
                }
            )
            if profile is not None:
                span.set_attribute(A.GEN_AI_REQUEST_MAX_TOKENS, profile.max_tokens)
                span.set_attribute(A.GEN_AI_REQUEST_TEMPERATURE, profile.temperature)
            if self.capture and messages:
                for i, m in enumerate(messages):
                    span.set_attribute(f"llm.input_messages.{i}.message.role", m.role)
                    span.set_attribute(
                        f"llm.input_messages.{i}.message.content", truncate(m.content)
                    )
                span.set_attribute(A.OI_INPUT, truncate(messages[-1].content))
            call = LLMCall(self, role, span, time.perf_counter())
            try:
                yield call
            except LLMError as error:  # call.fail() already recorded it; keep the span red
                span.set_status(Status(StatusCode.ERROR, truncate(error, 200)))
                raise

    @contextmanager
    def tool_call(self, role: str, name: str, arguments: dict[str, Any]) -> Iterator[ToolCall]:
        with self.tracer.start_as_current_span(
            f"{A.OP_EXECUTE_TOOL} {name}", kind=SpanKind.INTERNAL
        ) as span:
            span.set_attributes(
                {
                    A.GEN_AI_OPERATION: A.OP_EXECUTE_TOOL,
                    A.GEN_AI_TOOL_NAME: name,
                    A.OI_SPAN_KIND: A.KIND_TOOL,
                    A.OI_TOOL_NAME: name,
                    A.LB_ROLE: role,
                    A.LB_ATTEMPT: self.attempt,
                }
            )
            if self.capture:
                text = truncate(json.dumps(arguments, ensure_ascii=False, default=str))
                span.set_attribute(A.OI_TOOL_PARAMETERS, text)
                span.set_attribute(A.OI_INPUT, text)
            yield ToolCall(self, role, name, arguments, span)

    # ------------------------------------------------------------------ the run as a whole
    @property
    def trace_id(self) -> str | None:
        if self._root is None:
            return None
        ctx = self._root.get_span_context()
        return format(ctx.trace_id, "032x") if ctx.is_valid else None

    def annotate_run(self, result: AgentResult) -> None:
        """Put what the agent concluded on the root span (``lb.*`` attributes)."""
        span = self._root
        if span is None or not span.is_recording():
            return
        span.set_attributes(
            {
                A.LB_STATUS: result.status,
                A.LB_CONFIDENCE: result.confidence,
                A.LB_SWAR_RISK: result.swar_risk,
                A.LB_SWAR_LEVEL: result.swar_level,
                A.LB_DEFECTS_TOUCHED: result.defects_touched,
                A.LB_SWAR_SIGNALS: [s.name for s in result.swar_signals],
            }
        )
        if result.abstain_reason:
            span.set_attribute(A.LB_ABSTAIN_REASON, result.abstain_reason)
        if result.early_warning:
            span.set_attribute(A.LB_EARLY_WARNING, result.early_warning)
        if result.request_id is not None:
            span.set_attribute(A.LB_REQUEST_ID, result.request_id)
        if result.verdicts:
            span.set_attribute(A.LB_VERIFIER_AGREEMENT, result.verdicts[-1].agrees)
        if result.evidence is not None:
            span.set_attribute(A.LB_ROW_COUNT, result.evidence.row_count)
            span.set_attribute(A.LB_TRUNCATED, result.evidence.truncated)
        if self.capture:
            span.set_attribute(A.OI_OUTPUT, truncate(result.answer))
        if result.abstain_reason == "llm_error":
            span.set_status(Status(StatusCode.ERROR, truncate(result.answer, 200)))

    def usage(self) -> Usage:
        llm = [e for e in self.events if e.kind == "llm"]
        return Usage(
            llm_calls=len(llm),
            tool_calls=sum(1 for e in self.events if e.kind == "tool"),
            input_tokens=sum(e.input_tokens for e in llm),
            output_tokens=sum(e.output_tokens for e in llm),
            ms=round((time.perf_counter() - self._started) * 1000, 1),
        )
