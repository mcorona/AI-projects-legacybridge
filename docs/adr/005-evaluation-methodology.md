# ADR-005: Metodología de evaluación

- **Estado:** Aceptado
- **Fecha:** 2026-09-28
- **Relacionado:** ADR-001 (cascada), ADR-004 (contrato de evidencia), `evals/`, `scripts/gen_data.py`

## Contexto
La Fase 3 pide un harness reproducible con execution accuracy, SWAR, tasa de rechazo correcto,
latencia p50/p95, tokens y costo, y un golden set de 120 preguntas. Al iniciar:

- Los datos semilla tenían 3–4 filas por tabla, así que muchas preguntas no distinguían una
  respuesta correcta de una ingenua (p. ej. un filtro de fecha mal hecho daba el mismo resultado).
- Las 11 preguntas contestables ya se habían usado para ajustar el prompt en la Fase 2: sus
  métricas son optimistas.
- "SWAR" no estaba definido en el repositorio.
- En la Fase 2 el mismo ítem pasó o falló entre corridas con temperature=0.

## Decisión

### Datos sintéticos deterministas (`make seed`, semilla 42)
~200 clientes, ~300 artículos, 3 almacenes, ~2,000 pedidos y ~5,800 partidas, más las filas ancla
de `02_seed.sql`. Cada defecto D1–D10 se siembra con proporciones controladas (ver
`defect_profile`). Hallazgo relevante: `TO_DATE('00000000')` y `TO_DATE('')` **no fallan** en
PostgreSQL, devuelven el año 1 a.C.; un filtro de fechas ingenuo los incluye en silencio.

### Golden set: 120 preguntas con división dev/test
| Nivel | dev | test |
|---|---|---|
| easy | 10 | 30 |
| medium (joins y reglas) | 10 | 30 |
| defect | 5 | 20 |
| adversarial | 5 | 10 |

- **dev** se usa para ajustar prompts, reglas y guardrails; **test nunca**. Se reporta test.
- Las preguntas usan **lenguaje de negocio**: las de defectos no dicen cómo manejar el defecto.
- Cada pregunta de defecto trae `naive_sql`; una prueba demuestra que su resultado difiere del de
  `gold_sql` con los datos generados (la pregunta discrimina).
- Toda referencia devuelve entre 1 y 50 filas (lo que ve el agente); sin "top N" (empates) ni
  agrupaciones por mes (representación ambigua); "estado" siempre como "estado de la república".
- Las adversariales traen `expect` y, si aplica, `attack_sql` + `guard_reason`, verificados contra
  el guard en CI.

### Métricas (`evals/metrics.py`)
- **Execution accuracy**: se comparan RESULT SETS, no texto SQL. Tolerante (principal: columnas
  extra y cualquier orden permitidos) y estricta. Redondeo a 2 decimales. Una corrida que no
  terminó nunca cuenta como correcta, aunque haya evidencia registrada.
- **SWAR (Silent Wrong Answer Rate)**: contestables entregadas como respuesta (outcome=answer,
  terminadas) con resultado incorrecto o sin evidencia **y** confianza calibrada ≥ 0.6, el mismo
  umbral de escalamiento de `config/models.yaml` (prueba que lo amarra). Es el riesgo real en un
  ERP: una cifra equivocada que el sistema no marca como dudosa.
  - Se consideró exigir además "sin advertencias", pero el agente agrega advertencias genéricas
    casi siempre ("se aplicó la regla D4"), lo que llevaría la métrica a ~0 sin significado. Esa
    variante se reporta aparte (`swar_uncaveated`) por transparencia.
- Tasa de rechazo correcto y de rechazo indebido; fugas de nombres sensibles; corridas terminadas.
- Latencia p50/p95, tokens, costo real y **costo equivalente en Bedrock Haiku** (tokens × precio de
  `config/models.yaml`), para comparar corridas locales de $0.
- Desglose por nivel y por defecto; lista de ítems inestables entre repeticiones.

### Reproducibilidad
- Cada reporte incluye una huella: commit (y si había cambios sin commit), sha del golden set
  (dev/test), del prompt, de las specs de herramientas y del diccionario, checksum de los datos y
  del índice RAG, modelos y parámetros del agente.
- Resultados crudos por (pregunta, repetición) en JSONL; `--resume` continúa una corrida cortada.
- Reporte oficial: `make eval` = test × 3 repeticiones con Qwen local ($0). Se versionan los
  reportes oficiales; los de trabajo (`--scratch`) y los crudos no.

## Alternativas consideradas
- **Comparar texto SQL o usar un juez LLM**: la evidencia del agente es ejecutable; comparar
  resultados es objetivo y barato. Inventory-copilot usa un juez para *faithfulness* del texto;
  aquí no hace falta porque la cifra se verifica contra las filas.
- **Un solo split**: métricas infladas por el ajuste de la Fase 2.
- **Bedrock para el reporte de esta fase**: más rápido, pero CLAUDE.md reserva Bedrock para la
  evaluación final y la comparación de la Fase 5.
- **Gate de CI (inventory-copilot)**: el repositorio aún no tiene CI; se pospone a la Fase 6.

## Consecuencias
- (+) Cada cifra del README sale de un reporte con huella que cualquiera puede reproducir.
- (+) Las preguntas de defectos demuestran que discriminan; una mejora en ellas es real.
- (−) Una corrida oficial local tarda ~2 h (270 ejecuciones).
- (−) La comparación tolerante puede aceptar una respuesta con columnas extra irrelevantes; se
  reporta también la estricta.
