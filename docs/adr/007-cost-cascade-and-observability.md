# ADR-007: Cascada de costo a nivel de respuesta y observabilidad

- **Estado:** Aceptado · Fase 5 cerrada como **parcial** (evaluación con Bedrock bloqueada por AWS)
- **Fecha:** 2026-09-29
- **Relacionado:** ADR-001 (cascada de costo), ADR-002 (respuesta vacía), ADR-004 (evidencia),
  ADR-005 (evaluación), `src/legacybridge/agent/cascade.py`, `src/legacybridge/telemetry.py`

## Contexto
El PLAN pide que Qwen local sea el nivel por defecto y que se escale a Bedrock Haiku "si baja
confianza o falla de guard ×2", con una tabla comparativa y la meta: cascada ≥ 95 % de la accuracy de
Bedrock-only con ≤ 20 % de su costo. Hallazgos al iniciar:

1. La cascada existente (ADR-002) era por **llamada**: escala una llamada vacía o fallida, pero no
   evalúa si la **respuesta** es aceptable.
2. El precio de Haiku 4.5 en la configuración (`$1/$5`) no estaba verificado; el catálogo público de
   AWS (publicado 2026-09-28) da **$1.10/$5.50 Regional** para el perfil geográfico `us.`
   (Global: $1.00/$5.00). Los costos equivalentes de las Fases 3-4 quedaron ~10 % bajos.
3. La confianza que reporta Qwen es casi siempre 1.0: no sirve sola como señal de escalamiento.
4. La mayor pérdida del modelo local eran corridas **sin terminar** (razonamiento desbocado y typos
   repetidos en nombres crípticos), no respuestas incorrectas.

## Decisión

### Cascada por respuesta (`CascadeAgent`)
- Niveles en `config/models.yaml` (`cascade.agent_tiers: [local, bedrock]`). Cada nivel es un
  `Agent` con proveedor fijo: una conversación nunca mezcla proveedores.
- Si el resultado del nivel no pasa la política `cascade.escalate_when`, la pregunta se repite
  **desde cero** en el siguiente nivel. Motivos: error del proveedor, corrida sin terminar, SQL fallida
  ≥ 2 veces, respuesta sin evidencia, confianza calibrada < 0.6, `cannot_answer`.
- No se escalan decisiones deterministas o seguras (rechazos, propuestas pendientes, entradas
  bloqueadas): escalarlas solo costaría dinero.
- Tokens, costo, latencia y traza de llamadas se **acumulan** entre niveles (costo real) y cada
  escalamiento registra de dónde, a dónde y por qué.

### Reducir escalamientos en el origen
- **"¿Quisiste decir…?"** en los errores de `run_query` (columna/tabla inexistente, columna ambigua,
  `USING` inválido), calculado con el diccionario sobre las tablas de la consulta.
- **Evidencia señalada**: cada consulta devuelve un `query_id` y `submit_answer` puede indicar cuál
  sustenta la respuesta (`evidence_query`). Corrige un sesgo de medición: los modelos que verifican
  después de responder (Bedrock en dev) quedaban evaluados contra la consulta de verificación.

### Observabilidad
- Telemetría por turno (JSONL, `LB_TELEMETRY_PATH`): latencia por etapa (LLM, herramientas,
  guardrails), llamadas al LLM, tokens, costo real y equivalente, escalamientos y hallazgos de
  guardrails. Sin texto de preguntas ni respuestas (solo un hash). Campos compatibles con CloudWatch
  EMF y spans de AgentCore/OpenTelemetry. `make telemetry` la resume.
- Primer dato: ~99.6 % de la latencia de un turno es el LLM (herramientas ~0.1 s, guardrails ~1 ms).

### Precios y comparabilidad
- Precios con fuente y fecha en `config/models.yaml`; forman parte de la huella de cada reporte.
- `evals/compare_runs.py` combina corridas hechas en momentos distintos y solo las declara
  comparables si coinciden golden set, datos, prompt, herramientas, diccionario y guardrails.
- Techo de calidad opcional: Opus 4.5 (Opus 5.5 no tiene acuerdo disponible para la cuenta), con
  precio verificado en el catálogo de AWS el 2026-10-01.

## Alternativas consideradas
- **Solo la cascada por llamada**: no detecta corridas sin terminar ni respuestas sin evidencia.
- **Continuar la conversación con el nivel caro** en lugar de reiniciarla: arrastra el contexto y los
  errores del nivel barato y mezcla proveedores en una traza.
- **Usar solo la confianza del modelo**: Qwen reporta ~1.0 casi siempre.
- **Un verificador LLM de la respuesta** como señal adicional: detectaría errores silenciosos, pero
  agrega una llamada por pregunta; se deja como trabajo futuro, medido contra el SWAR.

