"""Traducción del formato neutro de mensajes/tools a OpenAI y Bedrock Converse (sin red)."""
from types import SimpleNamespace as NS

from legacybridge.llm.messages import (ToolCall, from_converse_output, from_openai_tool_calls,
                                       parse_arguments, to_converse_messages,
                                       to_converse_tool_config, to_openai_messages, to_openai_tools)

CALL = ToolCall("c1", "find_columns", {"concept": "fecha del pedido"})
CONVERSATION = [
    {"role": "user", "content": "¿Dónde está la fecha?"},
    {"role": "assistant", "content": "", "tool_calls": [CALL]},
    {"role": "tool", "tool_call_id": "c1", "name": "find_columns", "content": '{"matches": []}'},
    {"role": "assistant", "content": "En pedenc.pedfec"},
]
SPEC = {"name": "find_columns", "description": "d",
        "parameters": {"type": "object", "properties": {"concept": {"type": "string"}}}}


def test_to_openai_messages():
    out = to_openai_messages(CONVERSATION, "sistema")
    assert out[0] == {"role": "system", "content": "sistema"}
    assert out[2]["content"] is None   # asistente que solo llama tools
    fn = out[2]["tool_calls"][0]
    assert fn["id"] == "c1" and fn["function"]["arguments"] == '{"concept": "fecha del pedido"}'
    assert out[3] == {"role": "tool", "tool_call_id": "c1", "content": '{"matches": []}'}


def test_to_openai_tools():
    assert to_openai_tools([SPEC]) == [{"type": "function", "function": SPEC}]


def test_from_openai_tool_calls_handles_invalid_json():
    calls = from_openai_tool_calls([
        NS(id="a", function=NS(name="x", arguments='{"k": 1}')),
        NS(id="b", function=NS(name="y", arguments="{roto")),
    ])
    assert calls == (ToolCall("a", "x", {"k": 1}), ToolCall("b", "y", {"_raw": "{roto"}))
    assert from_openai_tool_calls(None) == ()


def test_to_converse_messages_alternates_roles():
    out = to_converse_messages(CONVERSATION + [{"role": "user", "content": "gracias"}])
    assert [m["role"] for m in out] == ["user", "assistant", "user", "assistant", "user"]
    assert out[1]["content"] == [{"toolUse": {"toolUseId": "c1", "name": "find_columns",
                                              "input": {"concept": "fecha del pedido"}}}]
    assert out[2]["content"][0]["toolResult"]["toolUseId"] == "c1"


def test_to_converse_merges_consecutive_tool_results():
    msgs = [{"role": "user", "content": "q"},
            {"role": "assistant", "content": "", "tool_calls": [CALL, ToolCall("c2", "list_tables", {})]},
            {"role": "tool", "tool_call_id": "c1", "name": "a", "content": "1"},
            {"role": "tool", "tool_call_id": "c2", "name": "b", "content": "2"}]
    out = to_converse_messages(msgs)
    assert len(out) == 3 and len(out[2]["content"]) == 2


def test_converse_tool_config_and_output():
    cfg = to_converse_tool_config([SPEC])
    assert cfg["tools"][0]["toolSpec"]["inputSchema"] == {"json": SPEC["parameters"]}
    text, calls = from_converse_output({"output": {"message": {"content": [
        {"text": "Voy a buscar."}, {"toolUse": {"toolUseId": "t1", "name": "find_columns",
                                                "input": {"concept": "moneda"}}}]}}})
    assert text == "Voy a buscar." and calls == (ToolCall("t1", "find_columns", {"concept": "moneda"}),)


def test_parse_arguments():
    assert parse_arguments({"a": 1}) == {"a": 1}
    assert parse_arguments("") == {}
    assert parse_arguments("[1]") == {"_raw": "[1]"}
