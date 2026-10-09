"""A short digest of the ontology for the prompts (business knowledge never lives in them)."""

from __future__ import annotations

from legacybridge.semantic.loader import Ontology


def _first_sentence(text: str, limit: int = 150) -> str:
    text = " ".join(text.split())
    cut = text.find(". ")
    text = text[: cut + 1] if 0 < cut < limit else text
    return text if len(text) <= limit else text[: limit - 1] + "…"


def years_covered(o: Ontology) -> tuple[int, int] | None:
    spans = [
        p for m in o.metrics for p in m.params.values() if "year" in (p.description or "").lower()
    ]
    return (min(p.min for p in spans), max(p.max for p in spans)) if spans else None


def digest(o: Ontology, *, terms: bool = True) -> str:
    lines = []
    if years := years_covered(o):
        lines.append(f"Data covers calendar years {years[0]} to {years[1]}.")
    lines.append("Canonical metrics (explain_business_term returns their SQL):")
    for m in o.metrics:
        params = f" [{', '.join(m.params)}]" if m.params else ""
        lines.append(f"- {m.name}{params}: {_first_sentence(m.description)}")
    lines.append("Entities and their tables:")
    lines += [f"- {e.name}: {', '.join(e.tables)}" for e in o.entities]
    if terms:
        lines.append("Business terms:")
        lines += [f"- {t.name}: {_first_sentence(t.definition)}" for t in o.terms]
    return "\n".join(lines)
