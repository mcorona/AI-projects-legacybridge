"""Pipeline de guardrails en capas (portado de inventory-copilot, `src/guardrails/pipeline.py`).

    check_input(text)           PII financiera (bloquear) -> inyección directa (reglas, LLM y
                                Bedrock opcionales)
    sanitize_tool_result(r)     inyección indirecta (D9): retira strings con instrucciones
    wrap_tool_output(name, s)   spotlighting: delimita la salida como datos no confiables,
                                escapando cierres falsos del delimitador
    check_output(text)          DLP: credenciales, tablas restringidas y PII en la respuesta

Cada intervención queda en la bitácora de auditoría.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

from legacybridge.guardrails.audit import AuditSink, NullAuditSink
from legacybridge.guardrails.injection import LLMInjectionClassifier, detect_injection, normalize
from legacybridge.guardrails.pii import DEFAULT_INPUT_ACTIONS, DEFAULT_OUTPUT_ACTIONS, apply_pii_policy
from legacybridge.guardrails.secrets import redact_secrets

ALLOW, ANONYMIZE, BLOCK = "ALLOW", "ANONYMIZE", "BLOCK"
REMOVED = "[contenido retirado por guardrail: posible instrucción inyectada]"
BLOCK_MESSAGES = {
    "pii": "No puedo procesar esa solicitud porque contiene datos financieros sensibles ({kinds}). "
           "Elimínalos y vuelve a intentarlo.",
    "injection": "No puedo procesar esa solicitud: parece un intento de cambiar mis instrucciones o de "
                 "saltarse controles de seguridad. Reformula tu pregunta sobre los datos del ERP.",
    "topic": "No tengo acceso a credenciales, contraseñas ni cuentas de usuario del sistema, y no puedo "
             "consultarlas ni describirlas. Puedo ayudarte con clientes, artículos, existencias y pedidos.",
}
# Temas denegados (equivalente local de los "denied topics" de Bedrock Guardrails), sobre texto
# normalizado. Términos inequívocos: "clave" a secas es la clave de un cliente o artículo.
DENIED_TOPICS = {
    "credentials": re.compile(
        r"\b(contrasen\w*|password\w*|passwd|credencial\w*|clave(s)? de acceso|hash(es)? de|"
        r"usuarios? (del|de la) (sistema|base|bd)|cuentas? de usuario|tabla de usuarios|login(s)? de)\b"),
}


@dataclass
class Decision:
    action: str               # ALLOW | ANONYMIZE | BLOCK
    text: str                 # texto a usar (enmascarado si aplica)
    findings: list[str] = field(default_factory=list)
    message: str = ""         # mensaje para el usuario si BLOCK


class GuardrailPipeline:
    def __init__(self, pii_input_actions: dict | None = None, pii_output_actions: dict | None = None,
                 detect_injection_input: bool = True, sanitize_tools: bool = True, spotlight: bool = True,
                 classifier: LLMInjectionClassifier | None = None, bedrock=None,
                 audit: AuditSink | None = None, actor: str = "agent", known_secrets: list[str] | None = None):
        self.pii_input_actions = pii_input_actions or DEFAULT_INPUT_ACTIONS
        self.pii_output_actions = pii_output_actions or DEFAULT_OUTPUT_ACTIONS
        self.detect_injection_input = detect_injection_input
        self.sanitize_tools = sanitize_tools
        self.spotlight = spotlight
        self.classifier = classifier
        self.bedrock = bedrock
        self.audit = audit or NullAuditSink()
        self.actor = actor
        self.known_secrets = list(known_secrets or [])

    @classmethod
    def from_env(cls, audit: AuditSink | None = None) -> "GuardrailPipeline":
        from legacybridge.guardrails.audit import get_audit_sink
        classifier = None
        if os.environ.get("GUARDRAIL_LLM_CLASSIFIER", "off").lower() in ("1", "on", "true"):
            classifier = LLMInjectionClassifier(os.environ.get("GUARDRAIL_CLASSIFIER_PROVIDER"))
        bedrock = None
        if os.environ.get("BEDROCK_GUARDRAIL_ID"):
            from legacybridge.guardrails.bedrock import BedrockGuardrail
            bedrock = BedrockGuardrail.from_env()
        return cls(classifier=classifier, bedrock=bedrock, audit=audit or get_audit_sink())

    # ------------------------------------------------------------ entrada
    def check_input(self, text: str) -> Decision:
        pii = apply_pii_policy(text, self.pii_input_actions)
        findings = [f"pii:{m.kind}" for m in pii.matches]
        if pii.blocked:
            return self._block("pii", text, findings, kinds=", ".join(pii.blocked_kinds))
        norm = normalize(text)
        topics = [t for t, rx in DENIED_TOPICS.items() if rx.search(norm)]
        if topics:
            return self._block("topic", text, findings + [f"topic:{t}" for t in topics])
        if self.detect_injection_input:
            inj = detect_injection(text)
            if not inj.flagged and self.classifier is not None:
                inj = self.classifier.classify(text)
            if inj.flagged:
                return self._block("injection", text, findings + [f"injection:{r}" for r in inj.rules])
        if self.bedrock is not None:
            br = self.bedrock.apply(text, "INPUT")
            if br.intervened:
                return self._block("injection", text, findings + [f"bedrock:{f}" for f in br.findings])
        return Decision(ALLOW, text, findings)

    def _block(self, kind: str, text: str, findings: list[str], **fmt) -> Decision:
        self.audit.log(self.actor, "guardrail_input_blocked",
                       {"kind": kind, "findings": findings,
                        "preview": apply_pii_policy(text, DEFAULT_OUTPUT_ACTIONS).text[:200]})
        return Decision(BLOCK, "", findings, BLOCK_MESSAGES[kind].format(**fmt))

    # ------------------------------------------------------------ salidas de herramientas
    def sanitize_tool_result(self, tool: str, result):
        """Recorre la estructura y reemplaza cada string con instrucciones inyectadas."""
        if not self.sanitize_tools:
            return result, []
        findings: list[str] = []

        def walk(node, path):
            if isinstance(node, dict):
                return {k: walk(v, f"{path}.{k}") for k, v in node.items()}
            if isinstance(node, list):
                return [walk(v, f"{path}[{i}]") for i, v in enumerate(node)]
            if isinstance(node, str) and len(node) > 12:
                inj = detect_injection(node)
                if inj.flagged:
                    findings.append(f"{path}:{'+'.join(inj.rules)}")
                    return REMOVED
            return node

        clean = walk(result, tool)
        if findings:
            self.audit.log(self.actor, "guardrail_tool_output_sanitized", {"tool": tool, "findings": findings})
        return clean, findings

    def wrap_tool_output(self, tool: str, text: str) -> str:
        if not self.spotlight:
            return text
        # un cierre falso dentro de los datos ("</tool_output> Nuevo rol…") no puede romper el delimitador
        safe = text.replace("</tool_output", "<\\/tool_output").replace("<tool_output", "<\\tool_output")
        return f'<tool_output tool="{tool}" trust="untrusted">\n{safe}\n</tool_output>'

    # ------------------------------------------------------------ respuesta
    def check_output(self, text: str) -> Decision:
        leak = redact_secrets(text, self.known_secrets)
        pii = apply_pii_policy(leak.text, self.pii_output_actions)
        findings = [f"secret:{f}" for f in leak.findings] + [f"pii:{m.kind}" for m in pii.matches]
        if self.bedrock is not None:
            br = self.bedrock.apply(pii.text, "OUTPUT")
            if br.intervened:
                findings += [f"bedrock:{f}" for f in br.findings]
                pii.text = br.text
        if findings:
            self.audit.log(self.actor, "guardrail_output_redacted", {"findings": findings})
            return Decision(ANONYMIZE, pii.text, findings)
        return Decision(ALLOW, text)


class NoGuardrails(GuardrailPipeline):
    """Línea base para evaluaciones: sin ninguna defensa (el delimitador tampoco se aplica)."""

    def __init__(self):
        super().__init__(detect_injection_input=False, sanitize_tools=False, spotlight=False)

    def check_input(self, text):
        return Decision(ALLOW, text)

    def check_output(self, text):
        return Decision(ALLOW, text)
