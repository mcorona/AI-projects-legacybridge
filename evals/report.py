"""Reporte markdown reproducible a partir de los resultados de evals.run."""
from __future__ import annotations

from collections import defaultdict

from evals.dataset import load

RATES = ["execution_accuracy", "strict_accuracy", "with_evidence_rate", "swar", "swar_uncaveated",
         "wrong_answer_rate", "correct_refusal_rate", "false_refusal_rate", "leak_rate", "completion_rate"]
OTHER = [("latency_p50_s", "s"), ("latency_p95_s", "s"), ("avg_llm_calls", ""), ("avg_input_tokens", ""),
         ("avg_output_tokens", ""), ("cost_usd", "$"), ("bedrock_equiv_cost_usd", "$")]
LABELS = {
    "execution_accuracy": "Execution accuracy (tolerante)", "strict_accuracy": "Execution accuracy (estricta)",
    "with_evidence_rate": "Con evidencia", "swar": "**SWAR** (incorrectas con confianza ≥ umbral)",
    "swar_uncaveated": "SWAR sin advertencias", "wrong_answer_rate": "Respuestas incorrectas",
    "correct_refusal_rate": "Rechazo correcto (adversariales)", "false_refusal_rate": "Rechazo indebido",
    "leak_rate": "Fugas de nombres sensibles", "completion_rate": "Corridas terminadas",
    "latency_p50_s": "Latencia p50", "latency_p95_s": "Latencia p95", "avg_llm_calls": "Llamadas LLM / pregunta",
    "avg_input_tokens": "Tokens de entrada / pregunta", "avg_output_tokens": "Tokens de salida / pregunta",
    "cost_usd": "Costo real por corrida", "bedrock_equiv_cost_usd": "Costo equivalente Bedrock Haiku por corrida",
}


def _pct(v) -> str:
    return "—" if v is None else f"{v * 100:.1f}%"


def _cell(agg: dict, kind: str) -> str:
    if agg["mean"] is None:
        return "—"
    fmt = (_pct if kind == "%" else (lambda v: f"${v:.4f}") if kind == "$"
           else (lambda v: f"{v:.1f} s") if kind == "s"
           else (lambda v: f"{v:,.0f}" if float(v).is_integer() else f"{v:,.1f}"))
    if agg["n"] > 1 and agg["min"] != agg["max"]:
        return f"{fmt(agg['mean'])} [{fmt(agg['min'])}–{fmt(agg['max'])}]"
    return fmt(agg["mean"])


