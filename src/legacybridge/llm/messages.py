"""Formato neutro de mensajes y tools, y su traducción a cada API de proveedor.

Portado de inventory-copilot (`src/llm/__init__.py`). Formato neutro:
    {"role": "user", "content": "..."}
    {"role": "assistant", "content": "...", "tool_calls": [ToolCall, ...]}   # tool_calls opcional
    {"role": "tool", "tool_call_id": "...", "name": "...", "content": "..."}

Tools: {"name", "description", "parameters": <JSON Schema>}.
Uso interno de `llm/`: el resto del código importa estos tipos desde `llm.router`.
"""
from __future__ import annotations

import json
from dataclasses import dataclass


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict


def parse_arguments(raw) -> dict:
    """Los modelos a veces devuelven JSON inválido: se conserva el texto en `_raw` para diagnóstico."""
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(raw or "{}")
        return parsed if isinstance(parsed, dict) else {"_raw": raw}
    except json.JSONDecodeError:
        return {"_raw": raw}


# ---------------------------------------------------------------- OpenAI-compatible (LM Studio, OmniRoute)

def to_openai_messages(messages: list[dict], system: str | None) -> list[dict]:
    out = [{"role": "system", "content": system}] if system else []
    for m in messages:
        if m["role"] == "assistant" and m.get("tool_calls"):
            out.append({"role": "assistant", "content": m.get("content") or None,
                        "tool_calls": [{"id": c.id, "type": "function",
                                        "function": {"name": c.name,
                                                     "arguments": json.dumps(c.arguments, ensure_ascii=False)}}
                                       for c in m["tool_calls"]]})
        elif m["role"] == "tool":
            out.append({"role": "tool", "tool_call_id": m["tool_call_id"], "content": m["content"]})
        else:
            out.append({"role": m["role"], "content": m["content"]})
    return out


def to_openai_tools(tools: list[dict]) -> list[dict]:
    return [{"type": "function", "function": t} for t in tools]


def from_openai_tool_calls(calls) -> tuple[ToolCall, ...]:
    return tuple(ToolCall(c.id, c.function.name, parse_arguments(c.function.arguments))
                 for c in (calls or []))


# ---------------------------------------------------------------- Bedrock Converse

def to_converse_messages(messages: list[dict]) -> list[dict]:
    """Converse exige alternar user/assistant: los resultados de tools van en un turno `user`
    con bloques toolResult, y los mensajes consecutivos del mismo rol se fusionan."""
    out: list[dict] = []
    for m in messages:
        if m["role"] == "tool":
            role, blocks = "user", [{"toolResult": {"toolUseId": m["tool_call_id"],
                                                    "content": [{"text": m["content"]}]}}]
        elif m["role"] == "assistant":
            role = "assistant"
            blocks = [{"text": m["content"]}] if m.get("content") else []
            blocks += [{"toolUse": {"toolUseId": c.id, "name": c.name, "input": c.arguments}}
                       for c in m.get("tool_calls") or []]
        else:
            role, blocks = "user", [{"text": m["content"]}]
        if out and out[-1]["role"] == role:
            out[-1]["content"].extend(blocks)
        else:
            out.append({"role": role, "content": blocks})
    return out


def to_converse_tool_config(tools: list[dict]) -> dict:
    return {"tools": [{"toolSpec": {"name": t["name"], "description": t["description"],
                                    "inputSchema": {"json": t["parameters"]}}} for t in tools]}


def from_converse_output(r: dict) -> tuple[str, tuple[ToolCall, ...]]:
    text, calls = [], []
    for block in r["output"]["message"]["content"]:
        if "text" in block:
            text.append(block["text"])
        elif "toolUse" in block:
            tu = block["toolUse"]
            calls.append(ToolCall(tu["toolUseId"], tu["name"], parse_arguments(tu["input"])))
    return "".join(text), tuple(calls)
