# Comparativa de proveedores — Fase 5

Split `test`. Valores: media de las repeticiones [mín–máx].

| Métrica | local (×3) | omniroute (×1) |
|---|---|---|
| Execution accuracy | 95.4% [95.0%–96.2%] | 88.8% |
| SWAR | 3.8% [2.5%–5.0%] | 11.2% |
| Corridas terminadas | 97.0% [96.7%–97.8%] | 100.0% |
| Rechazo correcto (adversariales) | 76.7% [70.0%–80.0%] | 70.0% |
| Rechazo indebido | 0.0% | 0.0% |
| Escaladas | 0.0% | 0.0% |
| Costo real por consulta | $0.0000 | $0.0000 |
| Latencia p50 | 19.4 s [18.1 s–20.1 s] | 17.1 s |
| Latencia p95 | 43.0 s [37.6 s–52.9 s] | 27.8 s |

## Meta del PLAN (cascada vs Bedrock-only)

No evaluable: faltan corridas de cascada y/o Bedrock-only.

## Comparabilidad

Todas las corridas comparten golden set, datos, prompt, herramientas, diccionario y guardrails.

| Proveedor | Reporte | Commit | Modelos |
|---|---|---|---|
| local | `2026-09-28-2307-test-local.json` | `18121c4` | local=`qwen/qwen3.6-35b-a3b` |
| omniroute | `2026-09-29-0051-test-omniroute.json` | `6ef921d` | omniroute=`auto/best-free` |

Precios usados (USD / 1M tokens): local=0.0/0.0, omniroute=0.0/0.0, bedrock=1.1/5.5, bedrock_ceiling=5.5/27.5.
