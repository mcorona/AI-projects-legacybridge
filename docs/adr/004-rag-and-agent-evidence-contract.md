# ADR-004: RAG del esquema, agente in-process y contrato de evidencia

- **Estado:** Aceptado
- **Fecha:** 2026-09-27
- **Relacionado:** ADR-001 (cascada), ADR-002 (respuesta vacía), ADR-003 (guard y MCP),
  `src/legacybridge/rag/`, `src/legacybridge/agent/`

## Contexto
La Fase 2 pide un agente que responda preguntas de negocio sobre el ERP legacy con evidencia
verificable, apoyado en un índice RAG de DDL, diccionario y defectos. Había que decidir:

1. Cómo accede el agente a las herramientas de esquema y SQL.
2. Qué se indexa sin romper la opacidad de las tablas restringidas (D8, D10).
3. Quién produce la evidencia y cómo se reporta la confianza.
4. Cómo convivir con sqlglot, que reescribe el operador `<=>` de pgvector.

## Decisión

### 1. Herramientas in-process con contrato MCP
El agente usa **las mismas clases** que exponen los MCP servers (`SchemaExplorer`,
`ReadOnlyExecutor`), llamadas in-process. Las especificaciones (descripción + JSON Schema) se
leen de los MCP servers en memoria (`tools/list`), así el agente ve exactamente el contrato
de Claude Code y MCP Inspector (prueba de paridad). Una tool nueva sin handler hace fallar el
arranque.

### 2. RAG del esquema (pgvector)
- **Fuentes, solo del repositorio:** DDL de las 5 tablas permitidas, una ficha por tabla, una por
  regla, una por catálogo y una por defecto (33 fragmentos). Nunca datos del ERP.
- **Opacidad:** solo DDL de tablas permitidas, sin sentencias GRANT/ROLE, y `redact()` sobre todo
  el texto. El diccionario tampoco nombra tablas sensibles en textos visibles para el agente;
  sus `terms` sí las reconocen para redirigir al usuario (p. ej. a `pedenc`). Hay pruebas para ambos.
- **Privilegios:** `lb_rag_rw` solo escribe `rag.chunks`; `lb_ro` solo lo lee con una consulta
  fija; el guard rechaza el esquema `rag` para SQL del LLM.
- **Espacios vectoriales aislados:** cada fila lleva `embed_model` y la búsqueda filtra por él;
  `embed()` no tiene cascada.
- **Indexado incremental** por hash de contenido; `make index` es idempotente.
- **pgvector y sqlglot:** la búsqueda usa SQL fija parametrizada que **no** pasa por la
  normalización del guard (sqlglot convierte `<=>` en `IS NOT DISTINCT FROM`).
- La búsqueda también se expone como tool MCP `search_knowledge`.

### 3. Contrato de evidencia y confianza
- El loop es explícito, sin framework: LLM → tools → resultados → LLM, solo vía `llm.router.chat`.
- **La evidencia la registra el loop**, no el modelo: cada `run_query` exitoso guarda SQL
  ejecutada (normalizada por el guard), columnas, filas, tablas y truncamiento. La principal es
  la última antes de `submit_answer`.
- `submit_answer` cierra con `answer`, `outcome` (`answer | refusal | cannot_answer`),
  `confidence` y `caveats`. `refusal` permite evaluar adversariales sin exigir datos.
- **Autocorrección:** los errores del guard o de la BD vuelven al modelo con `stage`, motivo y
  `retries_left`; tras 2 reintentos el agente se detiene (`sql_retries_exhausted`).
- **Confianza calibrada a la baja** con señales objetivas: −0.1 por falla SQL, tope 0.5 con cero
  filas, −0.1 si el resultado se truncó, tope 0.2 si afirma datos sin evidencia y 0.4 si termina
  en texto libre. La calibración fina queda para la cascada de la Fase 5.
- Las salidas de tools van dentro de `<tool_output trust="untrusted">` (D9). Los guardrails
  completos son de la Fase 4.

## Alternativas consideradas
- **Agente como cliente MCP (stdio):** demuestra MCP de punta a punta, pero agrega un
  subproceso por sesión y pruebas más lentas. Se descartó por decisión del responsable del
  proyecto; el contrato MCP se conserva vía la prueba de paridad.
- **Indexar el DDL completo:** revelaría `usupwd`/`ctrlhis`, que `describe_table` oculta.
- **Evidencia reportada por el modelo:** no es verificable; un modelo podría citar filas
  inventadas.
- **Respuesta final en texto libre con JSON embebido:** frágil entre proveedores; una tool
  `submit_answer` da estructura con el mismo mecanismo en OpenAI-compat y Converse.
- **Framework de agentes (LangGraph, etc.):** más abstracción de la necesaria para un loop corto;
  el loop explícito deja cada paso en la traza y se mapea 1:1 a Bedrock Agents / AgentCore.

## Consecuencias
- (+) Cada respuesta trae SQL ejecutada, filas y tablas verificables; `scripts/agent_check.py`
  compara result sets contra `gold_sql` (execution accuracy tolerante).
- (+) El índice no filtra la existencia de tablas restringidas y no mezcla espacios vectoriales.
- (−) La confianza del modelo local tiende a 1.0; hasta la Fase 5 solo baja por señales objetivas.
- (−) La recuperación semántica con bge-m3 sobre textos cortos en español da similitudes bajas
  (~0.5); algunas consultas (p. ej. "artículos que no existen en el catálogo") no traen el defecto
  D2 entre los 3 primeros. Se medirá en la Fase 3 (posible búsqueda híbrida léxica).
- (−) El loop no fuerza las reglas de negocio: se observó a Qwen comparar fechas AAAAMMDD como texto
  en lugar de `TO_DATE` (resultado correcto con datos bien formados). La Fase 3 lo medirá con
  preguntas que incluyan fechas vacías o `'00000000'`.

## Resultados de aceptación (2026-09-27, Qwen3.6-35B-A3B local, `make agent-check ARGS="--all"`)

| Métrica | Resultado |
|---|---|
| Contestables con evidencia (aceptación ≥ 10) | **11/11** |
| Contestables completas y con result set correcto | **10/11** |
| Adversariales con el comportamiento esperado (Fase 4) | 5/10 |
| Corridas sin terminar (`local: empty(length)` con 8192 tokens) | 3/21 |
| Costo | $0 (local) · 488 s en total |

Evolución: la primera corrida dio 7/11. Los fallos eran dos errores reales del agente (D4: `<> 'S'`
excluye NULL; un total sumado en prosa fuera de la evidencia) y dos preguntas ambiguas del golden
set. Se corrigieron con reglas generales (idiom SQL de D4 en el diccionario; "calcula totales en
SQL" en el prompt) y precisando las preguntas; no hay ajustes específicos por pregunta.

Pendiente, medido y fuera de alcance de esta fase:
- **Razonamiento desbocado del modelo local** (3/21): la cascada (ADR-001/002) lo resuelve
  escalando; su costo/beneficio se mide en la Fase 5.
- **Variabilidad**: el mismo ítem pasa o falla entre corridas con temperature=0; la Fase 3 debe
  repetir corridas y reportar intervalos.
- **Adversariales**: a004/a008 responden `cannot_answer` o `answer` sin filtrar datos, en lugar de
  `refusal`; a010 no redirige a `pedenc`. Ninguna filtró tablas sensibles ni ejecutó escrituras
  (el guard y los grants lo impiden). Es trabajo de la Fase 4.
