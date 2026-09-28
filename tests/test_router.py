"""Pruebas de la cascada de `llm.router.chat()` con backends falsos (sin red ni costo)."""
import pytest

from legacybridge.llm import router

CONF = {
    "cascade": {"order": ["local", "omniroute", "bedrock"]},
    "providers": {
        "local": {"kind": "fake_local", "strip_think": True,
                  "cost_per_mtok": {"input": 0.0, "output": 0.0}},
        "omniroute": {"kind": "fake_omni", "strip_think": True,
                      "cost_per_mtok": {"input": 0.0, "output": 0.0}},
        "bedrock": {"kind": "fake_bedrock", "strip_think": False,
                    "cost_per_mtok": {"input": 1.0, "output": 5.0}},
    },
}


def _backend(model, text, tin, tout, stop="stop", exc=None):
    def call(cfg, messages, system, **kw):
        if exc:
            raise exc
        return model, text, tin, tout, stop
    return call


@pytest.fixture
def fake(monkeypatch):
    """Registra backends falsos; cada prueba define su comportamiento con `fake(...)`."""
    monkeypatch.setattr(router, "load_config", lambda: CONF)
    backends: dict = {}
    monkeypatch.setattr(router, "_BACKENDS", backends)

    def configure(local=None, omni=None, bedrock=None):
        backends["fake_local"] = local or _backend("qwen", "SELECT 1", 10, 5)
        backends["fake_omni"] = omni or _backend("omni", "SELECT 1", 10, 5)
        backends["fake_bedrock"] = bedrock or _backend("haiku", "SELECT 1", 10, 5, "end_turn")
    return configure


MSG = [{"role": "user", "content": "Responde solo: SELECT 1"}]


def test_local_ok_does_not_escalate(fake):
    fake(local=_backend("qwen", "<think>pienso</think>\nSELECT 1", 12, 40))
    r = router.chat(MSG, provider="cascade")
    assert (r.provider, r.text, r.stop_reason) == ("local", "SELECT 1", "stop")
    assert r.attempts == ["local: ok"]
    assert not r.truncated


def test_truncated_reasoning_escalates(fake):
    """Caso real de Fase 0: Qwen agota max_tokens dentro de <think> y el contenido llega vacío."""
    fake(local=_backend("qwen", "<think>Here's a thinking process", 20, 63, "length"))
    r = router.chat(MSG, provider="cascade")
    assert r.provider == "omniroute"
    assert r.attempts == ["local: empty(length)", "omniroute: ok"]


def test_empty_content_without_think_escalates(fake):
    """LM Studio separa el razonamiento en `reasoning_content`: `content` llega ''."""
    fake(local=_backend("qwen", "", 20, 63, "length"),
         omni=_backend("omni", "   \n", 10, 1, "stop"))
    r = router.chat(MSG, provider="cascade")
    assert r.provider == "bedrock"
    assert r.attempts == ["local: empty(length)", "omniroute: empty(stop)", "bedrock: ok"]


def test_provider_exception_escalates(fake):
    fake(local=_backend("", "", 0, 0, exc=ConnectionError("LM Studio apagado")))
    r = router.chat(MSG, provider="cascade")
    assert r.provider == "omniroute"
    assert r.attempts == ["local: ConnectionError", "omniroute: ok"]


def test_single_provider_empty_raises_with_cause(fake):
    fake(local=_backend("qwen", "", 20, 63, "length"))
    with pytest.raises(RuntimeError, match=r"local: empty\(length\)") as ei:
        router.chat(MSG, provider="local")
    cause = ei.value.__cause__
    assert isinstance(cause, router.EmptyCompletionError)
    assert cause.stop_reason == "length" and "max_tokens" in str(cause)


def test_all_providers_fail(fake):
    boom = _backend("", "", 0, 0, exc=TimeoutError())
    fake(local=boom, omni=boom, bedrock=_backend("haiku", "", 5, 0, "max_tokens"))
    with pytest.raises(RuntimeError, match="Todos los proveedores fallaron"):
        router.chat(MSG, provider="cascade")


def test_tokens_and_cost_accumulate_across_attempts(fake):
    """Los tokens gastados en un intento fallido forman parte del costo real."""
    fake(local=_backend("qwen", "", 100, 200, "length"),
         omni=_backend("", "", 0, 0, exc=ConnectionError()),
         bedrock=_backend("haiku", "SELECT 1", 1_000, 100, "end_turn"))
    r = router.chat(MSG, provider="cascade")
    assert (r.input_tokens, r.output_tokens) == (1_100, 300)
    # solo Bedrock cobra: 1000 in * $1/M + 100 out * $5/M
    assert r.cost_usd == pytest.approx(0.0015)


