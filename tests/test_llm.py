"""OpenAI-compatible adapter (ADR-003), against a simulated transport."""

import asyncio

import httpx2
import pytest

from legacybridge.llm.base import (
    EmptyCompletion,
    LLMError,
    LLMUnavailable,
    Message,
    ToolCall,
    ToolSpec,
)
from legacybridge.llm.openai_compat import OpenAICompatClient, _arguments, strip_think
from legacybridge.llm.profiles import Profile

pytestmark = pytest.mark.anyio


def profile(**kw) -> Profile:
    base = dict(
        name="test", kind="openai_compat", base_url="http://fake/v1", model="m", retries=3,
        backoff_base_s=1.0, max_concurrency=1,
    )  # fmt: skip
    return Profile(**{**base, **kw})


def reply(content="hola", tool_calls=None, finish="stop", usage=(3, 2), status=200, headers=None):
    message = {"role": "assistant", "content": content}
    if tool_calls:
        message["tool_calls"] = tool_calls
    body = {
        "id": "x", "object": "chat.completion", "created": 0, "model": "served-model",
        "choices": [{"index": 0, "finish_reason": finish, "message": message}],
        "usage": {"prompt_tokens": usage[0], "completion_tokens": usage[1], "total_tokens": sum(usage)},
    }  # fmt: skip
    return httpx2.Response(status, json=body, headers=headers)


def error(status, headers=None):
    return httpx2.Response(status, json={"error": {"message": f"http {status}"}}, headers=headers)


def make(responses, **kw):
    """A client whose transport serves ``responses`` in order; returns (client, sleeps, calls)."""
    queue, sleeps, calls = list(responses), [], []

    def handler(request):
        calls.append(request)
        item = queue.pop(0)
        return item(request) if callable(item) else item

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    http = httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
    client = OpenAICompatClient(
        profile(**kw), http_client=http, sleep=fake_sleep, jitter=lambda: 0.5
    )
    return client, sleeps, calls


HI = [Message.user("hola")]


async def test_returns_text_usage_and_model():
    client, _, _ = make([reply("hola", usage=(10, 4))])
    out = await client.chat(HI)
    assert (out.text, out.input_tokens, out.output_tokens) == ("hola", 10, 4)
    assert out.model == "served-model" and out.stop_reason == "stop" and out.retries == []


async def test_think_blocks_are_removed_and_reasoning_content_is_ignored():
    body = reply("<think>let me see</think>respuesta")
    client, _, _ = make([body])
    assert (await client.chat(HI)).text == "respuesta"
    assert strip_think("a <think>unclosed") == "a"
    assert strip_think("<think>x</think>") == ""


async def test_tool_calls_are_parsed_and_invalid_arguments_are_kept_raw():
    calls = [
        {"id": "c1", "type": "function", "function": {"name": "run_query", "arguments": '{"sql": "SELECT 1"}'}},
        {"id": "", "type": "function", "function": {"name": "describe_table", "arguments": "{not json"}},
    ]  # fmt: skip
    client, _, _ = make([reply("", tool_calls=calls, finish="tool_calls")])
    out = await client.chat(HI, tools=[ToolSpec("run_query", "d", {"type": "object"})])
    assert out.tool_calls[0] == ToolCall("c1", "run_query", {"sql": "SELECT 1"})
    assert out.tool_calls[1].arguments == {"_raw": "{not json"} and out.tool_calls[1].id == "call_1"
    assert _arguments('["a"]') == {"_raw": '["a"]'}


async def test_request_carries_tools_messages_and_defaults():
    client, _, calls = make([reply()], max_tokens=1234, temperature=0.0)
    spec = ToolSpec("run_query", "Run SQL", {"type": "object", "properties": {}})
    history = [
        Message.system("sys"),
        Message.user("q"),
        Message.assistant("", (ToolCall("c1", "run_query", {"sql": "SELECT 1"}),)),
        Message.tool(ToolCall("c1", "run_query", {}), '{"ok": true}'),
    ]
    await client.chat(history, tools=[spec])
    import json

    sent = json.loads(calls[0].content)
    assert sent["model"] == "m" and sent["max_tokens"] == 1234 and sent["temperature"] == 0.0
    assert sent["tools"][0]["function"]["name"] == "run_query"
    assert sent["messages"][2]["tool_calls"][0]["function"]["arguments"] == '{"sql": "SELECT 1"}'
    assert sent["messages"][3] == {"role": "tool", "tool_call_id": "c1", "content": '{"ok": true}'}


