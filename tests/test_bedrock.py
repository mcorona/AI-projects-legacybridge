"""Bedrock Converse adapter (ADR-003, ADR-014), against a simulated boto3 client. No AWS call is made."""

import ast
from pathlib import Path

import pytest
from botocore.exceptions import ClientError, NoCredentialsError, ReadTimeoutError

from legacybridge.llm import bedrock
from legacybridge.llm.base import (
    EmptyCompletion,
    LLMError,
    LLMUnavailable,
    Message,
    ToolCall,
    ToolSpec,
)
from legacybridge.llm.bedrock import (
    BedrockConverseClient,
    from_converse,
    to_converse,
    to_tool_config,
)
from legacybridge.llm.profiles import Profile, ProfileError, load_profile

pytestmark = pytest.mark.anyio

MODEL = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
ENV = {"BEDROCK_CHAT_MODEL": MODEL, "AWS_PROFILE": "demo", "AWS_REGION": "us-west-2"}


def profile(**kw) -> Profile:
    base = dict(name="test", kind="bedrock_converse", model=MODEL, region="us-east-1", retries=3,
                backoff_base_s=1.0, max_concurrency=1)  # fmt: skip
    return Profile(**{**base, **kw})


def reply(text="hola", tool_uses=(), stop="end_turn", usage=(3, 2), extra_blocks=()):
    content = list(extra_blocks) + ([{"text": text}] if text else [])
    content += [{"toolUse": {"toolUseId": i, "name": n, "input": a}} for i, n, a in tool_uses]
    return {
        "output": {"message": {"role": "assistant", "content": content}},
        "stopReason": stop,
        "usage": {"inputTokens": usage[0], "outputTokens": usage[1], "totalTokens": sum(usage)},
    }


def failure(code, message="boom"):
    return ClientError({"Error": {"Code": code, "Message": message}}, "Converse")


class FakeBedrock:
    def __init__(self, items):
        self.items, self.requests = list(items), []

    def converse(self, **request):
        self.requests.append(request)
        item = self.items.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def make(items, **kw):
    fake, sleeps = FakeBedrock(items), []

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    client = BedrockConverseClient(profile(**kw), client=fake, sleep=fake_sleep, jitter=lambda: 0.5)
    return client, fake, sleeps


HI = [Message.user("hola")]
LOOKUP = ToolSpec("lookup", "finds a thing", {"type": "object", "properties": {}})


# --------------------------------------------------------------------------- conversion
def test_system_text_is_separate_and_user_turns_keep_their_text():
    system, turns = to_converse([Message.system("be brief"), Message.user("hola")])
    assert system == [{"text": "be brief"}]
    assert turns == [{"role": "user", "content": [{"text": "hola"}]}]


def test_a_tool_result_becomes_a_user_turn_and_merges_with_the_next_user_text():
    call = ToolCall("t1", "lookup", {"q": "x"})
    _, turns = to_converse([
        Message.user("q"), Message.assistant("thinking", (call,)),
        Message.tool(call, '{"ok": true}'), Message.user("go on"),
    ])  # fmt: skip
    assert [t["role"] for t in turns] == ["user", "assistant", "user"]
    assert turns[1]["content"] == [
        {"text": "thinking"},
        {"toolUse": {"toolUseId": "t1", "name": "lookup", "input": {"q": "x"}}},
    ]
    assert turns[2]["content"] == [
        {"toolResult": {"toolUseId": "t1", "content": [{"text": '{"ok": true}'}]}},
        {"text": "go on"},
    ]


def test_parallel_tool_results_share_one_user_turn():
    a, b = ToolCall("a", "lookup", {}), ToolCall("b", "lookup", {})
    _, turns = to_converse([Message.user("q"), Message.assistant("", (a, b)),
                            Message.tool(a, "1"), Message.tool(b, "2")])  # fmt: skip
    assert [t["role"] for t in turns] == ["user", "assistant", "user"]
    assert [list(block)[0] for block in turns[2]["content"]] == ["toolResult", "toolResult"]
    assert turns[1]["content"][0] == {"toolUse": {"toolUseId": "a", "name": "lookup", "input": {}}}


