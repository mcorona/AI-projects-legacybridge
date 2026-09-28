"""Diccionario de negocio del ERP legacy: carga, validación e índices.

Fuente única para los MCP servers (allowlist del guard, metadatos de columnas, reglas).
La validación es estricta y ocurre al cargar: un diccionario inconsistente no arranca.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import yaml

DEFAULT_PATH = Path(__file__).resolve().parents[2] / "config" / "business_dictionary.yaml"
# D8 y D10: nunca consultables, pase lo que pase en el YAML.
SENSITIVE_TABLES = frozenset({"ctrlhis", "usupwd"})
_JOIN = re.compile(r"^\s*(\w+)\.(\w+)\s*=\s*(\w+)\.(\w+)\s*(?:--\s*(.*))?$")


class DictionaryError(ValueError):
    """El diccionario de negocio es inconsistente o viola una regla de seguridad."""


def normalize(text: str) -> str:
    """Minúsculas sin acentos ni espacios repetidos: 'Línea  ' -> 'linea'."""
    text = unicodedata.normalize("NFKD", str(text))
    text = "".join(c for c in text if not unicodedata.combining(c))
    return " ".join(text.lower().split())


@dataclass(frozen=True)
class Column:
    table: str
    name: str
    meaning: str
    synonyms: tuple[str, ...] = ()
    defects: tuple[str, ...] = ()
    rule: str | None = None


@dataclass(frozen=True)
class Table:
    name: str
    concept: str
    description: str
    key: tuple[str, ...]
    synonyms: tuple[str, ...]
    columns: dict[str, Column]


@dataclass(frozen=True)
class Join:
    left: tuple[str, str]
    right: tuple[str, str]
    note: str = ""

    @property
    def sql(self) -> str:
        return f"{self.left[0]}.{self.left[1]} = {self.right[0]}.{self.right[1]}"

    def involves(self, table: str) -> bool:
        return table in (self.left[0], self.right[0])


@dataclass(frozen=True)
class Rule:
    name: str
    text: str
    terms: tuple[str, ...] = ()


@dataclass(frozen=True)
class BusinessDictionary:
    tables: dict[str, Table]
    joins: tuple[Join, ...]
    rules: dict[str, Rule]
    catalogs: dict[str, dict[str, str]]
    defects: dict[str, dict[str, str]]
    source: Path = field(default=DEFAULT_PATH, compare=False)

    @property
    def allowed_tables(self) -> frozenset[str]:
        return frozenset(self.tables)

    def columns(self):
        for t in self.tables.values():
            yield from t.columns.values()


def _tuple(v) -> tuple[str, ...]:
    if v is None:
        return ()
    return tuple(v) if isinstance(v, list) else (v,)


def parse(raw: dict, source: Path = DEFAULT_PATH) -> BusinessDictionary:
    """Construye y valida el diccionario a partir del YAML ya cargado."""
    errors: list[str] = []
    allowed = [t.lower() for t in raw.get("allowed_tables", [])]
    if leaked := SENSITIVE_TABLES & set(allowed):
        raise DictionaryError(f"tablas sensibles en allowed_tables: {sorted(leaked)}")
    raw_tables = {k.lower(): v for k, v in (raw.get("tables") or {}).items()}
    if set(allowed) != set(raw_tables):
        errors.append(f"allowed_tables {sorted(allowed)} != tables documentadas {sorted(raw_tables)}")

    defects = {str(k): v for k, v in (raw.get("defects") or {}).items()}
    rules = {name: Rule(name, v["text"], _tuple(v.get("terms"))) if isinstance(v, dict)
             else Rule(name, str(v)) for name, v in (raw.get("rules") or {}).items()}

    tables: dict[str, Table] = {}
    for tname, t in raw_tables.items():
        cols = {}
        for cname, c in (t.get("columns") or {}).items():
            col = Column(tname, cname.lower(), c.get("meaning", ""), _tuple(c.get("synonyms")),
                         _tuple(c.get("defects")), c.get("rule"))
            if not col.meaning:
                errors.append(f"{tname}.{cname}: falta 'meaning'")
            errors += [f"{tname}.{cname}: defecto desconocido {d}" for d in col.defects if d not in defects]
            if col.rule and col.rule not in rules:
                errors.append(f"{tname}.{cname}: regla desconocida {col.rule}")
            cols[col.name] = col
        if not cols:
            errors.append(f"{tname}: sin columnas documentadas")
        key = tuple(k.lower() for k in _tuple(t.get("key")))
        errors += [f"{tname}: llave {k} no es columna" for k in key if k not in cols]
        tables[tname] = Table(tname, t.get("concept", ""), t.get("description", ""), key,
                              _tuple(t.get("synonyms")), cols)

    joins = []
    for j in raw.get("joins") or []:
        m = _JOIN.match(j)
        if not m:
            errors.append(f"join ilegible: {j!r}")
            continue
        lt, lc, rt, rc, note = m.groups()
        for tb, cl in ((lt, lc), (rt, rc)):
            if cl not in getattr(tables.get(tb), "columns", {}):
                errors.append(f"join {j!r}: {tb}.{cl} no existe en el diccionario")
        joins.append(Join((lt, lc), (rt, rc), (note or "").strip()))

    catalogs = {k: {str(code): str(label) for code, label in v.items()}
                for k, v in (raw.get("catalogs") or {}).items()}
    all_cols = {c.name for t in tables.values() for c in t.columns.values()}
    errors += [f"catálogo {k} no corresponde a ninguna columna" for k in catalogs if k not in all_cols]

    if errors:
        raise DictionaryError("diccionario inválido:\n- " + "\n- ".join(errors))
    return BusinessDictionary(tables, tuple(joins), rules, catalogs, defects, source)


@lru_cache(maxsize=4)
def load(path: Path | str = DEFAULT_PATH) -> BusinessDictionary:
    path = Path(path)
    return parse(yaml.safe_load(path.read_text(encoding="utf-8")), path)
