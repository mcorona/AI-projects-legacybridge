# Plan de implementación — LegacyBridge (6 semanas)

Prerrequisito: `inventory-copilot` terminado (reutilizamos su capa LLM y el SQL guard).

## Fase 0 — Arranque (día 1)
- [x] `make setup && make db && make test` en verde.
- [x] Smoke test de los tres proveedores: `python -m scripts.smoke_llm --provider local|omniroute|bedrock`.
- **Aceptación:** los 3 responden; Qwen sin bloques `<think>` en la salida.

## Fase 1 — MCP servers (semana 1)
- [ ] `schema_explorer`: `list_tables`, `describe_table`, `find_columns(concept)`,
      `get_business_rule(term)` leyendo `config/business_dictionary.yaml`.
- [ ] `sql_readonly`: `run_query(sql)` → valida con `sql_guard` → ejecuta con `lb_ro`
      con `statement_timeout=5s` → devuelve filas + SQL normalizada.
- [ ] Registrar ambos en `.mcp.json` y probar desde Claude Code y MCP Inspector.
- **Aceptación:** 100% de pruebas de `tests/test_sql_guard.py`; herramientas visibles en Inspector.

## Fase 2 — RAG + agente (semana 2)
- [ ] Indexar DDL, diccionario de negocio y `docs/LEGACY_DEFECTS.md` en pgvector (bge-m3, 1024).
- [ ] Orquestador con tool use: plan → explorar esquema → SQL → validar → ejecutar → responder
      con evidencia. Máximo 2 reintentos de autocorrección con el error del guard/DB.
- **Aceptación:** responde 10 preguntas de `evals/questions/golden_v1.jsonl` con evidencia.

## Fase 3 — Evaluación (semana 3)
- [ ] Harness `evals/run.py`: execution accuracy (comparar result sets, no texto SQL),
      SWAR, tasa de rechazo correcto, latencia p50/p95, tokens y costo estimado.
- [ ] Golden set a 120 preguntas: 40 fáciles, 40 con joins/reglas, 25 que tocan defectos,
      15 adversariales (inyección, DML disfrazado, exfiltración de catálogo).
- **Aceptación:** reporte markdown reproducible en `evals/reports/`.

## Fase 4 — Seguridad y guardrails (semana 4)
- [ ] Prompt injection directa e indirecta (texto malicioso dentro de datos de la BD).
- [ ] Enmascarado de PII en salida; Bedrock Guardrails `ApplyGuardrail` como capa opcional.
- [ ] Human-in-the-loop para cualquier acción marcada como `write` (solo propuesta, no ejecución).
- **Aceptación:** 15/15 adversariales bloqueadas o respondidas de forma segura.

## Fase 5 — Cascada de costo y observabilidad (semana 5)
- [ ] Router: Qwen local por defecto; escala a Bedrock Haiku si baja confianza o falla de guard x2.
- [ ] Tabla comparativa: local vs OmniRoute vs Bedrock vs cascada (accuracy, costo/consulta, p95).
- **Aceptación:** cascada ≥ 95% de la accuracy de Bedrock-only con ≤ 20% de su costo (meta).

## Fase 6 — Publicación (semana 6)
- [ ] README en inglés con diagrama, resultados y "failure modes".
- [ ] Video demo de 90 s; post destacado y artículo en LinkedIn; caso de estudio para Upwork.
