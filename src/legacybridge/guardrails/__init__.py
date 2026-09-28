"""Guardrails de LegacyBridge (Fase 4): inyección, PII, DLP, auditoría y Bedrock opcional. Ver ADR-006."""
from legacybridge.guardrails.pipeline import ALLOW, ANONYMIZE, BLOCK, Decision, GuardrailPipeline, NoGuardrails

__all__ = ["ALLOW", "ANONYMIZE", "BLOCK", "Decision", "GuardrailPipeline", "NoGuardrails"]
