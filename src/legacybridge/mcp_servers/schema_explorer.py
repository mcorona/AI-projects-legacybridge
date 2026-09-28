"""MCP server: exploración del esquema legacy y reglas de negocio (solo metadatos).

Tools: list_tables, describe_table, find_columns, get_business_rule, search_knowledge.
Nunca ejecuta SQL generada por el LLM: solo consultas fijas (information_schema y el índice
RAG `rag.chunks`) con el rol `lb_ro`, que únicamente ve las tablas de la allowlist.

Arranque por stdio: `python -m legacybridge.mcp_servers.schema_explorer`.
"""
from __future__ import annotations

import os
from collections.abc import Callable
from typing import Annotated, Literal

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field

from legacybridge.dictionary import BusinessDictionary, Column, normalize
from legacybridge.dictionary import load as load_dictionary

INSTRUCTIONS = (
    "Metadatos del ERP legacy (nombres crípticos, sin llaves foráneas, fechas como texto, "
    "estatus mágicos). Flujo sugerido: list_tables para orientarte, find_columns para "
    "traducir un concepto de negocio a columnas, describe_table antes de escribir SQL y "
    "get_business_rule para convenciones (cliente activo, pedido válido, moneda, fechas). "
    "search_knowledge busca por significado en DDL, fichas de tablas, reglas, catálogos y "
    "defectos conocidos cuando no sabes qué término usar."
)
READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False,
                            idempotent_hint=True, open_world_hint=False)
STOPWORDS = frozenset("de del la el los las y o en por para a al con un una que se su sus".split())
_COLUMNS_SQL = (
    "SELECT column_name, data_type, character_maximum_length, numeric_precision, "
    "numeric_scale, is_nullable = 'YES' FROM information_schema.columns "
    "WHERE table_schema = 'public' AND table_name = %s ORDER BY ordinal_position"
)

DbColumns = Callable[[str], list[dict]]
SearchFn = Callable[..., list[dict]]


def _default_search(query: str, k: int, kinds: list[str] | None) -> list[dict]:
    from legacybridge.rag.store import search
    return search(query, k=k, kinds=kinds)


def fetch_db_columns(table: str) -> list[dict]:
    """Columnas reales de la BD (con `lb_ro`). Lanza excepción si la BD no responde."""
    import psycopg

    with psycopg.connect(os.environ.get("LB_DSN", "postgresql://lb_ro:lb_ro@localhost:5433/legacy"),
                         connect_timeout=3) as conn:
        rows = conn.execute(_COLUMNS_SQL, (table,)).fetchall()
    return [{"name": n, "type": _type_label(t, ln, p, s), "nullable": nb}
            for n, t, ln, p, s, nb in rows]


def _type_label(data_type: str, length, precision, scale) -> str:
    if length:
        return f"{data_type}({length})"
    if data_type == "numeric" and precision is not None:
        return f"numeric({precision},{scale})"
    return data_type


def _tokens(text: str) -> list[str]:
    return [t for t in normalize(text).replace("_", " ").split() if t not in STOPWORDS]


def _coverage(query_tokens: list[str], text: str) -> float:
    """Fracción de tokens de la consulta presentes en `text` (coincidencia por prefijo)."""
    if not query_tokens:
        return 0.0
    words = _tokens(text)
    # prefijo simple para plurales/derivados: "pedidos" ~ "pedido", "existencias" ~ "existencia"
    hit = sum(any(w.startswith(q[:max(4, len(q) - 2)]) or q.startswith(w) and len(w) >= 4
                  for w in words) for q in query_tokens)
    return hit / len(query_tokens)


