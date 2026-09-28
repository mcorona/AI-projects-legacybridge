"""Capa LLM multiproveedor con cascada y medición de costo/latencia.

El resto del código solo usa `chat()` y `embed()` (y los tipos `ToolCall`, `LLMResult`,
`EmbedResult`); nunca importa SDKs de proveedor.
"""
from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import NamedTuple

import yaml

from legacybridge.llm.messages import (ToolCall, from_converse_output, from_openai_tool_calls,
                                       to_converse_messages, to_converse_tool_config,
                                       to_openai_messages, to_openai_tools)

__all__ = ["ToolCall", "LLMResult", "EmbedResult", "EmptyCompletionError", "chat", "embed",
           "embed_model_id", "bedrock_runtime_client", "strip_think", "extract_sql"]

CONFIG = Path(__file__).resolve().parents[3] / "config" / "models.yaml"
_THINK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_SQL_FENCE = re.compile(r"```(?:sql)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)
# Motivos de paro que indican que se agotó max_tokens (OpenAI-compat / Bedrock Converse).
TRUNCATION_STOPS = {"length", "max_tokens"}


class EmptyCompletionError(RuntimeError):
    """El proveedor respondió sin texto útil (p. ej. Qwen agotó max_tokens razonando).

    Se trata como falla del proveedor para que la cascada escale (ver ADR-002).
    """

    def __init__(self, provider: str, stop_reason: str):
        self.provider, self.stop_reason = provider, stop_reason
        hint = " — sube max_tokens (el razonamiento consumió el presupuesto)" \
            if stop_reason in TRUNCATION_STOPS else ""
        super().__init__(f"{provider}: respuesta vacía (stop_reason={stop_reason or 'n/a'}){hint}")


@dataclass
class LLMResult:
    text: str
    provider: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    latency_s: float = 0.0
    cost_usd: float = 0.0
    stop_reason: str = ""
    attempts: list[str] = field(default_factory=list)
    tool_calls: tuple[ToolCall, ...] = ()

    @property
    def truncated(self) -> bool:
        """True si la respuesta se cortó por max_tokens (el texto puede estar incompleto)."""
        return self.stop_reason in TRUNCATION_STOPS


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


class BackendReply(NamedTuple):
    """Contrato de todo backend de chat (ver ADR-002)."""
    model: str
    text: str
    input_tokens: int
    output_tokens: int
    stop_reason: str
    tool_calls: tuple[ToolCall, ...] = ()


def _openai_client(cfg: dict):
    from openai import OpenAI

    return OpenAI(base_url=os.environ[cfg["base_url_env"]],
                  api_key=os.environ.get(cfg.get("api_key_env", ""), "lm-studio"))


def _bedrock_client():
    import boto3
    from botocore.config import Config

    session = boto3.Session(profile_name=os.environ.get("AWS_PROFILE"),
                            region_name=os.environ.get("AWS_REGION", "us-east-1"))
    # Reintentos adaptativos: absorben ThrottlingException sin escalar en falso.
    return session.client("bedrock-runtime",
                          config=Config(retries={"max_attempts": 5, "mode": "adaptive"}))


def bedrock_runtime_client():
    """Cliente `bedrock-runtime` (perfil/región del entorno) para capas que no son chat, como
    Bedrock Guardrails: así ningún módulo fuera de `llm/` importa el SDK del proveedor."""
    return _bedrock_client()


def _call_openai_compat(cfg: dict, messages: list[dict], system: str | None,
                        tools: list[dict] | None = None, **kw) -> BackendReply:
    model = os.environ[cfg["model_env"]]
    req = dict(model=model, messages=to_openai_messages(messages, system),
               temperature=kw.get("temperature", 0.0), max_tokens=kw.get("max_tokens", 2048))
    if tools:
        req["tools"] = to_openai_tools(tools)
    r = _openai_client(cfg).chat.completions.create(**req)
    u, choice = r.usage, r.choices[0]
    return BackendReply(model, choice.message.content or "", (u.prompt_tokens if u else 0),
                        (u.completion_tokens if u else 0), choice.finish_reason or "",
                        from_openai_tool_calls(choice.message.tool_calls))


def _call_bedrock(cfg: dict, messages: list[dict], system: str | None,
                  tools: list[dict] | None = None, **kw) -> BackendReply:
    model = os.environ[cfg["model_env"]]
    req = {
        "modelId": model,
        "messages": to_converse_messages(messages),
        "inferenceConfig": {"temperature": kw.get("temperature", 0.0),
                            "maxTokens": kw.get("max_tokens", 2048)},
    }
    if system:
        req["system"] = [{"text": system}]
    if tools:
        req["toolConfig"] = to_converse_tool_config(tools)
    r = _bedrock_client().converse(**req)
    text, calls = from_converse_output(r)
    return BackendReply(model, text, r["usage"]["inputTokens"], r["usage"]["outputTokens"],
                        r.get("stopReason", ""), calls)


_BACKENDS = {"openai_compat": _call_openai_compat, "bedrock_converse": _call_bedrock}


def chat(messages: list[dict], system: str | None = None, provider: str | None = None,
         tools: list[dict] | None = None, **kw) -> LLMResult:
    """Llama al proveedor indicado; con `cascade` prueba en orden hasta que uno responda.

    - `messages` y `tools` usan el formato neutro de `llm.messages`; la respuesta trae
      `tool_calls` si el modelo pidió herramientas.
    - Una respuesta sin texto (tras quitar `<think>`) y sin tool calls cuenta como falla y
      escala al siguiente proveedor (ADR-002); si no hay siguiente, lanza `RuntimeError`.
    - Tokens, costo y latencia se acumulan en todos los intentos: es el costo real
      de la consulta, no solo el del proveedor que respondió.
    """
    conf = load_config()
    provider = provider or os.environ.get("LLM_PROVIDER", "local")
    order = conf["cascade"]["order"] if provider == "cascade" else [provider]
    attempts: list[str] = []
    tin_total = tout_total = 0
    cost_total = 0.0
    t_start = time.perf_counter()
    last_err: Exception | None = None
    for name in order:
        cfg = conf["providers"][name]
        try:
            reply = BackendReply(*_BACKENDS[cfg["kind"]](cfg, messages, system, tools=tools, **kw))
        except Exception as e:  # noqa: BLE001 — fallback de cascada
            attempts.append(f"{name}: {type(e).__name__}")
            last_err = e
            continue
        model, text, tin, tout, stop, calls = reply
        tin_total, tout_total = tin_total + tin, tout_total + tout
        cost_total += _cost(cfg, tin, tout)
        text = strip_think(text) if cfg.get("strip_think") else text
        if not text.strip() and not calls:
            last_err = EmptyCompletionError(name, stop)
            attempts.append(f"{name}: empty({stop or 'n/a'})")
            continue
        attempts.append(f"{name}: ok")
        return LLMResult(text.strip() if calls else text, name, model, tin_total, tout_total,
                         time.perf_counter() - t_start, cost_total, stop, attempts, tuple(calls))
    raise RuntimeError(f"Todos los proveedores fallaron: {attempts}") from last_err


# ---------------------------------------------------------------- embeddings

@dataclass
class EmbedResult:
    vectors: list[list[float]]
    provider: str
    model: str
    input_tokens: int = 0
    latency_s: float = 0.0
    cost_usd: float = 0.0

    @property
    def model_id(self) -> str:
        """Etiqueta estable del espacio vectorial: vectores de modelos distintos no se comparan."""
        return f"{self.provider}:{self.model}"


def _embed_openai_compat(cfg: dict, texts: list[str]) -> tuple[str, list[list[float]], int]:
    model = os.environ[cfg["model_env"]]
    r = _openai_client(cfg).embeddings.create(model=model, input=texts)
    usage = getattr(r, "usage", None)
    return model, [d.embedding for d in sorted(r.data, key=lambda d: d.index)], \
        (getattr(usage, "prompt_tokens", 0) or 0)


def _embed_bedrock_titan(cfg: dict, texts: list[str]) -> tuple[str, list[list[float]], int]:
    import json

    model, client = os.environ[cfg["model_env"]], _bedrock_client()
    vectors, tokens = [], 0
    for t in texts:   # Titan v2 embebe un texto por llamada
        r = json.loads(client.invoke_model(modelId=model, body=json.dumps(
            {"inputText": t, "dimensions": cfg.get("dims", 1024), "normalize": True}))["body"].read())
        vectors.append(r["embedding"])
        tokens += r.get("inputTextTokenCount", 0)
    return model, vectors, tokens


_EMBED_BACKENDS = {"openai_compat": _embed_openai_compat, "bedrock_titan": _embed_bedrock_titan}


def _embed_provider(provider: str | None) -> tuple[str, dict]:
    conf = load_config()["embeddings"]
    name = provider or os.environ.get("EMBED_PROVIDER") or conf["default"]
    return name, conf["providers"][name]


def embed_model_id(provider: str | None = None) -> str:
    """Id del espacio vectorial que usará `embed()` (sin llamar al modelo)."""
    name, cfg = _embed_provider(provider)
    return f"{name}:{os.environ[cfg['model_env']]}"


def embed(texts: list[str], provider: str | None = None) -> EmbedResult:
    """Embeddings con un proveedor FIJO (sin cascada: un índice no puede mezclar modelos).

    Proveedor: argumento, `EMBED_PROVIDER` o `embeddings.default` de config/models.yaml.
    Verifica que la dimensión coincida con la configurada (la columna vector(N) del índice).
    """
    name, cfg = _embed_provider(provider)
    if not texts:
        return EmbedResult([], name, os.environ.get(cfg["model_env"], ""))
    t0 = time.perf_counter()
    model, vectors, tokens = _EMBED_BACKENDS[cfg["kind"]](cfg, list(texts))
    if len(vectors) != len(texts):
        raise RuntimeError(f"{name}: {len(vectors)} vectores para {len(texts)} textos")
    dims = cfg.get("dims")
    if dims and any(len(v) != dims for v in vectors):
        raise RuntimeError(f"{name}: dimensión {len(vectors[0])} != {dims} configurada")
    per_mtok = cfg.get("cost_per_mtok", {}).get("input", 0)
    return EmbedResult(vectors, name, model, tokens, time.perf_counter() - t0,
                       tokens * per_mtok / 1_000_000)
