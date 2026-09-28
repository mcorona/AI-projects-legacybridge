"""Comparación de result sets (execution accuracy): se compara lo que la SQL DEVUELVE, no su texto.

Unifica `scripts/agent_check.result_sets_match` (Fase 2) con `results_match` de inventory-copilot:
- tolerante (métrica principal): cada columna de la referencia se mapea a una columna distinta
  de la predicción; se permiten columnas extra y cualquier orden de columnas.
- estricto: además, el mismo número de columnas.
- filas como multiconjunto salvo `order_matters`.
- números redondeados a FLOAT_DIGITS (el agente suele aplicar ROUND(x, 2) a importes y promedios).
"""
from __future__ import annotations

import datetime as dt
from collections import Counter
from decimal import Decimal

FLOAT_DIGITS = 2


def normalize_value(v):
    if isinstance(v, bool) or v is None:
        return v
    if isinstance(v, (int, float, Decimal)):
        return round(float(v), FLOAT_DIGITS)
    if isinstance(v, (dt.date, dt.datetime)):
        return v.isoformat()
    return str(v).strip()


def _sort_key(x):
    return (x is None, type(x).__name__, str(x))


def results_match(gold_cols: list[str], gold_rows: list[list], cols: list[str], rows: list[list],
                  order_matters: bool = False, allow_extra_columns: bool = True) -> bool:
    if len(gold_rows) != len(rows):
        return False
    if not allow_extra_columns and len(cols) != len(gold_cols):
        return False
    if not gold_rows:
        return len(cols) >= len(gold_cols)
    g = [[normalize_value(r[i]) for r in gold_rows] for i in range(len(gold_cols))]
    p = [[normalize_value(r[j]) for r in rows] for j in range(len(cols))]
    # candidatos por columna: misma multiconjunto de valores (poda la búsqueda de proyecciones)
    candidates = [[j for j in range(len(p)) if sorted(p[j], key=_sort_key) == sorted(g[i], key=_sort_key)]
                  for i in range(len(g))]
    gold_t = [tuple(normalize_value(v) for v in r) for r in gold_rows]
    target = gold_t if order_matters else Counter(gold_t)

    def project(mapping: tuple[int, ...]):
        proj = [tuple(normalize_value(r[j]) for j in mapping) for r in rows]
        return proj if order_matters else Counter(proj)

    def search(i: int, used: tuple[int, ...]) -> bool:
        if i == len(g):
            return project(used) == target
        return any(search(i + 1, used + (j,)) for j in candidates[i] if j not in used)
    return search(0, ())
