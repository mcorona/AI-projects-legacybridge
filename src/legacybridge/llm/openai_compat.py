"""OpenAI-compatible chat adapter (LM Studio, OmniRoute) with concurrency and retries (ADR-003)."""

from __future__ import annotations

import asyncio
import json
import random
import re
import time
from collections.abc import Awaitable, Callable
from typing import Any

import openai
from openai import AsyncOpenAI

from legacybridge.llm.base import (
    EmptyCompletion,
    LLMError,
    LLMResponse,
    LLMUnavailable,
    Message,
    ToolCall,
    ToolSpec,
)
from legacybridge.llm.profiles import Profile

_THINK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
RETRYABLE = (openai.RateLimitError, openai.InternalServerError, openai.APIConnectionError)
MAX_RETRY_AFTER_S = 60.0
RETRY_TEMPERATURE = 0.4  # used when a retry follows an empty reply


def strip_think(text: str) -> str:
    """Remove ``<think>...</think>`` blocks and an unclosed trailing ``<think>``."""
    text = _THINK.sub("", text)
    if "<think>" in text.lower():
        text = text[: text.lower().index("<think>")]
    return text.strip()


def _arguments(raw: Any) -> dict[str, Any]:
    """Tool arguments as a dict; invalid JSON is kept under ``_raw`` so the loop can ask again."""
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {"_raw": raw}
    return parsed if isinstance(parsed, dict) else {"_raw": raw}


def _to_openai_messages(messages: list[Message]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for m in messages:
        if m.role == "assistant" and m.tool_calls:
            out.append(
                {
                    "role": "assistant",
                    "content": m.content or None,
                    "tool_calls": [
                        {
                            "id": c.id,
                            "type": "function",
                            "function": {
                                "name": c.name,
                                "arguments": json.dumps(c.arguments, ensure_ascii=False),
                            },
                        }
                        for c in m.tool_calls
                    ],
                }
            )
        elif m.role == "tool":
            out.append({"role": "tool", "tool_call_id": m.tool_call_id, "content": m.content})
        else:
            out.append({"role": m.role, "content": m.content})
    return out


def _to_openai_tools(tools: list[ToolSpec]) -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {"name": t.name, "description": t.description, "parameters": t.parameters},
        }
        for t in tools
    ]


class OpenAICompatClient:
    def __init__(
        self,
        profile: Profile,
        *,
        http_client: Any = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        jitter: Callable[[], float] = random.random,
    ):
        self.name = profile.name
        self.profile = profile
        self._client = AsyncOpenAI(
            base_url=profile.base_url,
            api_key=profile.api_key or "lm-studio",  # LM Studio ignores it, the SDK insists
            http_client=http_client,
            max_retries=0,  # retries are ours, so they are visible and bounded by the profile
            timeout=profile.timeout_s,
        )
        self._slots = asyncio.Semaphore(profile.max_concurrency)
        self._sleep, self._jitter = sleep, jitter

    # ------------------------------------------------------------------ public
    async def chat(
        self,
        messages: list[Message],
        tools: list[ToolSpec] | None = None,
        *,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> LLMResponse:
        request: dict[str, Any] = {
            "model": self.profile.model,
            "messages": _to_openai_messages(messages),
            "max_tokens": max_tokens or self.profile.max_tokens,
            "temperature": self.profile.temperature if temperature is None else temperature,
        }
        if tools:
            request["tools"] = _to_openai_tools(tools)
        reasons: list[str] = []
        started = time.perf_counter()
        async with self._slots:  # held across retries: a rate-limited slot must not be stolen
            for attempt in range(self.profile.retries + 1):
                try:
                    response = await self._client.chat.completions.create(**request)
                    return self._parse(response, started, reasons)
                except (EmptyCompletion, *RETRYABLE) as error:
                    reasons.append(self._reason(error))
                    if isinstance(error, EmptyCompletion):
                        # At temperature 0 the same request gives the same empty reply, so retrying
                        # it unchanged is wasted. A little randomness gets a different sample.
                        request["temperature"] = max(request["temperature"], RETRY_TEMPERATURE)
                    if attempt >= self.profile.retries:
                        why = "; ".join(reasons)
                        raise LLMUnavailable(
                            f"{self.name}: gave up after {attempt + 1} attempts ({why})"
                        ) from error
                    await self._sleep(self._delay(attempt, error))
                except openai.AuthenticationError as error:
                    raise LLMError(
                        f"{self.name}: authentication failed; check the API key"
                    ) from error
                except openai.APIStatusError as error:
                    if error.status_code == 408:  # request timeout: worth another try
                        reasons.append(self._reason(error))
                        if attempt < self.profile.retries:
                            await self._sleep(self._delay(attempt, error))
                            continue
                    raise LLMError(
                        f"{self.name}: HTTP {error.status_code}: {error.message}"
                    ) from error
        raise LLMUnavailable(f"{self.name}: no attempt was made")  # pragma: no cover

    # ------------------------------------------------------------------ helpers
    def _parse(self, response: Any, started: float, reasons: list[str]) -> LLMResponse:
        choice = response.choices[0]
        message = choice.message
        text = strip_think(message.content or "")  # reasoning_content is deliberately ignored
        calls = tuple(
            ToolCall(c.id or f"call_{i}", c.function.name, _arguments(c.function.arguments))
            for i, c in enumerate(message.tool_calls or [])
        )
        if not text and not calls:
            reasoning = (message.model_extra or {}).get("reasoning_content") or ""
            tokens = response.usage.completion_tokens if response.usage else 0
            raise EmptyCompletion(
                f"empty reply (finish_reason={choice.finish_reason or 'n/a'}, {tokens} output "
                f"tokens, {len(reasoning)} chars of reasoning)"
            )
        usage = response.usage
        return LLMResponse(
            text=text,
            tool_calls=calls,
            input_tokens=usage.prompt_tokens if usage else 0,
            output_tokens=usage.completion_tokens if usage else 0,
            latency_s=time.perf_counter() - started,
            stop_reason=choice.finish_reason or "",
            model=response.model or self.profile.model,
            retries=list(reasons),
        )

    @staticmethod
    def _reason(error: Exception) -> str:
        if isinstance(error, EmptyCompletion):
            return f"EmptyCompletion: {error}"
        status = getattr(error, "status_code", None)
        return f"{type(error).__name__}{f' {status}' if status else ''}"

    def _delay(self, attempt: int, error: Exception) -> float:
        response = getattr(error, "response", None)
        header = response.headers.get("retry-after") if response is not None else None
        if header:
            try:
                return min(float(header), MAX_RETRY_AFTER_S)
            except ValueError:
                pass
        return self.profile.backoff_base_s * (2**attempt) * (0.5 + self._jitter())
