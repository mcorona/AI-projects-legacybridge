"""Capa LLM multiproveedor con cascada y medición de costo/latencia.

El resto del código solo usa `chat()`; nunca importa SDKs de proveedor.
"""
from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

import yaml

CONFIG = Path(__file__).resolve().parents[3] / "config" / "models.yaml"
_THINK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_SQL_FENCE = re.compile(r"```(?:sql)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)


@dataclass
class LLMResult:
    text: str
    provider: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    latency_s: float = 0.0
    cost_usd: float = 0.0
    attempts: list[str] = field(default_factory=list)


def strip_think(text: str) -> str:
    """Elimina bloques <think>…</think> (Qwen3.x) y un <think> sin cerrar al final."""
    text = _THINK.sub("", text)
    if "<think>" in text.lower():
        text = text[: text.lower().index("<think>")]
    return text.strip()


def extract_sql(text: str) -> str:
    """Devuelve la SQL de un bloque ```sql``` o el texto completo si no hay bloque."""
    m = _SQL_FENCE.search(text)
    return (m.group(1) if m else text).strip().rstrip(";").strip()


def load_config() -> dict:
    return yaml.safe_load(CONFIG.read_text())


def _cost(cfg: dict, tin: int, tout: int) -> float:
    p = cfg.get("cost_per_mtok", {})
    return (tin * p.get("input", 0) + tout * p.get("output", 0)) / 1_000_000


def _call_openai_compat(cfg: dict, messages: list[dict], system: str | None, **kw):
    from openai import OpenAI

    client = OpenAI(
        base_url=os.environ[cfg["base_url_env"]],
        api_key=os.environ.get(cfg.get("api_key_env", ""), "lm-studio"),
    )
    model = os.environ[cfg["model_env"]]
    msgs = ([{"role": "system", "content": system}] if system else []) + messages
    r = client.chat.completions.create(model=model, messages=msgs,
                                       temperature=kw.get("temperature", 0.0),
                                       max_tokens=kw.get("max_tokens", 2048))
    u = r.usage
    return model, r.choices[0].message.content or "", (u.prompt_tokens if u else 0), (u.completion_tokens if u else 0)


def _call_bedrock(cfg: dict, messages: list[dict], system: str | None, **kw):
    import boto3

    session = boto3.Session(profile_name=os.environ.get("AWS_PROFILE"),
                            region_name=os.environ.get("AWS_REGION", "us-east-1"))
    client = session.client("bedrock-runtime")
    model = os.environ[cfg["model_env"]]
    req = {
        "modelId": model,
        "messages": [{"role": m["role"], "content": [{"text": m["content"]}]} for m in messages],
        "inferenceConfig": {"temperature": kw.get("temperature", 0.0),
                            "maxTokens": kw.get("max_tokens", 2048)},
    }
    if system:
        req["system"] = [{"text": system}]
    r = client.converse(**req)
    text = "".join(b.get("text", "") for b in r["output"]["message"]["content"])
    return model, text, r["usage"]["inputTokens"], r["usage"]["outputTokens"]


_BACKENDS = {"openai_compat": _call_openai_compat, "bedrock_converse": _call_bedrock}


def chat(messages: list[dict], system: str | None = None, provider: str | None = None,
         **kw) -> LLMResult:
    """Llama al proveedor indicado; con `cascade` prueba en orden hasta que uno responda."""
    conf = load_config()
    provider = provider or os.environ.get("LLM_PROVIDER", "local")
    order = conf["cascade"]["order"] if provider == "cascade" else [provider]
    attempts: list[str] = []
    last_err: Exception | None = None
    for name in order:
        cfg = conf["providers"][name]
        t0 = time.perf_counter()
        try:
            model, text, tin, tout = _BACKENDS[cfg["kind"]](cfg, messages, system, **kw)
        except Exception as e:  # noqa: BLE001 — fallback de cascada
            attempts.append(f"{name}: {type(e).__name__}")
            last_err = e
            continue
        if cfg.get("strip_think"):
            text = strip_think(text)
        attempts.append(f"{name}: ok")
        return LLMResult(text, name, model, tin, tout, time.perf_counter() - t0,
                         _cost(cfg, tin, tout), attempts)
    raise RuntimeError(f"Todos los proveedores fallaron: {attempts}") from last_err
