"""Adaptador a Amazon Bedrock Guardrails (ApplyGuardrail), capa OPCIONAL del pipeline.

Portado de inventory-copilot (`src/guardrails/bedrock.py`). Evalúa entrada o salida con un guardrail
administrado (ataques de prompt, filtros de contenido, PII, temas denegados) sin invocar un modelo.
Se activa con BEDROCK_GUARDRAIL_ID; el cliente lo crea `llm.router` (única capa que conoce el SDK).
Decisión de la Fase 4 (ADR-006): adaptador probado con respuestas simuladas; no se crean recursos
en AWS desde este repositorio.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass
class BedrockGuardrailResult:
    intervened: bool
    text: str                      # texto de salida (enmascarado si hubo ANONYMIZE)
    findings: list[str] = field(default_factory=list)
    raw: dict = field(default_factory=dict, repr=False)


class BedrockGuardrail:
    def __init__(self, guardrail_id: str, version: str = "DRAFT", client=None):
        if client is None:
            from legacybridge.llm.router import bedrock_runtime_client
            client = bedrock_runtime_client()
        self.client, self.guardrail_id, self.version = client, guardrail_id, version

    @classmethod
    def from_env(cls) -> "BedrockGuardrail | None":
        gid = os.environ.get("BEDROCK_GUARDRAIL_ID")
        return cls(gid, os.environ.get("BEDROCK_GUARDRAIL_VERSION", "DRAFT")) if gid else None

    def apply(self, text: str, source: str = "INPUT") -> BedrockGuardrailResult:
        r = self.client.apply_guardrail(guardrailIdentifier=self.guardrail_id, guardrailVersion=self.version,
                                        source=source, content=[{"text": {"text": text}}])
        intervened = r.get("action") == "GUARDRAIL_INTERVENED"
        outputs = r.get("outputs") or []
        out_text = outputs[0].get("text", text) if outputs else text
        return BedrockGuardrailResult(intervened, out_text, findings(r.get("assessments") or []), r)


def findings(assessments: list[dict]) -> list[str]:
    """Aplana las evaluaciones de Bedrock a etiquetas legibles ('PROMPT_ATTACK', 'PII:EMAIL', …)."""
    out = []
    for a in assessments:
        for f in a.get("contentPolicy", {}).get("filters", []):
            if f.get("action") != "NONE":
                out.append(f.get("type", "CONTENT"))
        for t in a.get("topicPolicy", {}).get("topics", []):
            out.append(f"TOPIC:{t.get('name')}")
        for e in a.get("sensitiveInformationPolicy", {}).get("piiEntities", []):
            out.append(f"PII:{e.get('type')}")
        for r in a.get("sensitiveInformationPolicy", {}).get("regexes", []):
            out.append(f"REGEX:{r.get('name')}")
        for w in a.get("wordPolicy", {}).get("customWords", []):
            out.append(f"WORD:{w.get('match')}")
    return out
