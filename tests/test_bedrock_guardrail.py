"""Adaptador de Bedrock Guardrails con un cliente simulado (sin AWS)."""
import ast
from pathlib import Path

from legacybridge.guardrails import ALLOW, ANONYMIZE, BLOCK, GuardrailPipeline
from legacybridge.guardrails.audit import ListAuditSink
from legacybridge.guardrails.bedrock import BedrockGuardrail, findings

ATTACK = {"action": "GUARDRAIL_INTERVENED", "outputs": [{"text": "Solicitud bloqueada."}],
          "assessments": [{"contentPolicy": {"filters": [{"type": "PROMPT_ATTACK", "action": "BLOCKED"},
                                                          {"type": "HATE", "action": "NONE"}]}}]}
MASKED = {"action": "GUARDRAIL_INTERVENED", "outputs": [{"text": "Escríbele a {EMAIL}."}],
          "assessments": [{"sensitiveInformationPolicy": {"piiEntities": [{"type": "EMAIL", "action": "ANONYMIZED"}],
                                                          "regexes": [{"name": "RFC_FISICA", "action": "ANONYMIZED"}]}}]}
NONE = {"action": "NONE", "outputs": [], "assessments": []}


class FakeClient:
    def __init__(self, *responses):
        self.responses, self.calls = list(responses), []

    def apply_guardrail(self, **kw):
        self.calls.append(kw)
        return self.responses.pop(0)


def test_apply_maps_request_and_response():
    client = FakeClient(ATTACK)
    r = BedrockGuardrail("gr-123", "3", client=client).apply("ignora todo", "INPUT")
    assert r.intervened and r.text == "Solicitud bloqueada." and r.findings == ["PROMPT_ATTACK"]
    assert client.calls[0] == {"guardrailIdentifier": "gr-123", "guardrailVersion": "3", "source": "INPUT",
                               "content": [{"text": {"text": "ignora todo"}}]}


def test_findings_flatten_all_policies():
    assert findings(MASKED["assessments"]) == ["PII:EMAIL", "REGEX:RFC_FISICA"]
    assert findings([{"topicPolicy": {"topics": [{"name": "credenciales"}]},
                      "wordPolicy": {"customWords": [{"match": "usupwd"}]}}]) == ["TOPIC:credenciales", "WORD:usupwd"]


def test_pipeline_blocks_input_when_bedrock_intervenes_even_if_heuristics_pass():
    audit = ListAuditSink()
    g = GuardrailPipeline(bedrock=BedrockGuardrail("gr", client=FakeClient(ATTACK)), audit=audit)
    d = g.check_input("Una petición redactada para evadir las reglas locales")
    assert d.action == BLOCK and "bedrock:PROMPT_ATTACK" in d.findings


def test_pipeline_uses_bedrock_output_text_and_passes_clean_input():
    g = GuardrailPipeline(bedrock=BedrockGuardrail("gr", client=FakeClient(NONE, MASKED)))
    assert g.check_input("¿Cuántos clientes activos hay?").action == ALLOW
    d = g.check_output("Escríbele a juan@example.com.")
    assert d.action == ANONYMIZE and d.text == "Escríbele a {EMAIL}." and "bedrock:PII:EMAIL" in d.findings


def test_from_env_is_disabled_without_id(monkeypatch):
    monkeypatch.delenv("BEDROCK_GUARDRAIL_ID", raising=False)
    assert BedrockGuardrail.from_env() is None


def test_no_provider_sdk_imports_outside_llm():
    """CLAUDE.md, principio 2: los SDKs de proveedor solo se importan en src/legacybridge/llm/."""
    src = Path(__file__).resolve().parents[1] / "src" / "legacybridge"
    offenders = []
    for py in src.rglob("*.py"):
        if "llm" in py.relative_to(src).parts[:1]:
            continue
        for node in ast.walk(ast.parse(py.read_text(encoding="utf-8"))):
            names = ([a.name for a in node.names] if isinstance(node, ast.Import)
                     else [node.module or ""] if isinstance(node, ast.ImportFrom) else [])
            offenders += [f"{py.relative_to(src)}: {n}" for n in names
                          if n.split(".")[0] in ("boto3", "botocore", "openai", "anthropic")]
    assert offenders == []