def render_markdown(results: dict, items: list[dict]) -> str:
    questions = {q["id"]: q for q in load("all")}
    providers, fp = results["providers"], results["fingerprint"]
    s = results["summary"]
    dirty = " ⚠️ con cambios sin commit" if fp["git"]["dirty"] else ""
    out = [f"# Evaluación LegacyBridge — split `{results['split']}` — {', '.join(providers)}", "",
           f"- Fecha: {results['started_at']} → {results['finished_at']} (UTC)",
           f"- Commit: `{fp['git']['commit']}`{dirty}",
           f"- Preguntas: {results['questions']} × {results['repeats']} repetición(es) por proveedor",
           f"- Datos crudos: `{results['raw']}` (no versionado)", "",
           "Valores: media de las repeticiones [mín–máx]. Definiciones en `evals/metrics.py` y ADR-005.", ""]
    rs = results.get("rescore")
    if rs:
        out += [f"> **Re-puntuado** el {rs['at']} desde `{rs['from']}` sin volver a correr el agente "
                f"(commit `{rs['rescored_with_commit']}`): se reejecutó la SQL de evidencia guardada con los mismos "
                f"datos (misma huella). Reglas de comparación corregidas: {rs['rules']}. "
                f"Evidencia no reproducida: {rs['evidence_rows_mismatch']}.", "",
                "| Métrica | " + " | ".join(f"{p} original | {p} corregido" for p in providers) + " |",
                "|---" * (2 * len(providers) + 1) + "|"]
        for k in ("execution_accuracy", "strict_accuracy", "swar", "wrong_answer_rate", "correct_refusal_rate",
                  "leak_rate"):
            out.append(f"| {LABELS[k]} | " + " | ".join(
                f"{_cell(rs['original_metrics'][p][k], '%')} | {_cell(s[p]['metrics'][k], '%')}"
                for p in providers) + " |")
        out.append("")
    out += ["## Resumen", "", "| Métrica | " + " | ".join(providers) + " |", "|---" * (len(providers) + 1) + "|"]
    for k in RATES:
        out.append(f"| {LABELS[k]} | " + " | ".join(_cell(s[p]["metrics"][k], "%") for p in providers) + " |")
    for k, kind in OTHER:
        out.append(f"| {LABELS[k]} | " + " | ".join(_cell(s[p]["metrics"][k], kind) for p in providers) + " |")

    for p in providers:
        out += ["", f"## Desglose — {p}", "", "| Nivel | n | Execution accuracy | SWAR | Rechazo correcto | Terminadas |",
                "|---|---|---|---|---|---|"]
        for lvl, m in s[p]["by_level"].items():
            out.append(f"| {lvl} | {m['n']} | {_pct(m['execution_accuracy'])} | {_pct(m['swar'])} | "
                       f"{_pct(m['correct_refusal_rate'])} | {_pct(m['completion_rate'])} |")
        out += ["", "| Defecto | n | Execution accuracy | SWAR | Rechazo correcto |", "|---|---|---|---|---|"]
        for d, m in s[p]["by_defect"].items():
            out.append(f"| {d} | {m['n']} | {_pct(m['execution_accuracy'])} | {_pct(m['swar'])} | "
                       f"{_pct(m['correct_refusal_rate'])} |")

    # fallos agregados por pregunta
    fails: dict[tuple, list[dict]] = defaultdict(list)
    for i in items:
        ok = i["adversarial_ok"] if i["level"] == "adversarial" else i["match"]
        if not ok:
            fails[(i["provider"], i["id"])].append(i)
    out += ["", "## Fallos por pregunta", ""]
    if not fails:
        out.append("Ninguno.")
    else:
        out += ["| Proveedor | id | Fallos | Tipo | Confianza | Pregunta | SQL del agente (primer fallo) |",
                "|---|---|---|---|---|---|---|"]
        for (p, qid), fs in sorted(fails.items()):
            f0 = fs[0]
            kind = ("no terminó: " + f0["stop_reason"] if not f0["finished"]
                    else f"esperado {questions[qid].get('expect')}, fue {f0['outcome']}" if f0["level"] == "adversarial"
                    else "sin evidencia" if not f0["has_evidence"] else f"resultado distinto ({f0['outcome']})")
            sql = (f0["sql"] or "").replace("|", "\\|")[:160]
            q = questions[qid]["question"].replace("|", "\\|")
            out.append(f"| {p} | {qid} | {len(fs)}/{results['repeats']} | {kind} | {f0['confidence']:.2f} | {q} | "
                       f"`{sql}` |" if sql else f"| {p} | {qid} | {len(fs)}/{results['repeats']} | {kind} | "
                       f"{f0['confidence']:.2f} | {q} | |")

    flaky = [(p, f) for p in providers for f in s[p]["flaky"]]
    if flaky:
        out += ["", "## Variabilidad entre repeticiones", "", "| Proveedor | id | Aciertos |", "|---|---|---|"]
        out += [f"| {p} | {f['id']} | {f['passed']}/{f['runs']} |" for p, f in flaky]

    out += ["", "## Huella de reproducibilidad", "", "| Componente | Valor |", "|---|---|",
            f"| Commit | `{fp['git']['commit']}`{dirty} |",
            f"| Golden set (sha dev / test) | `{fp['datasets']['dev']}` / `{fp['datasets']['test']}` |",
            f"| Prompt del agente | `{fp['prompt']}` |", f"| Specs de herramientas | `{fp['tools']}` |",
            f"| Diccionario de negocio | `{fp['dictionary']}` |",
            f"| Datos (sha) | `{fp['data']['sha']}` — " + ", ".join(f"{t}={n}" for t, n in fp["data"]["rows"].items()) + " |",
            f"| Índice RAG | `{fp['rag_index']['embed_model']}` · {fp['rag_index']['chunks']} fragmentos · "
            f"`{fp['rag_index']['sha']}` |",
            "| Modelos | " + ", ".join(f"{p}=`{m}`" for p, m in fp["models"].items()) + " |",
            f"| Agente | max_steps={fp['agent']['max_steps']}, reintentos SQL={fp['agent']['max_sql_retries']}, "
            f"max_tokens={fp['agent']['max_tokens']} |",
            f"| Umbral SWAR | confianza ≥ {fp['swar_confidence']} |",
            *([f"| Guardrails | `{fp['guardrails']['sha']}` · {fp['guardrails']['type']} · clasificador LLM="
               f"{'sí' if fp['guardrails']['llm_classifier'] else 'no'} · Bedrock={'sí' if fp['guardrails']['bedrock'] else 'no'} |"]
              if "guardrails" in fp else []), "",
            "Reproducir: `make seed && make index && make eval` con la misma huella."]
    return "\n".join(out) + "\n"
