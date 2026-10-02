# Comparativa de proveedores — Fase 5

Split `test`. Valores: media de las repeticiones [mín–máx].

| Métrica | local (×3) | omniroute (×1) | bedrock (×3) | cascade (×3) | bedrock_ceiling (×1) |
|---|---|---|---|---|---|
| Execution accuracy | 95.4% [95.0%–96.2%] | 88.8% | 93.3% [92.5%–93.8%] | 95.4% [95.0%–96.2%] | 93.8% |
| SWAR | 3.8% [2.5%–5.0%] | 11.2% | 6.7% [6.2%–7.5%] | 4.6% [3.8%–5.0%] | 6.2% |
| Corridas terminadas | 97.0% [96.7%–97.8%] | 100.0% | 100.0% | 100.0% | 100.0% |
| Rechazo correcto (adversariales) | 76.7% [70.0%–80.0%] | 70.0% | 80.0% | 80.0% | 90.0% |
| Rechazo indebido | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% |
| Escaladas | 0.0% | 0.0% | 0.0% | 6.3% [4.4%–7.8%] | 0.0% |
| Costo real por consulta | $0.0000 | $0.0000 | $0.0281 [$0.0279–$0.0283] | $0.0018 [$0.0011–$0.0022] | $0.1315 |
| Latencia p50 | 19.4 s [18.1 s–20.1 s] | 17.1 s | 9.2 s [9.1 s–9.3 s] | 16.9 s [14.8 s–18.5 s] | 14.6 s |
| Latencia p95 | 43.0 s [37.6 s–52.9 s] | 27.8 s | 16.3 s [16.1 s–16.4 s] | 43.0 s [34.8 s–48.9 s] | 26.2 s |

## Meta del PLAN (cascada vs Bedrock-only)

- Accuracy de la cascada / Bedrock: **102.2%** (meta ≥ 95%) ✅
- Costo de la cascada / Bedrock: **6.4%** (meta ≤ 20%) ✅
- **Meta cumplida**

## Comparabilidad

Todas las corridas comparten golden set, datos, prompt, herramientas, diccionario y guardrails.

| Proveedor | Reporte | Commit | Modelos |
|---|---|---|---|
| local | `2026-09-28-2307-test-local.json` | `18121c4` | local=`qwen/qwen3.6-35b-a3b` |
| omniroute | `2026-09-29-0051-test-omniroute.json` | `6ef921d` | omniroute=`auto/best-free` |
| bedrock | `2026-10-01-2258-test-bedrock.json` | `a61a9f4` | bedrock=`us.anthropic.claude-haiku-4-5-20251001-v1:0` |
| cascade | `2026-10-01-2343-test-cascade.json` | `8eb987d` | local=`qwen/qwen3.6-35b-a3b`, bedrock=`us.anthropic.claude-haiku-4-5-20251001-v1:0` |
| bedrock_ceiling | `2026-10-02-0119-test-bedrock_ceiling.json` | `8eb987d` | bedrock_ceiling=`us.anthropic.claude-opus-4-5-20251101-v1:0` |

Precios usados (USD / 1M tokens): local=0.0/0.0, omniroute=0.0/0.0, bedrock=1.1/5.5, bedrock_ceiling=5.5/27.5.
