"""Unidad de indexado (`Chunk`) y chunking de Markdown por secciones.

`chunk_markdown` está portado de inventory-copilot (`src/rag/chunking.py`): cada sección `##`
es un chunk; si excede `max_chars` se parte por párrafos con traslape, y cada chunk lleva un
encabezado de contexto ("Documento > Sección") para que el embedding no pierda su origen.
Aquí se usa para manuales en `docs/`; el conocimiento del esquema se arma en `rag.sources`.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

MAX_CHARS = 1600      # ~400 tokens
OVERLAP_CHARS = 200

_H1_RE = re.compile(r"^#\s+(.+)$", re.MULTILINE)
_H2_RE = re.compile(r"^##\s+(.+)$", re.MULTILINE)


@dataclass(frozen=True)
class Chunk:
    source: str               # identificador estable, p. ej. 'table:pedenc'
    kind: str                 # ddl | table | rule | catalog | defect | doc
    content: str
    ref: str | None = None    # tabla / regla / defecto al que se refiere
    index: int = 0
    metadata: dict = field(default_factory=dict, compare=False, hash=False)

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(self.content.encode("utf-8")).hexdigest()


def _split_long(text: str, max_chars: int, overlap: int) -> list[str]:
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    parts, current = [], ""
    for p in paragraphs:
        if current and len(current) + len(p) + 2 > max_chars:
            parts.append(current)
            current = current[-overlap:].lstrip() + "\n\n" + p if overlap else p
        else:
            current = f"{current}\n\n{p}" if current else p
    if current:
        parts.append(current)
    return parts


def chunk_markdown(text: str, source: str, max_chars: int = MAX_CHARS,
                   overlap: int = OVERLAP_CHARS) -> list[Chunk]:
    m = _H1_RE.search(text)
    title = m.group(1).strip() if m else source
    heads = list(_H2_RE.finditer(text))
    sections: list[tuple[str, str]] = []
    # el preámbulo (entre el título y la primera ##) solo se indexa si tiene contenido propio
    pre_start = m.end() if m else 0
    preamble = text[pre_start: heads[0].start() if heads else len(text)]
    # las líneas de cita (> ...) son notas de control del documento, no contenido
    preamble = "\n".join(ln for ln in preamble.splitlines() if not ln.lstrip().startswith(">")).strip()
    if preamble:
        sections.append(("Introducción", preamble))
    for i, h in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else len(text)
        body = text[h.end():end].strip()
        if body:
            sections.append((h.group(1).strip(), body))

    chunks: list[Chunk] = []
    for section, body in sections:
        for part in _split_long(body, max_chars, overlap):
            chunks.append(Chunk(source=source, kind="doc", index=len(chunks), ref=section,
                                content=f"Documento: {title} > {section}\n\n{part}",
                                metadata={"title": title, "section": section}))
    return chunks