class SchemaExplorer:
    """Lógica de las tools, independiente del transporte MCP (probada sin BD)."""

    def __init__(self, dictionary: BusinessDictionary | None = None,
                 db_columns: DbColumns | None = fetch_db_columns,
                 search_fn: SearchFn | None = _default_search):
        self.d = dictionary or load_dictionary()
        self.db_columns = db_columns
        self.search_fn = search_fn

    # ------------------------------------------------------------------ list_tables
    def list_tables(self) -> dict:
        return {"tables": [{"table": t.name, "concept": t.concept, "description": t.description,
                            "key": list(t.key), "synonyms": list(t.synonyms)}
                           for t in sorted(self.d.tables.values(), key=lambda t: t.name)],
                "note": "Solo estas tablas son consultables. No hay llaves foráneas (D2): "
                        "usa los joins de describe_table."}

    # ------------------------------------------------------------------ describe_table
    def describe_table(self, table: str) -> dict:
        name = normalize(table).split(".")[-1]
        t = self.d.tables.get(name)
        if t is None:
            # misma respuesta para tablas inexistentes y prohibidas: no confirma su existencia
            return {"error": "table_not_allowed", "table": table,
                    "allowed_tables": sorted(self.d.allowed_tables)}
        db_cols, db_error = {}, None
        if self.db_columns is not None:
            try:
                db_cols = {c["name"]: c for c in self.db_columns(name)}
            except Exception as e:  # noqa: BLE001 — degradar a solo diccionario
                db_error = f"{type(e).__name__}: {e}".splitlines()[0][:200]
        columns = []
        order = list(db_cols) + [c for c in t.columns if c not in db_cols]
        for cname in order:
            col, db = t.columns.get(cname), db_cols.get(cname, {})
            columns.append({"name": cname, "type": db.get("type"), "nullable": db.get("nullable"),
                            "meaning": col.meaning if col else None,
                            "defects": list(col.defects) if col else [],
                            "rule": col.rule if col else None,
                            "catalog": self.d.catalogs.get(cname)})
        used_rules = sorted({c.rule for c in t.columns.values() if c.rule})
        used_defects = sorted({d for c in t.columns.values() for d in c.defects},
                              key=lambda x: int(x[1:]))
        out = {"table": t.name, "concept": t.concept, "description": t.description,
               "key": list(t.key), "columns": columns,
               "joins": [{"on": j.sql, "note": j.note} for j in self.d.joins if j.involves(t.name)],
               "rules": {r: self.d.rules[r].text for r in used_rules},
               "defect_warnings": {d: self.d.defects[d] for d in used_defects}}
        if db_error:
            out["db_error"] = db_error
        return out

    # ------------------------------------------------------------------ find_columns
    def find_columns(self, concept: str, limit: int = 8) -> dict:
        q = normalize(concept)
        qt = _tokens(concept)
        scored: list[tuple[float, str, Column]] = []
        for t in self.d.tables.values():
            table_ctx = " ".join((t.name, t.concept, *t.synonyms))
            for col in t.columns.values():
                score, why = self._score(q, qt, col)
                if score:
                    score += 5 * _coverage(qt, table_ctx)   # desempate por tabla relevante
                    scored.append((round(score, 1), why, col))
        scored.sort(key=lambda x: (-x[0], x[2].table, x[2].name))
        matches = [{"table": c.table, "column": c.name, "meaning": c.meaning,
                    "defects": list(c.defects), "rule": c.rule, "score": s, "matched_on": why}
                   for s, why, c in scored[:max(1, min(limit, 25))]]
        out = {"concept": concept, "matches": matches}
        if not matches:
            out["hint"] = "Sin coincidencias. Prueba un sinónimo o revisa list_tables."
        return out

    @staticmethod
    def _score(q: str, qt: list[str], col: Column) -> tuple[float, str]:
        """(puntaje, motivo): nombre exacto > sinónimo exacto > cobertura de sinónimos > significado."""
        syns = [normalize(s) for s in col.synonyms]
        if q == col.name:
            return 100.0, "column_name"
        if q in syns:
            return 90.0, "synonym"
        best = (0.0, "")
        for label, text, weight in (("synonym", " | ".join(syns), 70), ("meaning", col.meaning, 50)):
            cov = _coverage(qt, text)
            if cov >= 0.5 and cov * weight > best[0]:
                best = (cov * weight, label)
        return best

    # ------------------------------------------------------------------ get_business_rule
    def get_business_rule(self, term: str) -> dict:
        qt = _tokens(term)
        rules = {r.name: r.text for r in self.d.rules.values()
                 if qt and _coverage(qt, " ".join((r.name, *r.terms, r.text))) == 1.0}
        catalogs = {}
        for key, values in self.d.catalogs.items():
            cols = [c for c in self.d.columns() if c.name == key]
            ctx = " ".join([key, *values.values(), *(s for c in cols for s in (c.meaning, *c.synonyms))])
            if qt and _coverage(qt, ctx) == 1.0:
                catalogs[key] = values
        if rules or catalogs:
            return {"term": term, "matched": True, "rules": rules, "catalogs": catalogs}
        return {"term": term, "matched": False,
                "available_rules": sorted(self.d.rules), "available_catalogs": sorted(self.d.catalogs),
                "hint": "Sin coincidencias; consulta una de las reglas o catálogos disponibles."}


    # ------------------------------------------------------------------ search_knowledge
    def search_knowledge(self, query: str, k: int = 5, kinds: list[str] | None = None) -> dict:
        if self.search_fn is None:
            return {"query": query, "error": "search_unavailable", "results": []}
        try:
            hits = self.search_fn(query, k, kinds)
        except ValueError as e:          # argumentos inválidos (p. ej. kinds desconocido)
            return {"query": query, "error": "invalid_arguments", "message": str(e), "results": []}
        except Exception as e:  # noqa: BLE001 — BD o servidor de embeddings caído
            return {"query": query, "error": "search_unavailable",
                    "message": f"{type(e).__name__}: {e}".splitlines()[0][:200], "results": []}
        return {"query": query, "results": hits}


