"""Construye los fragmentos de conocimiento del esquema que se indexan en pgvector.

Fuentes (todas del repositorio, sin datos del ERP):
- DDL de las tablas permitidas (`db/legacy/01_schema.sql`, filtrado por la allowlist).
- Una ficha por tabla, una por regla y una por catálogo (`config/business_dictionary.yaml`).
- Un fragmento por defecto (`docs/LEGACY_DEFECTS.md`).

Seguridad: el índice nunca revela tablas fuera de la allowlist. Solo se indexa DDL de tablas
permitidas y todo el texto pasa por `redact()` (ver ADR-004).
"""
from __future__ import annotations

import re
from pathlib import Path

from legacybridge.dictionary import SENSITIVE_TABLES, BusinessDictionary
from legacybridge.dictionary import load as load_dictionary
from legacybridge.rag.chunking import Chunk

ROOT = Path(__file__).resolve().parents[3]
SCHEMA_SQL = ROOT / "db" / "legacy" / "01_schema.sql"
DEFECTS_MD = ROOT / "docs" / "LEGACY_DEFECTS.md"
_REDACTIONS = {"ctrlhis": "[tabla histórica restringida]", "usupwd": "[tabla de credenciales restringida]"}
_CREATE = re.compile(r"CREATE TABLE\s+(\w+)\s*\((.*?)\n\);", re.S | re.I)
_DEFECT_ROW = re.compile(r"^\|\s*(D\d+)\s*\|(.+?)\|(.+?)\|(.+?)\|\s*$", re.M)

assert set(_REDACTIONS) == SENSITIVE_TABLES, "cada tabla sensible necesita su redacción"


def redact(text: str) -> str:
    for name, label in _REDACTIONS.items():
        text = re.sub(rf"\b{name}\b", label, text, flags=re.I)
    return text


def ddl_chunks(d: BusinessDictionary, schema_sql: str) -> list[Chunk]:
    out = []
    for m in _CREATE.finditer(schema_sql):
        table = m.group(1).lower()
        if table not in d.allowed_tables:
            continue
        body = "\n".join(ln if ln.lstrip()[:2] != "--" else "    " + ln.strip()
                         for ln in m.group(2).strip("\n").splitlines())
        ddl = f"CREATE TABLE {table} (\n{body.rstrip()}\n);"
        out.append(Chunk(f"ddl:{table}", "ddl", f"DDL de la tabla {table} ({d.tables[table].concept}):\n{ddl}",
                         ref=table))
    return out


def table_chunks(d: BusinessDictionary) -> list[Chunk]:
    out = []
    for t in sorted(d.tables.values(), key=lambda t: t.name):
        lines = [f"Tabla {t.name}: {t.concept}. {t.description}",
                 f"Llave: {', '.join(t.key)}. Sinónimos: {', '.join(t.synonyms)}.", "Columnas:"]
        for c in t.columns.values():
            tags = [*c.defects] + ([f"regla {c.rule}"] if c.rule else [])
            syn = f" (también: {', '.join(c.synonyms)})" if c.synonyms else ""
            lines.append(f"- {c.name}: {c.meaning}{syn}" + (f" [{'; '.join(tags)}]" if tags else ""))
        joins = [j for j in d.joins if j.involves(t.name)]
        if joins:
            lines.append("Joins conocidos (no hay llaves foráneas):")
            lines += [f"- {j.sql}" + (f"  -- {j.note}" if j.note else "") for j in joins]
        out.append(Chunk(f"table:{t.name}", "table", "\n".join(lines), ref=t.name))
    return out


def rule_chunks(d: BusinessDictionary) -> list[Chunk]:
    out = []
    for r in d.rules.values():
        cols = [f"{c.table}.{c.name}" for c in d.columns() if c.rule == r.name]
        text = f"Regla de negocio '{r.name}': {r.text}"
        if r.terms:
            text += f"\nAplica a: {', '.join(r.terms)}."
        if cols:
            text += f"\nColumnas: {', '.join(cols)}."
        out.append(Chunk(f"rule:{r.name}", "rule", text, ref=r.name))
    return out


def catalog_chunks(d: BusinessDictionary) -> list[Chunk]:
    out = []
    for key, values in d.catalogs.items():
        cols = [c for c in d.columns() if c.name == key]
        where = ", ".join(f"{c.table}.{c.name} ({c.meaning})" for c in cols)
        codes = "; ".join(f"'{k}' = {v}" for k, v in values.items())
        out.append(Chunk(f"catalog:{key}", "catalog", f"Catálogo de códigos de {where}: {codes}.", ref=key))
    return out


def defect_chunks(defects_md: str) -> list[Chunk]:
    out = []
    for m in _DEFECT_ROW.finditer(defects_md):
        did, *cells = (x.strip() for x in m.groups())
        if did == "ID":
            continue
        defect, risk, handling = cells
        out.append(Chunk(f"defect:{did}", "defect",
                         f"Defecto {did} del esquema legacy: {defect}.\nRiesgo si se ignora: {risk}.\n"
                         f"Manejo esperado: {handling}.", ref=did))
    return out


def build_chunks(d: BusinessDictionary | None = None, schema_sql: str | None = None,
                 defects_md: str | None = None) -> list[Chunk]:
    d = d or load_dictionary()
    schema_sql = schema_sql if schema_sql is not None else SCHEMA_SQL.read_text(encoding="utf-8")
    defects_md = defects_md if defects_md is not None else DEFECTS_MD.read_text(encoding="utf-8")
    chunks = (ddl_chunks(d, schema_sql) + table_chunks(d) + rule_chunks(d) + catalog_chunks(d)
              + defect_chunks(defects_md))
    return [Chunk(c.source, c.kind, redact(c.content), c.ref, c.index, c.metadata) for c in chunks]
