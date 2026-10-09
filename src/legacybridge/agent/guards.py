"""Text guardrails around the agent (ADR-014): one check on the question, one on what comes out.

The agent depends on this contract only; the implementation that calls Amazon Bedrock Guardrails
lives in ``llm/bedrock_guardrails.py``. With no guard configured nothing here runs and the agent
behaves exactly as before.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal, Protocol

GuardAction = Literal["none", "blocked", "masked"]


@dataclass(frozen=True)
class GuardVerdict:
    action: GuardAction = "none"
    text: str = ""  # the text to use: the input unchanged, or with the sensitive parts masked
    findings: tuple[str, ...] = ()  # readable labels such as PROMPT_ATTACK or PII:EMAIL

    @property
    def intervened(self) -> bool:
        return self.action != "none"


class TextGuard(Protocol):
    async def check_input(self, text: str) -> GuardVerdict: ...

    async def check_output(self, text: str) -> GuardVerdict: ...


_ENGLISH = re.compile(r"\b(the|what|which|how|many|much|show|list|are|is|of|per|by|with)\b", re.I)


def guess_language(text: str) -> str:
    """``en`` when the text reads as English, else ``es``. Used only to word a refusal when no
    model has run yet (so the planner's language is not known)."""
    return "en" if len(_ENGLISH.findall(text)) >= 2 else "es"
