"""Amazon Bedrock Converse adapter with tool use, concurrency and retries (ADR-003, ADR-014).

The agent talks to ``LLMClient`` only; this module is the one place that knows the Converse shapes.
boto3 is synchronous, so each call runs on a worker thread and the profile's semaphore bounds how
many are in flight. Retries are ours (botocore's own are switched off) so they are visible in
``LLMResponse.retries`` and bounded by the profile, like the OpenAI-compatible adapter.
"""

from __future__ import annotations

import asyncio
import random
import time
from collections.abc import Awaitable, Callable
from typing import Any

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError, HTTPClientError
from botocore.exceptions import ConnectionError as BotoConnectionError

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

# Errors that are worth another attempt. Everything else (access, validation, unknown model) is a
# request that will fail the same way again, so it is reported at once.
RETRYABLE_CODES = {
    "ThrottlingException",
    "ModelTimeoutException",
    "ServiceUnavailableException",
    "InternalServerException",
    "ModelNotReadyException",
}
RETRY_TEMPERATURE = 0.4  # used when a retry follows an empty reply
CONNECT_TIMEOUT_S = 10
EMPTY_TEXT = "(empty)"  # Converse rejects empty text blocks


def make_client(profile: Profile) -> Any:
    """The ``bedrock-runtime`` client for a profile: a named AWS profile on a laptop, the default
    credential chain (the function's role) in Lambda."""
    session = boto3.Session(profile_name=profile.aws_profile, region_name=profile.region)
    return session.client(
        "bedrock-runtime",
        config=Config(
            retries={"total_max_attempts": 1},  # the retries are ours, so they are visible
            read_timeout=profile.timeout_s,
            connect_timeout=CONNECT_TIMEOUT_S,
        ),
    )


# ============================================================================ message conversion
def _text(content: str) -> list[dict[str, Any]]:
    return [{"text": content}] if content and content.strip() else []


