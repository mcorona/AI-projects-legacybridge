"""Filtro de salida contra fuga de secretos (DLP): última capa ante inyección indirecta.

Portado de inventory-copilot (`src/guardrails/secrets.py`), cuya lección fue que ninguna defensa de
ENTRADA detiene del todo a un dato envenenado: hay que revisar la SALIDA sin importar cómo llegó el
contenido. Aquí se agregan los nombres de tablas restringidas (D8, D10): el sistema nunca los revela.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from legacybridge.dictionary import SENSITIVE_TABLES

REDACTED = "[dato sensible retirado]"
RESTRICTED_TABLE = "[tabla restringida]"

CREDENTIAL_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("connection_string", re.compile(r"\b(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis)://\S+", re.IGNORECASE)),
    ("aws_access_key", re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")),
    ("api_key", re.compile(r"\b(?:sk|pk|rk)-[A-Za-z0-9_-]{20,}\b")),
    ("private_key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("bearer_token", re.compile(r"\bBearer\s+[A-Za-z0-9._~+/-]{20,}=*", re.IGNORECASE)),
    ("password_hash", re.compile(r"\b[a-f0-9]{32}\b|\$2[aby]\$\d{2}\$[./A-Za-z0-9]{53}")),   # md5 / bcrypt
]
_TABLES = re.compile(r"\b(" + "|".join(sorted(SENSITIVE_TABLES)) + r")\b", re.IGNORECASE)


@dataclass
class SecretScanResult:
    text: str
    findings: list[str] = field(default_factory=list)


def redact_secrets(text: str, known_secrets: list[str] | None = None) -> SecretScanResult:
    """Retira secretos registrados, credenciales y nombres de tablas restringidas."""
    findings = []
    for secret in known_secrets or []:
        if secret and re.search(re.escape(secret), text, re.IGNORECASE):
            text = re.sub(re.escape(secret), REDACTED, text, flags=re.IGNORECASE)
            findings.append("known_secret")
    for name, pattern in CREDENTIAL_PATTERNS:
        if pattern.search(text):
            text = pattern.sub(REDACTED, text)
            findings.append(name)
    if _TABLES.search(text):
        text = _TABLES.sub(RESTRICTED_TABLE, text)
        findings.append("restricted_table_name")
    return SecretScanResult(text, findings)
