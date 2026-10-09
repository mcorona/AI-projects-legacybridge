"""The contract of a documentation search that backs ``explain_business_term`` (ADR-014).

The ontology stays the authority for what a term means and for the canonical SQL of a metric. A
knowledge base only adds passages of documentation for a term the ontology does not know, and it
is always optional: when it is absent or fails, the tool answers from the ontology alone.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class Passage:
    text: str
    source: str  # the document it came from, e.g. legacy-defects.md
    score: float | None = None


class KnowledgeBase(Protocol):
    def retrieve(self, query: str) -> list[Passage]: ...
