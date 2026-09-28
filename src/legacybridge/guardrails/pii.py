"""Detección y enmascarado de PII con formatos de México.

Portado de inventory-copilot (`src/guardrails/pii.py`): regex + validadores (Luhn, dígito de control
de CLABE, fecha en RFC/CURP) para no confundir cantidades, montos o claves con datos personales.
Acciones con la terminología de Bedrock Guardrails: ANONYMIZE reemplaza por [TIPO_n]; BLOCK rechaza.

Cambio clave (ADR-006): el RFC se distingue por titular. El de persona FÍSICA (4 letras, 13
caracteres) es dato personal (LFPDPPP) y se enmascara; el de persona MORAL (3 letras, 12
caracteres) es dato de negocio y se permite.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

ALLOW, ANONYMIZE, BLOCK = "ALLOW", "ANONYMIZE", "BLOCK"

# Entrada: los datos financieros no se procesan. Salida: se enmascara todo dato personal.
DEFAULT_INPUT_ACTIONS = {"CARD": BLOCK, "CLABE": BLOCK}
DEFAULT_OUTPUT_ACTIONS = {"EMAIL": ANONYMIZE, "PHONE": ANONYMIZE, "RFC_FISICA": ANONYMIZE,
                          "CURP": ANONYMIZE, "CARD": ANONYMIZE, "CLABE": ANONYMIZE, "RFC_MORAL": ALLOW}

_DATE6 = r"\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])"
PATTERNS: list[tuple[str, re.Pattern]] = [   # orden = prioridad ante traslapes
    ("EMAIL", re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b")),
    ("CURP", re.compile(rf"\b[A-Z][AEIOUX][A-Z]{{2}}{_DATE6}[HM][A-Z]{{5}}[A-Z\d]\d\b", re.IGNORECASE)),
    ("RFC_FISICA", re.compile(rf"\b[A-ZÑ&]{{4}}{_DATE6}[A-Z\d]{{3}}\b", re.IGNORECASE)),
    ("RFC_MORAL", re.compile(rf"\b[A-ZÑ&]{{3}}{_DATE6}[A-Z\d]{{3}}\b", re.IGNORECASE)),
    ("CLABE", re.compile(r"(?<![\d-])\d{18}(?![\d-])")),
    ("CARD", re.compile(r"(?<![\d-])(?:\d[ -]?){12,18}\d(?![\d-])")),
    ("PHONE", re.compile(r"(?<![\w.,-])(?:\+?52[ .-]?)?(?:\(?\d{2,3}\)?[ .-])\d{3,4}[ .-]?\d{4}(?![\w-])|"
                         r"(?<![\w.,-])\+?52[ .-]?\d{10}(?![\w-])")),
]


def luhn_ok(digits: str) -> bool:
    total, parity = 0, len(digits) % 2
    for i, ch in enumerate(digits):
        d = int(ch)
        if i % 2 == parity:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def clabe_ok(digits: str) -> bool:
    weights = [3, 7, 1] * 6
    s = sum((int(d) * w) % 10 for d, w in zip(digits[:17], weights))
    return (10 - s % 10) % 10 == int(digits[17])


def _valid(kind: str, raw: str) -> bool:
    digits = re.sub(r"\D", "", raw)
    if kind == "CARD":
        return 13 <= len(digits) <= 19 and luhn_ok(digits)
    if kind == "CLABE":
        return clabe_ok(digits)
    if kind == "PHONE":
        return len(digits) in (10, 12)
    return True


@dataclass
class PIIMatch:
    kind: str
    start: int
    end: int
    value: str


@dataclass
class PIIResult:
    text: str
    matches: list[PIIMatch] = field(default_factory=list)
    blocked_kinds: list[str] = field(default_factory=list)

    @property
    def blocked(self) -> bool:
        return bool(self.blocked_kinds)


def find_pii(text: str) -> list[PIIMatch]:
    taken: list[tuple[int, int]] = []
    found: list[PIIMatch] = []
    for kind, pattern in PATTERNS:
        for m in pattern.finditer(text):
            if any(m.start() < e and s < m.end() for s, e in taken) or not _valid(kind, m.group()):
                continue
            taken.append((m.start(), m.end()))
            found.append(PIIMatch(kind, m.start(), m.end(), m.group()))
    return sorted(found, key=lambda x: x.start)


def apply_pii_policy(text: str, actions: dict[str, str]) -> PIIResult:
    """Enmascara según la política (ALLOW deja el valor); reporta los tipos con acción BLOCK."""
    matches = [m for m in find_pii(text) if actions.get(m.kind, ALLOW) != ALLOW]
    labels: dict[tuple[str, str], str] = {}
    counters: dict[str, int] = {}
    out, pos = [], 0
    for m in matches:
        key = (m.kind, m.value.upper())
        if key not in labels:
            counters[m.kind] = counters.get(m.kind, 0) + 1
            labels[key] = f"[{m.kind}_{counters[m.kind]}]"
        out += [text[pos:m.start], labels[key]]
        pos = m.end
    out.append(text[pos:])
    blocked = sorted({m.kind for m in matches if actions.get(m.kind) == BLOCK})
    return PIIResult("".join(out), matches, blocked)