def test_truncated_but_non_empty_is_returned_and_flagged(fake):
    """Texto parcial no escala: se devuelve marcado para que el agente decida."""
    fake(bedrock=_backend("haiku", "SELECT clinom FROM", 10, 64, "max_tokens"))
    r = router.chat(MSG, provider="bedrock")
    assert r.text == "SELECT clinom FROM" and r.truncated


def test_strip_think_not_applied_when_disabled(fake):
    fake(bedrock=_backend("haiku", "<think>x</think>SELECT 1", 1, 1, "end_turn"))
    assert router.chat(MSG, provider="bedrock").text.startswith("<think>")


def test_unknown_provider_is_config_error(fake):
    fake()
    with pytest.raises(KeyError):
        router.chat(MSG, provider="nope")


# ---------------------------------------------------------------- tool calls

CALL = router.ToolCall("c1", "find_columns", {"concept": "moneda"})


def test_tool_calls_without_text_are_not_empty(fake):
    """Qwen devuelve content '\\n\\n' junto con tool_calls: no es una respuesta vacía."""
    fake(local=lambda cfg, m, s, **kw: ("qwen", "\n\n", 50, 20, "tool_calls", (CALL,)))
    r = router.chat(MSG, provider="cascade", tools=[{"name": "find_columns"}])
    assert r.provider == "local" and r.tool_calls == (CALL,) and r.text == ""


def test_tools_are_forwarded_to_backend(fake):
    seen = {}

    def local(cfg, messages, system, tools=None, **kw):
        seen.update(tools=tools, max_tokens=kw.get("max_tokens"))
        return "qwen", "ok", 1, 1, "stop"
    fake(local=local)
    router.chat(MSG, provider="local", tools=[{"name": "t"}], max_tokens=99)
    assert seen == {"tools": [{"name": "t"}], "max_tokens": 99}


# ---------------------------------------------------------------- embeddings

EMB_CONF = {**CONF, "embeddings": {"default": "local", "providers": {
    "local": {"kind": "fake_embed", "model_env": "X", "dims": 3, "cost_per_mtok": {"input": 0.0}},
    "paid": {"kind": "fake_embed", "model_env": "X", "dims": 3, "cost_per_mtok": {"input": 2.0}},
}}}


@pytest.fixture
def fake_embed(monkeypatch):
    monkeypatch.setattr(router, "load_config", lambda: EMB_CONF)
    monkeypatch.delenv("EMBED_PROVIDER", raising=False)
    impl = {}
    monkeypatch.setattr(router, "_EMBED_BACKENDS", {"fake_embed": lambda cfg, texts: impl["fn"](texts)})

    def configure(fn):
        impl["fn"] = fn
    return configure


def test_embed_returns_vectors_and_model_id(fake_embed):
    fake_embed(lambda texts: ("bge-m3", [[0.1, 0.2, 0.3] for _ in texts], 7))
    r = router.embed(["a", "b"])
    assert len(r.vectors) == 2 and r.model_id == "local:bge-m3" and r.input_tokens == 7


def test_embed_provider_from_env_and_cost(fake_embed, monkeypatch):
    monkeypatch.setenv("EMBED_PROVIDER", "paid")
    fake_embed(lambda texts: ("titan", [[0.0] * 3], 500_000))
    r = router.embed(["a"])
    assert r.provider == "paid" and r.cost_usd == pytest.approx(1.0)


def test_embed_rejects_wrong_dimension(fake_embed):
    fake_embed(lambda texts: ("m", [[0.1, 0.2]], 1))
    with pytest.raises(RuntimeError, match="dimensión 2 != 3"):
        router.embed(["a"])


def test_embed_rejects_count_mismatch(fake_embed):
    fake_embed(lambda texts: ("m", [[0.1, 0.2, 0.3]], 1))
    with pytest.raises(RuntimeError, match="1 vectores para 2 textos"):
        router.embed(["a", "b"])


def test_embed_empty_input_does_not_call_backend(fake_embed):
    fake_embed(lambda texts: pytest.fail("no debe llamarse"))
    assert router.embed([]).vectors == []
