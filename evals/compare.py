"""Comparación de result sets (execution accuracy): se compara lo que la SQL DEVUELVE, no su texto.

Unifica `scripts/agent_check.result_sets_match` (Fase 2) con `results_match` de inventory-copilot:
- tolerante (métrica principal): cada columna de la referencia se mapea a una columna distinta
  de la predicción; se permiten columnas extra y cualquier orden de columnas.
- estricto: además, el mismo número de columnas.
- filas como multiconjunto salvo `order_matters`.
- números redondeados a FLOAT_DIGITS (el agente suele aplicar ROUND(x, 2) a importes y promedios).
- representaciones equivalentes (ADR-005, corrección posterior a la primera corrida oficial):
  un texto numérico vale como número ('2024' == 2024) y, con `aliases`, la etiqueta de negocio de
  un catálogo del diccionario vale como su código ('MXN' == 'P', 'Guadalajara' == '01').
  El alias se resuelve POR COLUMNA: solo aplica el catálogo cuyos códigos contienen los valores de
  esa columna de la referencia ("Querétaro" es '03' como almacén y 'QRO' como estado).
"""
from __future__ import annotations

import datetime as dt
import re
from collections import Counter
from decimal import Decimal

FLOAT_DIGITS = 2
_NUMERIC = re.compile(r"^[+-]?\d+(\.\d+)?$")


Aliases = dict[str, dict[str, str]]    # {catálogo: {etiqueta normalizada: código}}


def catalog_aliases() -> Aliases:
    """Etiquetas de negocio de cada catálogo del diccionario, por catálogo."""
    from legacybridge.dictionary import load, normalize

    out: Aliases = {}
    for name, values in load().catalogs.items():
        labels: dict[str, str] = {}
        for code, label in values.items():
            labels[normalize(label)] = code
            short = re.sub(r"\s*\(.*\)\s*$", "", label)       # 'Migrado 1998 (no usar)' -> 'Migrado 1998'
            labels.setdefault(normalize(short), code)
        out[name] = labels
    return out


def _column_aliases(gold_values: list, aliases: Aliases | None) -> dict[str, str] | None:
    """Etiquetas aplicables a una columna: las de los catálogos que contienen todos sus valores."""
    if not aliases:
        return None
    vals = {str(v).strip() for v in gold_values if v is not None}
    if not vals:
        return None
    merged: dict[str, str] = {}
    for labels in aliases.values():
        if vals <= set(labels.values()):
            merged.update(labels)
    return merged or None


def normalize_value(v, aliases: dict[str, str] | None = None):
    if isinstance(v, bool) or v is None:
        return v
    if isinstance(v, (int, float, Decimal)):
        return round(float(v), FLOAT_DIGITS)
    if isinstance(v, (dt.date, dt.datetime)):
        return v.isoformat()
    text = str(v).strip()
    if aliases:
        from legacybridge.dictionary import normalize
        text = aliases.get(normalize(text), text)
    return round(float(text), FLOAT_DIGITS) if _NUMERIC.match(text) else text


def _sort_key(x):
    return (x is None, type(x).__name__, str(x))


def results_match(gold_cols: list[str], gold_rows: list[list], cols: list[str], rows: list[list],
                  order_matters: bool = False, allow_extra_columns: bool = True,
                  aliases: Aliases | None = None) -> bool:
    if len(gold_rows) != len(rows):
        return False
    if not allow_extra_columns and len(cols) != len(gold_cols):
        return False
    if not gold_rows:
        return len(cols) >= len(gold_cols)
    col_alias = [_column_aliases([r[i] for r in gold_rows], aliases) for i in range(len(gold_cols))]
    g = [[normalize_value(r[i]) for r in gold_rows] for i in range(len(gold_cols))]
    # la columna j del agente se interpreta con el catálogo de la columna i de la referencia
    pred = lambda i, j: [normalize_value(r[j], col_alias[i]) for r in rows]  # noqa: E731
    # candidatos por columna: mismo multiconjunto de valores (poda la búsqueda de proyecciones)
    candidates = [[j for j in range(len(cols))
                   if sorted(pred(i, j), key=_sort_key) == sorted(g[i], key=_sort_key)] for i in range(len(g))]
    gold_t = [tuple(normalize_value(v) for v in r) for r in gold_rows]
    target = gold_t if order_matters else Counter(gold_t)

    def project(mapping: tuple[int, ...]):
        proj = [tuple(normalize_value(r[j], col_alias[i]) for i, j in enumerate(mapping)) for r in rows]
        return proj if order_matters else Counter(proj)

    def search(i: int, used: tuple[int, ...]) -> bool:
        if i == len(g):
            return project(used) == target
        return any(search(i + 1, used + (j,)) for j in candidates[i] if j not in used)
    return search(0, ())
