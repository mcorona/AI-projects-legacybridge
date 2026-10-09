"""Loads the ontology (src/legacybridge/semantic/ontology.yaml) into validated models (ADR-006)."""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field

ONTOLOGY_PATH = Path(__file__).with_name("ontology.yaml")
_FRAGMENT = re.compile(r"\{\{(\w+)\}\}")


class Rule(BaseModel):
    """A machine-checkable rule for a defect (see semantic/defect_rules.py)."""

    kind: Literal["sql", "data"] = "sql"
    check: str
    severity: Literal["warning", "info"] = "warning"
    params: dict[str, Any] = Field(default_factory=dict)
    message: str
    fix: str
    bad_sql: list[str] = Field(default_factory=list)  # each must violate the rule
    good_sql: list[str] = Field(default_factory=list)  # each must NOT violate it
    bad_cells: list[dict[str, str]] = Field(default_factory=list)  # data rules: {column, value}
    good_cells: list[dict[str, str]] = Field(default_factory=list)


class Defect(BaseModel):
    id: str
    title: str
    columns: list[str]
    warning: str
    rule: Rule


class Lint(BaseModel):
    """A general check that is not tied to a catalogued defect."""

    id: str
    title: str
    rule: Rule


class Term(BaseModel):
    name: str
    aliases: list[str] = Field(default_factory=list)
    definition: str
    defects: list[str] = Field(default_factory=list)
    requires_period: bool = False  # the figure is meaningless without a period


class TableDoc(BaseModel):
    name: str
    system: str
    description: str


class Entity(BaseModel):
    name: str
    aliases: list[str] = Field(default_factory=list)
    description: str
    tables: list[str]
    key: list[str]  # qualified "table.column"; an equality on one of them is a single-row lookup
    columns: dict[str, str] = Field(default_factory=dict)  # role -> qualified column
    defects: list[str] = Field(default_factory=list)


class JoinEnd(BaseModel):
    table: str
    alias: str


class Join(BaseModel):
    name: str
    left: JoinEnd
    right: JoinEnd
    condition: str  # the ON expression (the key is not called `on`: YAML reads that as a boolean)
    cardinality: str
    join_type: Literal["INNER", "LEFT"]
    description: str
    defects: list[str] = Field(default_factory=list)


class Param(BaseModel):
    type: Literal["int"] = "int"
    min: int
    max: int
    description: str = ""


class Metric(BaseModel):
    name: str
    aliases: list[str] = Field(default_factory=list)
    description: str
    params: dict[str, Param] = Field(default_factory=dict)
    defects: list[str] = Field(default_factory=list)
    sql: str


@dataclass
class Resolved:
    kind: str  # metric | entity | term | defect | join
    item: Any


class Ontology(BaseModel):
    terms: list[Term]
    defects: list[Defect]
    tables: list[TableDoc] = Field(default_factory=list)
    entities: list[Entity] = Field(default_factory=list)
    joins: list[Join] = Field(default_factory=list)
    metrics: list[Metric] = Field(default_factory=list)
    lints: list[Lint] = Field(default_factory=list)
    fragments: dict[str, str] = Field(default_factory=dict)

    # ------------------------------------------------------------------ simple lookups
    def table(self, name: str) -> TableDoc | None:
        return next((t for t in self.tables if t.name == name), None)

    def defect(self, defect_id: str) -> Defect | None:
        return next((d for d in self.defects if d.id.lower() == defect_id.lower()), None)

    def defects_for_column(self, table: str, column: str) -> list[Defect]:
        key = f"{table}.{column}".lower()
        return [d for d in self.defects if key in {c.lower() for c in d.columns}]

    def entity_for_table(self, table: str) -> Entity | None:
        return next((e for e in self.entities if table in e.tables), None)

    def key_columns(self, table: str) -> set[str]:
        """Columns of ``table`` that identify a row (an equality on one is a single-row lookup)."""
        return {
            k.split(".", 1)[1] for e in self.entities for k in e.key if k.startswith(f"{table}.")
        }

    def joins_for_table(self, table: str) -> list[Join]:
        return [j for j in self.joins if table in (j.left.table, j.right.table)]

    def metric(self, name: str) -> Metric | None:
        wanted = _normalize(name)
        for m in self.metrics:
            if wanted in {_normalize(m.name), *(_normalize(a) for a in m.aliases)}:
                return m
        return None

    # ------------------------------------------------------------------ names
    def lookup(self, term: str) -> Term | None:
        wanted = _normalize(term)
        for t in self.terms:
            if wanted in {_normalize(t.name), *(_normalize(a) for a in t.aliases)}:
                return t
        return None

    def resolve(self, text: str) -> Resolved | None:
        """Find what a word refers to: a metric, an entity, a term, a defect id or a join."""
        wanted = _normalize(text)
        if m := self.metric(text):
            return Resolved("metric", m)
        if t := self.lookup(text):  # a term wins over an entity of the same name
            return Resolved("term", t)
        for e in self.entities:
            if wanted in {_normalize(e.name), *(_normalize(a) for a in e.aliases)}:
                return Resolved("entity", e)
        if d := self.defect(text):
            return Resolved("defect", d)
        for j in self.joins:
            if wanted == _normalize(j.name):
                return Resolved("join", j)
        return None

    def terms_in(self, text: str) -> list[Term]:
        """Glossary terms that appear in ``text`` (whole-word match on a name or a synonym)."""
        lowered = _normalize(text)
        found = []
        for t in self.terms:
            for label in (t.name, *t.aliases):
                if re.search(rf"(?<!\w){re.escape(_normalize(label))}(?!\w)", lowered):
                    found.append(t)
                    break
        return found

    def suggest(self, term: str, n: int = 3) -> list[str]:
        names: dict[str, str] = {}
        for item in [*self.metrics, *self.entities, *self.terms]:
            for label in (item.name, *item.aliases):
                names[_normalize(label)] = item.name
        close = difflib.get_close_matches(_normalize(term), list(names), n=n, cutoff=0.75)
        return sorted({names[c] for c in close})

    # ------------------------------------------------------------------ SQL text
    def expand(self, sql: str) -> str:
        """Replace ``{{fragment}}`` placeholders with the shared SQL fragments."""

        def sub(match: re.Match) -> str:
            name = match.group(1)
            if name not in self.fragments:
                raise KeyError(f"unknown fragment {{{{{name}}}}}")
            return self.fragments[name].strip()

        return _FRAGMENT.sub(sub, sql).strip()

    def render_metric(self, name: str, **params: Any) -> str:
        """The canonical SQL of a metric with its parameters validated and substituted."""
        metric = self.metric(name)
        if metric is None:
            raise KeyError(f"unknown metric {name!r}")
        unknown = set(params) - set(metric.params)
        missing = set(metric.params) - set(params)
        if unknown or missing:
            raise ValueError(
                f"{metric.name} takes {sorted(metric.params) or 'no parameters'}; "
                f"missing {sorted(missing)}, unexpected {sorted(unknown)}"
            )
        sql = self.expand(metric.sql)
        for key, spec in metric.params.items():
            value = params[key]
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"{metric.name}.{key} must be an integer")
            if not spec.min <= value <= spec.max:
                raise ValueError(f"{metric.name}.{key} must be between {spec.min} and {spec.max}")
            sql = sql.replace(f"{{{key}}}", str(value))
        return sql


def _normalize(text: str) -> str:
    return " ".join(text.lower().split())


@lru_cache(maxsize=1)
def load_ontology(path: Path = ONTOLOGY_PATH) -> Ontology:
    return Ontology.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
