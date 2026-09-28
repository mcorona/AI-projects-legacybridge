"""Golden set: `evals/questions/{dev,test}.jsonl`.

- dev: se usa para desarrollar y ajustar prompts, reglas y guardrails. Sus métricas son optimistas.
- test: NUNCA se usa para ajustar; es la medida que se reporta.
- holdout: 15 ataques adversariales escritos ANTES de construir los guardrails de la Fase 4
  (ADR-006). No se consultan al desarrollar; miden la generalización de las defensas.

Campos: id, level (easy | medium | defect | adversarial), question, defects [D1..D10].
Contestables: gold_sql, order_matters (opcional), naive_sql (obligatorio en level=defect: la
respuesta ingenua que ignora el defecto; debe dar un resultado DISTINTO a gold_sql).
Adversariales: expect y, si aplica, attack, attack_sql y guard_reason.
"""
from __future__ import annotations

import json
from pathlib import Path

QUESTIONS_DIR = Path(__file__).parent / "questions"
SPLITS = ("dev", "test")          # el golden set de 120 preguntas
EXTRA_SPLITS = ("holdout",)
LEVELS = ("easy", "medium", "defect", "adversarial")
TARGETS = {  # composición objetivo del PLAN (Fase 3): 120 preguntas
    "dev": {"easy": 10, "medium": 10, "defect": 5, "adversarial": 5},
    "test": {"easy": 30, "medium": 30, "defect": 20, "adversarial": 10},
}


def load(split: str = "dev") -> list[dict]:
    """Preguntas de un split ('dev', 'test', 'holdout'); 'all' = dev + test (las 120 del PLAN);
    'adversarial' = las 15 adversariales del golden set (criterio de aceptación de la Fase 4)."""
    if split == "all":
        return [q for s in SPLITS for q in load(s)]
    if split == "adversarial":
        return [q for q in load("all") if q["level"] == "adversarial"]
    if split not in SPLITS + EXTRA_SPLITS:
        raise ValueError(f"split inválido: {split}; usa {SPLITS + EXTRA_SPLITS} o 'all'")
    path = QUESTIONS_DIR / f"{split}.jsonl"
    return [{**json.loads(line), "split": split}
            for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def by_id() -> dict[str, dict]:
    """Todas las preguntas conocidas (dev, test y holdout) por id: para reportes y re-puntuación."""
    return {q["id"]: q for q in load("all") + load("holdout")}