## Consecuencias
- (+) Las corridas sin terminar del nivel local se rescatan con el nivel caro.
- (+) Costo y latencia reales por consulta, con motivo de cada escalamiento.
- (−) La cascada **no** detecta respuestas incorrectas entregadas con confianza (SWAR): su accuracy
  está acotada por la del nivel local en las preguntas que no escala.
- (−) La latencia de las preguntas escaladas suma ambos niveles.

## Resultados (2026-09-29, split test)

Comparativa: `evals/reports/2026-09-29-phase5-comparison.md` (misma huella en ambas corridas).

| Métrica | Qwen local (×3) | OmniRoute `auto/best-free` (×1) |
|---|---|---|
| Execution accuracy | **95.4 %** [95.0–96.2] | 88.8 % |
| SWAR | **3.8 %** [2.5–5.0] | 11.2 % |
| Corridas terminadas | 97.0 % | 100 % |
| Rechazo correcto / indebido | 76.7 % / 0 % | 70.0 % / 0 % |
| Latencia p50 / p95 | 19 s / 43 s | 17 s / 28 s |
| Costo real | $0 | $0 |
| Costo equivalente en Haiku 4.5 (precio verificado) | ~$0.026 por consulta | — |

Por nivel (local): easy 100 %, medium 96.7 %, defect 86.7 %. Frente a la Fase 3 (77.9 %, SWAR 5.4 %,
84.1 % terminadas), la mejora coincide con las sugerencias de nombres, la evidencia señalada y los
cambios de la Fase 4; no se aisló el efecto de cada cambio.

Implicación para la cascada: con 97 % de corridas terminadas, el nivel local deja poco que escalar. La
cascada costaría muy por debajo del 20 % de Bedrock-only; el reto de la meta es la accuracy, porque la
cascada no corrige el 3.8 % de respuestas incorrectas silenciosas.

### Bedrock y verificación de la meta (2026-10-01, split test)

AWS levantó la restricción de cuenta (`Error 002`, 2026-09-28 a 2026-10-01). Comparativa final con las cinco
corridas: `evals/reports/2026-10-01-phase5-comparison.md` (misma huella de golden set, datos, prompt,
herramientas, diccionario y guardrails).

| Métrica | Qwen local (×3) | OmniRoute (×1) | Bedrock Haiku 4.5 (×3) | **Cascada (×3)** | Techo Opus 4.5 (×1) |
|---|---|---|---|---|---|
| Execution accuracy | 95.4 % | 88.8 % | 93.3 % | **95.4 %** | 93.8 % |
| SWAR | 3.8 % | 11.2 % | 6.7 % | **4.6 %** | 6.2 % |
| Corridas terminadas | 97.0 % | 100 % | 100 % | **100 %** | 100 % |
| Rechazo correcto (adversariales) | 76.7 % | 70.0 % | 80.0 % | 80.0 % | 90.0 % |
| Escaladas | — | — | — | 6.3 % | — |
| Costo real por corrida (90 preguntas) | $0 | $0 | $2.53 | **$0.17** | $11.83 |
| Costo por consulta | $0 | $0 | $0.0281 | **$0.0018** | $0.1315 |
| Latencia p50 / p95 | 19 s / 43 s | 17 s / 28 s | 9 s / 16 s | 17 s / 43 s | 15 s / 26 s |

**Meta del PLAN cumplida:** la cascada logra **102.2 %** de la accuracy de Bedrock-only (meta ≥ 95 %)
con **6.4 %** de su costo (meta ≤ 20 %).

- La cascada termina el 100 % de las corridas: rescata el 3 % que Qwen dejaba sin terminar y escala
  solo 6.3 % de las preguntas. Por nivel: easy 100 %, medium 94.4 %, defect 90.0 %.
- Más modelo no es mejor respuesta en este dominio: Haiku y Opus quedan por debajo de Qwen con las
  mismas herramientas. Lo que más mueve la accuracy es el contexto (reglas de negocio, sugerencias de
  nombres, evidencia), no el tamaño del modelo. Bedrock-only es, en cambio, el más rápido (p50 9 s).
- Dos preguntas fallan en los cuatro proveedores: d019 (la referencia aplica la regla de pedido válido
  que la pregunta no menciona) y d018 (la referencia excluye existencias de artículos huérfanos).
  Quedan para revisión del golden set; no se cambiaron para no invalidar la comparación.
- El techo usa **Opus 4.5**, no Opus 5.5: el acuerdo de uso de Opus 5.5 aparece como NOT_AVAILABLE para
  la cuenta. Precio de Opus 4.5 verificado en el catálogo de AWS (Regional $5.50 / $27.50).
- Bedrock Guardrails en vivo: ADR-006, *Prueba en vivo*.
- Gasto total en Bedrock de la Fase 5: ≈ $20 en la evaluación final (Bedrock ×3 $7.6, cascada ×3 $0.5, Opus ×1 $11.8) (más ≈ $1.6 previos), cubierto
  por créditos; alarma de presupuesto `legacybridge-evals-40usd`.