def test_converse_rejects_empty_text_so_it_is_never_sent():
    call = ToolCall("a", "lookup", {})
    _, turns = to_converse(
        [Message.user("  "), Message.assistant("  ", (call,)), Message.tool(call, "")]
    )
    assert turns[0]["content"] == [{"text": "(empty)"}]
    assert turns[1]["content"] == [{"toolUse": {"toolUseId": "a", "name": "lookup", "input": {}}}]
    assert turns[2]["content"][0]["toolResult"]["content"] == [{"text": "(empty)"}]


def test_the_tool_config_wraps_each_json_schema():
    assert to_tool_config([LOOKUP]) == {"tools": [{"toolSpec": {
        "name": "lookup", "description": "finds a thing",
        "inputSchema": {"json": {"type": "object", "properties": {}}}}}]}  # fmt: skip


def test_text_and_tool_calls_come_out_and_reasoning_is_ignored():
    response = reply("answer ", [("t1", "lookup", {"q": 1})],
                     extra_blocks=[{"reasoningContent": {"reasoningText": {"text": "hmm"}}}])  # fmt: skip
    text, calls = from_converse(response)
    assert text == "answer" and calls == (ToolCall("t1", "lookup", {"q": 1}),)
    odd = reply("", [("t2", "lookup", "not a dict")])
    assert from_converse(odd)[1][0].arguments == {"_raw": "not a dict"}


# --------------------------------------------------------------------------- the client
async def test_a_reply_carries_text_usage_model_and_stop_reason():
    client, fake, _ = make([reply("hola", usage=(10, 4))])
    out = await client.chat(HI)
    assert (out.text, out.input_tokens, out.output_tokens) == ("hola", 10, 4)
    assert out.model == MODEL and out.stop_reason == "end_turn" and out.retries == []
    sent = fake.requests[0]
    assert sent["modelId"] == MODEL and "toolConfig" not in sent and "system" not in sent
    assert sent["inferenceConfig"] == {"maxTokens": 4096, "temperature": 0.0}


async def test_tools_system_and_overrides_reach_the_request():
    client, fake, _ = make([reply("ok")])
    await client.chat([Message.system("s"), Message.user("q")], [LOOKUP],
                      max_tokens=77, temperature=0.2)  # fmt: skip
    sent = fake.requests[0]
    assert (
        sent["system"] == [{"text": "s"}]
        and sent["toolConfig"]["tools"][0]["toolSpec"]["name"] == "lookup"
    )
    assert sent["inferenceConfig"] == {"maxTokens": 77, "temperature": 0.2}


async def test_a_tool_use_reply_is_returned_as_tool_calls():
    client, _, _ = make([reply("", [("t1", "lookup", {"q": "x"})], stop="tool_use")])
    out = await client.chat(HI, [LOOKUP])
    assert (
        out.tool_calls == (ToolCall("t1", "lookup", {"q": "x"}),) and out.stop_reason == "tool_use"
    )


async def test_running_out_of_tokens_is_reported_as_truncated():
    client, _, _ = make([reply("cut off", stop="max_tokens")])
    assert (await client.chat(HI)).truncated


async def test_throttling_is_retried_with_backoff_and_the_reason_is_kept():
    client, fake, sleeps = make(
        [failure("ThrottlingException"), failure("ModelTimeoutException"), reply("ok")]
    )
    out = await client.chat(HI)
    assert out.text == "ok" and out.retries == ["ThrottlingException", "ModelTimeoutException"]
    assert sleeps == [1.0, 2.0] and len(fake.requests) == 3  # 1 * 2**attempt * (0.5 + 0.5)


async def test_a_network_error_is_retried_too():
    client, _, _ = make([ReadTimeoutError(endpoint_url="https://bedrock"), reply("ok")])
    out = await client.chat(HI)
    assert out.text == "ok" and out.retries == ["ReadTimeoutError"]


async def test_giving_up_names_every_attempt():
    client, fake, _ = make([failure("ServiceUnavailableException")] * 2, retries=1)
    with pytest.raises(
        LLMUnavailable, match="gave up after 2 attempts.*ServiceUnavailableException"
    ):
        await client.chat(HI)
    assert len(fake.requests) == 2


