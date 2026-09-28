"""Tabla comparativa entre corridas separadas y verificación de la meta de la Fase 5 (ADR-007).

Uso:
    python -m evals.compare_runs evals/reports/<local>.json evals/reports/<bedrock>.json ... \
        [--out evals/reports/<fecha>-phase5-comparison.md]

Cada corrida puede venir de un reporte distinto (proveedores que se evalúan en momentos distintos).
Antes de comparar se verifica que las huellas relevantes coincidan: golden set, datos, prompt, specs de
herramientas, diccionario y guardrails. Si no coinciden, la tabla se marca como NO comparable.

Meta del PLAN: cascada >= 95 % de la execution accuracy de Bedrock-only con <= 20 % de su costo real.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

MUST_MATCH = ("datasets", "prompt", "tools", "dictionary", "data", "guardrails")
META_ACCURACY_RATIO, META_COST_RATIO = 0.95, 0.20


def load_runs(paths: list[str]) -> list[dict]:
    """Una fila por (reporte, proveedor)."""
    runs = []
    for p in paths:
        rep = json.loads(Path(p).read_text(encoding="utf-8"))
        for provider, s in rep["summary"].items():
            runs.append({"provider": provider, "report": Path(p).name, "repeats": rep["repeats"],
                         "split": rep["split"], "fingerprint": rep["fingerprint"], "metrics": s["metrics"]})
    return runs


def fingerprint_mismatches(runs: list[dict]) -> list[str]:
    base = runs[0]["fingerprint"]
    out = []
    for r in runs[1:]:
        for k in MUST_MATCH:
            a, b = base.get(k), r["fingerprint"].get(k)
            if k == "data":
                a, b = (a or {}).get("sha"), (b or {}).get("sha")
            if k == "guardrails":
                a, b = (a or {}).get("sha"), (b or {}).get("sha")
            if a != b:
                out.append(f"{r['provider']} ({r['report']}): `{k}` difiere de {runs[0]['provider']}")
    splits = {r["split"] for r in runs}
    if len(splits) > 1:
        out.append(f"splits distintos: {sorted(splits)}")
    return out


def _m(run: dict, key: str):
    v = run["metrics"].get(key) or {}
    return v.get("mean")


def meta_check(runs: list[dict]) -> dict:
    by = {r["provider"]: r for r in runs}
    if "cascade" not in by or "bedrock" not in by:
        return {"evaluable": False, "reason": "faltan corridas de cascada y/o Bedrock-only"}
    c, b = by["cascade"], by["bedrock"]
    acc_c, acc_b = _m(c, "execution_accuracy"), _m(b, "execution_accuracy")
    cost_c, cost_b = _m(c, "cost_per_query_usd"), _m(b, "cost_per_query_usd")
    if None in (acc_c, acc_b, cost_c, cost_b) or not acc_b or not cost_b:
        return {"evaluable": False, "reason": "métricas incompletas"}
    acc_ratio, cost_ratio = acc_c / acc_b, cost_c / cost_b
    return {"evaluable": True, "accuracy_ratio": round(acc_ratio, 4), "cost_ratio": round(cost_ratio, 4),
            "accuracy_ok": acc_ratio >= META_ACCURACY_RATIO, "cost_ok": cost_ratio <= META_COST_RATIO,
            "met": acc_ratio >= META_ACCURACY_RATIO and cost_ratio <= META_COST_RATIO}


def _fmt(v, kind: str) -> str:
    if v is None:
        return "—"
    return {"%": f"{v * 100:.1f}%", "$": f"${v:.4f}", "s": f"{v:.1f} s"}.get(kind, str(v))


def _cell(run: dict, key: str, kind: str) -> str:
    v = run["metrics"].get(key) or {}
    if v.get("mean") is None:
        return "—"
    if v.get("n", 1) > 1 and v["min"] != v["max"]:
        return f"{_fmt(v['mean'], kind)} [{_fmt(v['min'], kind)}–{_fmt(v['max'], kind)}]"
    return _fmt(v["mean"], kind)


ROWS = [("Execution accuracy", "execution_accuracy", "%"), ("SWAR", "swar", "%"),
        ("Corridas terminadas", "completion_rate", "%"), ("Rechazo correcto (adversariales)", "correct_refusal_rate", "%"),
        ("Rechazo indebido", "false_refusal_rate", "%"), ("Escaladas", "escalation_rate", "%"),
        ("Costo real por consulta", "cost_per_query_usd", "$"), ("Latencia p50", "latency_p50_s", "s"),
        ("Latencia p95", "latency_p95_s", "s")]


def render(runs: list[dict]) -> str:
    mism, meta = fingerprint_mismatches(runs), meta_check(runs)
    head = " | ".join(f"{r['provider']} (×{r['repeats']})" for r in runs)
    out = ["# Comparativa de proveedores — Fase 5", "",
           f"Split `{runs[0]['split']}`. Valores: media de las repeticiones [mín–máx].", "",
           f"| Métrica | {head} |", "|---" * (len(runs) + 1) + "|"]
    out += [f"| {label} | " + " | ".join(_cell(r, key, kind) for r in runs) + " |" for label, key, kind in ROWS]
    pricing = runs[0]["fingerprint"].get("pricing", {}).get("per_mtok", {})
    out += ["", "## Meta del PLAN (cascada vs Bedrock-only)", ""]
    if meta["evaluable"]:
        out.append(f"- Accuracy de la cascada / Bedrock: **{meta['accuracy_ratio'] * 100:.1f}%** "
                   f"(meta ≥ {META_ACCURACY_RATIO * 100:.0f}%) {'✅' if meta['accuracy_ok'] else '❌'}")
        out.append(f"- Costo de la cascada / Bedrock: **{meta['cost_ratio'] * 100:.1f}%** "
                   f"(meta ≤ {META_COST_RATIO * 100:.0f}%) {'✅' if meta['cost_ok'] else '❌'}")
        out.append(f"- **{'Meta cumplida' if meta['met'] else 'Meta no cumplida'}**")
    else:
        out.append(f"No evaluable: {meta['reason']}.")
    out += ["", "## Comparabilidad", ""]
    out += ([f"- ⚠️ {m}" for m in mism] + ["", "**Tabla NO comparable** en esos componentes."] if mism
            else ["Todas las corridas comparten golden set, datos, prompt, herramientas, diccionario y guardrails."])
    out += ["", "| Proveedor | Reporte | Commit | Modelos |", "|---|---|---|---|"]
    out += [f"| {r['provider']} | `{r['report']}` | `{r['fingerprint']['git']['commit']}` | "
            + ", ".join(f"{k}=`{v}`" for k, v in r["fingerprint"].get("models", {}).items()) + " |" for r in runs]
    if pricing:
        out += ["", "Precios usados (USD / 1M tokens): "
                + ", ".join(f"{p}={v['input']}/{v['output']}" for p, v in pricing.items() if v) + "."]
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="evals.compare_runs", description=__doc__.splitlines()[0])
    ap.add_argument("reports", nargs="+")
    ap.add_argument("--out")
    args = ap.parse_args(argv)
    md = render(load_runs(args.reports))
    if args.out:
        Path(args.out).write_text(md, encoding="utf-8")
    print(md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