def build_server(explorer: SchemaExplorer | None = None) -> MCPServer:
    ex = explorer or SchemaExplorer()
    server = MCPServer(name="legacybridge-schema", instructions=INSTRUCTIONS)

    @server.tool(annotations=READ_ONLY)
    def list_tables() -> dict:
        """Lista las tablas consultables con su concepto de negocio, llave y sinónimos."""
        return ex.list_tables()

    @server.tool(annotations=READ_ONLY)
    def describe_table(
        table: Annotated[str, Field(description="Nombre de la tabla, p. ej. 'pedenc'")],
    ) -> dict:
        """Columnas (tipo, significado, defectos D1-D10), joins conocidos, reglas y catálogos
        de una tabla permitida. Úsala antes de escribir SQL sobre esa tabla."""
        return ex.describe_table(table)

    @server.tool(annotations=READ_ONLY)
    def find_columns(
        concept: Annotated[str, Field(description="Concepto de negocio, p. ej. 'fecha del pedido', 'moneda', 'piezas por caja'")],
        limit: Annotated[int, Field(ge=1, le=25, description="Máximo de resultados")] = 8,
    ) -> dict:
        """Traduce un concepto de negocio a columnas candidatas del esquema legacy,
        ordenadas por relevancia, con sus defectos y reglas asociadas."""
        return ex.find_columns(concept, limit)

    @server.tool(annotations=READ_ONLY)
    def get_business_rule(
        term: Annotated[str, Field(description="Término, p. ej. 'cliente activo', 'pedido válido', 'moneda', 'estatus'")],
    ) -> dict:
        """Reglas y catálogos del negocio que aplican a un término. Si no hay coincidencias,
        devuelve matched=false y la lista de reglas disponibles."""
        return ex.get_business_rule(term)

    @server.tool(annotations=READ_ONLY)
    def search_knowledge(
        query: Annotated[str, Field(description="Qué buscar, en lenguaje natural")],
        k: Annotated[int, Field(ge=1, le=10, description="Número de fragmentos")] = 5,
        kinds: Annotated[list[Literal["ddl", "table", "rule", "catalog", "defect", "doc"]] | None,
                         Field(description="Filtrar por tipo de fragmento (opcional)")] = None,
    ) -> dict:
        """Búsqueda semántica en el conocimiento del esquema (DDL, fichas de tablas, reglas,
        catálogos y defectos D1-D10). Devuelve fragmentos con fuente y similitud."""
        return ex.search_knowledge(query, k, kinds)

    return server


if __name__ == "__main__":
    build_server().run("stdio")