async def test_rate_limit_is_retried_and_retry_after_is_honoured():
    client, sleeps, calls = make([error(429, {"retry-after": "7"}), reply("ok")])
    out = await client.chat(HI)
    assert out.text == "ok" and len(calls) == 2
    assert sleeps == [7.0] and out.retries == ["RateLimitError 429"]


async def test_backoff_is_exponential_when_there_is_no_retry_after():
    client, sleeps, _ = make([error(500), error(503), error(500), reply("ok")], backoff_base_s=2)
    await client.chat(HI)
    assert sleeps == [2 * 1 * 1.0, 2 * 2 * 1.0, 2 * 4 * 1.0]  # jitter fixed at 0.5 -> x1.0


async def test_retry_after_is_capped():
    client, sleeps, _ = make([error(429, {"retry-after": "9999"}), reply("ok")])
    await client.chat(HI)
    assert sleeps == [60.0]


async def test_an_empty_reply_is_retried_then_succeeds():
    client, _, calls = make([reply("<think>only thinking</think>", finish="length"), reply("ok")])
    out = await client.chat(HI)
    assert out.text == "ok" and len(calls) == 2
    assert out.retries[0].startswith("EmptyCompletion: empty reply (finish_reason=length")


async def test_an_empty_reply_is_retried_with_a_different_sample_not_the_same_request():
    import json

    client, _, calls = make([reply(""), reply(""), reply("ok")], temperature=0.0)
    out = await client.chat(HI)
    sent = [json.loads(c.content)["temperature"] for c in calls]
    assert sent == [0.0, 0.4, 0.4] and out.text == "ok"


async def test_an_empty_reply_says_what_happened():
    client, _, _ = make([reply("", finish="stop", usage=(5, 0))] * 2, retries=1)
    with pytest.raises(LLMUnavailable) as raised:
        await client.chat(HI)
    text = str(raised.value)
    assert "EmptyCompletion: empty reply (finish_reason=stop, 0 output tokens" in text


async def test_gives_up_after_the_profile_retries_with_every_reason():
    client, sleeps, calls = make([error(429)] * 4, retries=3)
    with pytest.raises(LLMUnavailable) as raised:
        await client.chat(HI)
    assert len(calls) == 4 and len(sleeps) == 3
    assert "4 attempts" in str(raised.value) and "RateLimitError 429" in str(raised.value)


async def test_persistent_empty_replies_raise_unavailable_with_the_cause():
    client, _, _ = make([reply("")] * 3, retries=2)
    with pytest.raises(LLMUnavailable) as raised:
        await client.chat(HI)
    assert isinstance(raised.value.__cause__, EmptyCompletion)


async def test_authentication_and_bad_requests_are_not_retried():
    client, sleeps, calls = make([error(401)])
    with pytest.raises(LLMError, match="authentication"):
        await client.chat(HI)
    assert len(calls) == 1 and sleeps == []
    client, _, calls = make([error(400)])
    with pytest.raises(LLMError, match="HTTP 400"):
        await client.chat(HI)
    assert len(calls) == 1


async def test_concurrency_is_limited_by_the_profile():
    active, peak = 0, 0

    async def handler(request):
        return reply("ok")

    class Probe(httpx2.AsyncBaseTransport):
        async def handle_async_request(self, request):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.02)
            active -= 1
            return reply("ok")

    for limit in (1, 3):
        active = peak = 0
        client = OpenAICompatClient(
            profile(max_concurrency=limit), http_client=httpx2.AsyncClient(transport=Probe())
        )
        await asyncio.gather(*(client.chat(HI) for _ in range(6)))
        assert peak == limit, f"max_concurrency={limit} but {peak} calls ran at once"


async def test_a_rate_limited_call_keeps_its_slot_while_it_waits():
    """With max_concurrency=1 a retry must not let another request jump the queue."""
    order = []
    served = iter([error(429), reply("first"), reply("second")])

    class Transport(httpx2.AsyncBaseTransport):
        async def handle_async_request(self, request):
            order.append("call")
            return next(served)

    async def slow_sleep(seconds):
        order.append("sleep")
        await asyncio.sleep(0.02)

    client = OpenAICompatClient(
        profile(), http_client=httpx2.AsyncClient(transport=Transport()), sleep=slow_sleep,
        jitter=lambda: 0.5,
    )  # fmt: skip
    a, b = await asyncio.gather(client.chat(HI), client.chat(HI))
    assert (a.text, b.text) == ("first", "second")
    assert order == ["call", "sleep", "call", "call"]