def to_converse(messages: list[Message]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """``(system, messages)`` in the Converse shape.

    Converse wants user and assistant turns to alternate, so a tool result (which the neutral
    format keeps as its own message) becomes a ``toolResult`` block in a ``user`` turn, and
    consecutive turns of one role are merged.
    """
    system: list[dict[str, Any]] = []
    turns: list[dict[str, Any]] = []
    for m in messages:
        if m.role == "system":
            system += _text(m.content)
            continue
        if m.role == "tool":
            role, blocks = (
                "user",
                [
                    {
                        "toolResult": {
                            "toolUseId": m.tool_call_id,
                            "content": [{"text": m.content if m.content.strip() else EMPTY_TEXT}],
                        }
                    }
                ],
            )
        elif m.role == "assistant":
            role = "assistant"
            blocks = _text(m.content) + [
                {"toolUse": {"toolUseId": c.id, "name": c.name, "input": c.arguments}}
                for c in m.tool_calls
            ]
        else:
            role, blocks = "user", _text(m.content) or [{"text": EMPTY_TEXT}]
        if not blocks:
            continue
        if turns and turns[-1]["role"] == role:
            turns[-1]["content"].extend(blocks)
        else:
            turns.append({"role": role, "content": blocks})
    return system, turns


def to_tool_config(tools: list[ToolSpec]) -> dict[str, Any]:
    return {
        "tools": [
            {
                "toolSpec": {
                    "name": t.name,
                    "description": t.description,
                    "inputSchema": {"json": t.parameters},
                }
            }
            for t in tools
        ]
    }


def has_tool_blocks(turns: list[dict[str, Any]]) -> bool:
    return any("toolUse" in b or "toolResult" in b for t in turns for b in t["content"])


def from_converse(response: dict[str, Any]) -> tuple[str, tuple[ToolCall, ...]]:
    """The text and tool calls of a Converse reply. Reasoning blocks are deliberately ignored."""
    text: list[str] = []
    calls: list[ToolCall] = []
    for block in response["output"]["message"]["content"]:
        if "text" in block:
            text.append(block["text"])
        elif "toolUse" in block:
            use = block["toolUse"]
            arguments = use.get("input")
            calls.append(
                ToolCall(
                    use["toolUseId"],
                    use["name"],
                    arguments if isinstance(arguments, dict) else {"_raw": arguments},
                )
            )
    return "".join(text).strip(), tuple(calls)


# ============================================================================ the client
class BedrockConverseClient:
    def __init__(
        self,
        profile: Profile,
        *,
        client: Any = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        jitter: Callable[[], float] = random.random,
    ):
        self.name = profile.name
        self.profile = profile
        self._client = client if client is not None else make_client(profile)
        self._slots = asyncio.Semaphore(profile.max_concurrency)
        self._sleep, self._jitter = sleep, jitter

    async def chat(
        self,
        messages: list[Message],
        tools: list[ToolSpec] | None = None,
        *,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> LLMResponse:
        system, turns = to_converse(messages)
        request: dict[str, Any] = {
            "modelId": self.profile.model,
            "messages": turns,
            "inferenceConfig": {
                "maxTokens": max_tokens or self.profile.max_tokens,
                "temperature": self.profile.temperature if temperature is None else temperature,
            },
        }
        if system:
            request["system"] = system
        if tools:
            request["toolConfig"] = to_tool_config(tools)
        elif has_tool_blocks(turns):
            raise LLMError(f"{self.name}: the history has tool calls but no tools were offered")
        reasons: list[str] = []
        started = time.perf_counter()
        async with self._slots:  # held across retries: a throttled slot must not be stolen
            for attempt in range(self.profile.retries + 1):
                try:
                    response = await asyncio.to_thread(self._client.converse, **request)
                    return self._parse(response, started, reasons)
                except (
                    EmptyCompletion,
                    ClientError,
                    BotoConnectionError,
                    HTTPClientError,
                ) as error:
                    if isinstance(error, ClientError) and not _retryable(error):
                        raise self._failure(error) from error
                    reasons.append(self._reason(error))
                    if isinstance(error, EmptyCompletion):
                        # At temperature 0 the same request gives the same empty reply.
                        request["inferenceConfig"]["temperature"] = max(
                            request["inferenceConfig"]["temperature"], RETRY_TEMPERATURE
                        )
                    if attempt >= self.profile.retries:
                        why = "; ".join(reasons)
                        raise LLMUnavailable(
                            f"{self.name}: gave up after {attempt + 1} attempts ({why})"
                        ) from error
                    await self._sleep(self._delay(attempt))
                except BotoCoreError as error:  # credentials, endpoint, parameters: not transient
                    raise LLMError(
                        f"{self.name}: {type(error).__name__}: {error}. If the AWS session "
                        "expired, run `aws login`."
                    ) from error
        raise LLMUnavailable(f"{self.name}: no attempt was made")  # pragma: no cover

    # ------------------------------------------------------------------ helpers
    def _parse(self, response: dict[str, Any], started: float, reasons: list[str]) -> LLMResponse:
        text, calls = from_converse(response)
        usage = response.get("usage", {})
        stop = response.get("stopReason", "")
        if not text and not calls:
            raise EmptyCompletion(
                f"empty reply (stopReason={stop or 'n/a'}, "
                f"{usage.get('outputTokens', 0)} output tokens)"
            )
        return LLMResponse(
            text=text,
            tool_calls=calls,
            input_tokens=usage.get("inputTokens", 0),
            output_tokens=usage.get("outputTokens", 0),
            latency_s=time.perf_counter() - started,
            stop_reason=stop,
            model=self.profile.model,
            retries=list(reasons),
        )

    def _failure(self, error: ClientError) -> LLMError:
        code = _code(error)
        message = error.response.get("Error", {}).get("Message", "")[:300]
        hint = {
            "AccessDeniedException": " (model access or IAM: see the Bedrock console and the role)",
            "ValidationException": " (check the model id: an inference profile id may be needed)",
            "ResourceNotFoundException": " (model or inference profile not found in this region)",
            "ExpiredTokenException": " (the AWS session expired: run `aws login`)",
            "UnrecognizedClientException": " (the AWS credentials were rejected)",
        }.get(code, "")
        return LLMError(f"{self.name}: {code}: {message}{hint}")

    @staticmethod
    def _reason(error: Exception) -> str:
        if isinstance(error, EmptyCompletion):
            return f"EmptyCompletion: {error}"
        if isinstance(error, ClientError):
            return _code(error)
        return type(error).__name__

    def _delay(self, attempt: int) -> float:
        return self.profile.backoff_base_s * (2**attempt) * (0.5 + self._jitter())


def _code(error: ClientError) -> str:
    return error.response.get("Error", {}).get("Code", "ClientError")


def _retryable(error: ClientError) -> bool:
    return _code(error) in RETRYABLE_CODES
