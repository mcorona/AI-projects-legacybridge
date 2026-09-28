# ADR-002: Respuesta vacía o truncada = falla del proveedor

- **Estado:** Aceptado
- **Fecha:** 2026-09-27
- **Relacionado:** ADR-001 (cascada de costo), `src/legacybridge/llm/router.py`

## Contexto
En el smoke test de la Fase 0, el proveedor `local` (Qwen3.6-35B-A3B en LM Studio) reportó
`OK` con una respuesta vacía. Diagnóstico con `max_tokens=64`:

| max_tokens | finish_reason | completion_tokens | `content` | `reasoning_content` |
|-----------:|---------------|------------------:|-----------|---------------------|
| 64         | `length`      | 63                | `''`      | "Here's a thinking process…" |
| 1024       | `stop`        | 209               | `'SELECT 1'` | (razonamiento completo) |

Qwen3.x razona antes de responder. LM Studio separa ese razonamiento en `reasoning_content`
(otros servidores lo dejan inline como `<think>…`). Si el presupuesto se agota razonando,
`content` llega vacío. El router lo trataba como éxito, lo que tenía tres consecuencias:

1. La cascada **nunca escalaba**: devolvía `""` al agente como respuesta válida.
2. El smoke test y, a futuro, el harness de evaluación, contaban como éxito una falla.
3. Los tokens consumidos en el intento no se reflejaban en el costo cuando otro proveedor
   terminaba respondiendo.

## Decisión
1. Los backends devuelven el motivo de paro (`finish_reason` en OpenAI-compat,
   `stopReason` en Bedrock Converse) y `LLMResult` lo expone en `stop_reason`.
2. Si el texto queda vacío tras `strip_think()`, el intento es una **falla del proveedor**
   (`EmptyCompletionError`): la cascada pasa al siguiente. Con un solo proveedor se lanza
   `RuntimeError` con el historial de intentos y la causa encadenada.
3. Un texto **no vacío pero truncado** (`length` / `max_tokens`) **no** escala: se devuelve con
   `LLMResult.truncated = True`. El agente decide (p. ej. el SQL guard rechazará una SQL
   incompleta y eso dispara la autocorrección o el escalamiento por `guard_failures`).
4. Tokens, costo y latencia de `LLMResult` **acumulan todos los intentos** de la cascada:
   reflejan el costo real de la consulta.
5. Bedrock usa reintentos adaptativos de botocore (5 intentos) para que un
   `ThrottlingException` transitorio no cuente como falla del proveedor.
6. El smoke test usa `max_tokens=1024` por defecto y falla ante texto vacío o `<think>`.

## Alternativas consideradas
- **Leer `reasoning_content` como respuesta:** mezcla razonamiento con respuesta, y ese campo
  no es estándar entre servidores. Rechazada.
- **Desactivar el razonamiento de Qwen (`/no_think`):** baja la calidad de la SQL, que es
  justo lo que el proyecto mide. Podrá evaluarse como variante en la Fase 5, no como default.
- **Reintentar el mismo proveedor con más `max_tokens`:** duplica la latencia local y oculta un
  presupuesto mal configurado. Se prefirió escalar y dejar la señal visible en `attempts`.
- **Escalar también ante texto truncado no vacío:** descartaría respuestas útiles; la decisión
  corresponde al agente, que tiene el contexto (guard, confianza).

## Consecuencias
- (+) La cascada es honesta: `local: empty(length)` queda registrado y se escala.
- (+) Las métricas de costo de la Fase 5 incluyen los intentos fallidos.
- (+) Las fallas silenciosas se vuelven visibles en el smoke test y en las evaluaciones.
- (−) Presupuestos de `max_tokens` chicos provocan escalamientos a proveedores de pago;
  hay que dimensionarlos para modelos de razonamiento (≥ 1024 en llamadas del agente).
- (−) Todos los backends deben devolver el motivo de paro (una tupla de 5 elementos); un nuevo
  backend debe respetar ese contrato (cubierto por `tests/test_router.py`).
