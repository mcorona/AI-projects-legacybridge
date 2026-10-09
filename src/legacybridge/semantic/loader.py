"""Loads the ontology (src/legacybridge/semantic/ontology.yaml)."""

from __future__ import annotations

import difflib
from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

ONTOLOGY_PATH = Path(__file__).with_name("ontology.yaml")


class Term(BaseModel):
    name: str
    aliases: list[str] = Field(default_factory=list)
    definition: str
    defects: list[str] = Field(default_factory=list)


class Defect(BaseModel):
    id: str
    title: str
    columns: list[str]
    warning: str


class TableDoc(BaseModel):
    name: str
    system: str
    description: str


class Ontology(BaseModel):
    terms: list[Term]
    defects: list[Defect]
    tables: list[TableDoc] = Field(default_factory=list)

    def table(self, name: str) -> TableDoc | None:
        return next((t for t in self.tables if t.name == name), None)

    def defect(self, defect_id: str) -> Defect | None:
        return next((d for d in self.defects if d.id == defect_id), None)

    def defects_for_column(self, table: str, column: str) -> list[Defect]:
        key = f"{table}.{column}".lower()
        return [d for d in self.defects if key in {c.lower() for c in d.columns}]

    def lookup(self, term: str) -> Term | None:
        wanted = _normalize(term)
        for t in self.terms:
            if wanted in {_normalize(t.name), *(_normalize(a) for a in t.aliases)}:
                return t
        return None

    def suggest(self, term: str, n: int = 3) -> list[str]:
        names = {_normalize(x): t.name for t in self.terms for x in (t.name, *t.aliases)}
        close = difflib.get_close_matches(_normalize(term), list(names), n=n, cutoff=0.75)
        return sorted({names[c] for c in close})


def _normalize(text: str) -> str:
    return " ".join(text.lower().split())


@lru_cache(maxsize=1)
def load_ontology(path: Path = ONTOLOGY_PATH) -> Ontology:
    return Ontology.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