@pytest.mark.parametrize("code,hint", [
    ("AccessDeniedException", "model access"),
    ("ValidationException", "inference profile"),
    ("ResourceNotFoundException", "not found"),
    ("ExpiredTokenException", "aws login"),
])  # fmt: skip
async def test_a_request_that_cannot_work_fails_at_once_with_a_hint(code, hint):
    client, fake, sleeps = make([failure(code, "details here")])
    with pytest.raises(LLMError, match=code) as raised:
        await client.chat(HI)
    assert hint in str(raised.value) and "details here" in str(raised.value)
    assert len(fake.requests) == 1 and sleeps == [] and not isinstance(raised.value, LLMUnavailable)


async def test_missing_credentials_point_at_aws_login():
    client, fake, _ = make([NoCredentialsError()])
    with pytest.raises(LLMError, match="aws login"):
        await client.chat(HI)
    assert len(fake.requests) == 1


async def test_an_empty_reply_is_retried_with_some_randomness_then_reported():
    client, fake, _ = make([reply("", stop="end_turn"), reply("now it answers")])
    out = await client.chat(HI)
    assert out.text == "now it answers" and out.retries[0].startswith("EmptyCompletion")
    assert fake.requests[1]["inferenceConfig"]["temperature"] == bedrock.RETRY_TEMPERATURE
    client, _, _ = make([reply("")] * 2, retries=1)
    with pytest.raises(LLMUnavailable, match="EmptyCompletion"):
        await client.chat(HI)
    assert issubclass(EmptyCompletion, LLMError)


async def test_a_history_with_tool_calls_needs_the_tools_that_Bedrock_demands():
    call = ToolCall("a", "lookup", {})
    client, fake, _ = make([reply("x")])
    with pytest.raises(LLMError, match="no tools were offered"):
        await client.chat(
            [Message.user("q"), Message.assistant("", (call,)), Message.tool(call, "1")]
        )
    assert fake.requests == []


# --------------------------------------------------------------------------- the profile
def test_the_bedrock_profile_reads_model_region_and_credentials_profile_from_the_environment():
    p = load_profile("bedrock-claude", env=ENV)
    assert (p.kind, p.model, p.region, p.aws_profile) == (
        "bedrock_converse",
        MODEL,
        "us-west-2",
        "demo",
    )
    assert p.base_url == "" and p.api_key is None and not p.synthetic_data_only
    assert (p.price_in_per_mtok, p.price_out_per_mtok) == (1.10, 5.50)


def test_without_a_region_or_profile_the_defaults_are_the_documented_ones():
    p = load_profile("bedrock-claude", env={"BEDROCK_CHAT_MODEL": MODEL})
    assert (p.region, p.aws_profile) == ("us-east-1", None), (
        "None = the default credential chain (Lambda)"
    )


def test_a_missing_model_names_the_variable():
    with pytest.raises(ProfileError, match="BEDROCK_CHAT_MODEL"):
        load_profile("bedrock-claude", env={"AWS_PROFILE": "demo"})


def test_the_factory_builds_a_bedrock_client_for_the_profile(monkeypatch):
    from legacybridge.agent.factory import build_llm

    for key, value in ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(bedrock, "make_client", lambda p: FakeBedrock([]))
    client = build_llm("bedrock-claude")
    assert isinstance(client, BedrockConverseClient) and client.name == "bedrock-claude"


# --------------------------------------------------------------------------- the SDK rule
def test_provider_sdks_are_imported_only_by_the_layers_that_may_know_them():
    """CLAUDE.md: the agent never imports a provider SDK. boto3 may also live in `aws/`, which holds
    the deployment plumbing (secrets, job store) and no model logic."""
    src = Path(__file__).resolve().parents[1] / "src" / "legacybridge"
    allowed = {"llm", "aws"}
    offenders = []
    for path in src.rglob("*.py"):
        if path.relative_to(src).parts[0] in allowed:
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else (
                [node.module or ""] if isinstance(node, ast.ImportFrom) else [])  # fmt: skip
            offenders += [f"{path.relative_to(src)}: {n}" for n in names
                          if n.split(".")[0] in {"boto3", "botocore", "openai"}]  # fmt: skip
    assert offenders == []
