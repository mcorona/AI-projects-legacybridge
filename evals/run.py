"""Harness de evaluación de LegacyBridge (Fase 3).

Uso:
    python -m evals.run                                   # split test, LLM_PROVIDER, 1 repetición
    python -m evals.run --split test --provider local --repeats 3
    python -m evals.run --split dev --ids e001,d003 --scratch
    python -m evals.run --resume evals/results/raw/<run_id>.jsonl   # continúa una corrida cortada

Por cada pregunta y repetición corre el agente, ejecuta `gold_sql` con el mismo rol de solo lectura
y compara RESULT SETS (evals.compare). Escribe:
- evals/results/raw/<run_id>.jsonl      una línea por (pregunta, repetición); permite reanudar
- evals/reports/<fecha>-<split>-<proveedores>.{md,json}   reporte reproducible (con huella)
  (con --scratch: evals/reports/scratch/, no versionado)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from evals.compare import catalog_aliases, results_match
from evals.dataset import QUESTIONS_DIR, load
from evals.metrics import (FINISHED, SWAR_CONFIDENCE, adversarial_ok, aggregate, breakdown, item_stability,
                           leaks, run_metrics)
from evals.report import render_markdown

ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = ROOT / "evals" / "results" / "raw"
REPORTS_DIR = ROOT / "evals" / "reports"


# ---------------------------------------------------------------- puntuación

def bedrock_price() -> tuple[float, float]:
    from legacybridge.llm.router import load_config
    p = load_config()["providers"]["bedrock"]["cost_per_mtok"]
    return p["input"], p["output"]


def score_item(q: dict, r, gold: dict | None, price: tuple[float, float], provider: str, repeat: int,
               aliases=None) -> dict:
    """Registro plano de una pregunta evaluada (una línea del JSONL crudo)."""
    ev = r.primary_evidence
    tables = sorted({t for e in r.evidence for t in e.tables})
    finished = r.stop_reason in FINISHED
    match = match_strict = False
    if q["level"] != "adversarial" and finished and ev is not None and gold and gold.get("ok"):
        om = bool(q.get("order_matters"))
        match = results_match(gold["columns"], gold["rows"], ev.columns, ev.rows, om, True, aliases)
        match_strict = match and results_match(gold["columns"], gold["rows"], ev.columns, ev.rows, om, False,
                                               aliases)
    adv_ok = (finished and adversarial_ok(q["expect"], r.outcome, r.answer, tables, q["question"])
              if q["level"] == "adversarial" else None)
    return {
        "id": q["id"], "split": q["split"], "level": q["level"], "defects": q["defects"],
        "provider": provider, "repeat": repeat,
        "outcome": r.outcome, "stop_reason": r.stop_reason, "finished": finished,
        "has_evidence": ev is not None, "match": match, "match_strict": match_strict,
        "adversarial_ok": adv_ok, "leak": leaks(r.answer, q["question"]),
        "confidence": r.confidence, "model_confidence": r.model_confidence, "caveats": r.caveats,
        "sql_failures": r.sql_failures, "llm_calls": r.llm_calls,
        "input_tokens": r.input_tokens, "output_tokens": r.output_tokens,
        "latency_s": r.latency_s, "cost_usd": round(r.cost_usd, 6),
        "bedrock_equiv_cost_usd": round((r.input_tokens * price[0] + r.output_tokens * price[1]) / 1e6, 6),
        "providers_used": r.providers, "tables": tables, "guardrail_findings": r.guardrail_findings,
        "sql": ev.sql if ev else None, "row_count": ev.row_count if ev else None,
        "answer": r.answer[:500],
        # traza compacta para diagnosticar fallos sin volver a correr el agente
        "trace": [f"{'ok' if st.ok else 'FAIL'} {st.tool}: {st.summary}"[:200] for st in r.steps],
    }


# ---------------------------------------------------------------- huella de reproducibilidad

def _sha(*parts) -> str:
    h = hashlib.sha256()
    for p in parts:
        h.update(p if isinstance(p, bytes) else str(p).encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()[:16]


def _git() -> dict:
    def run(*args):
        return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    return {"commit": run("rev-parse", "--short", "HEAD"), "dirty": bool(run("status", "--porcelain"))}


def _guardrails_fingerprint(g) -> dict:
    import inspect

    from legacybridge.guard import sql_guard, write_guard
    from legacybridge.guardrails import injection, pii, pipeline, secrets
    return {"sha": _sha(*(inspect.getsource(m) for m in (injection, pii, secrets, pipeline, sql_guard, write_guard))),
            "type": type(g).__name__, "llm_classifier": g.classifier is not None, "bedrock": g.bedrock is not None}


def fingerprint(providers: list[str], agent) -> dict:
    """Todo lo que determina el resultado: código, prompt, tools, datos, índice y modelos."""
    import psycopg

    from legacybridge.agent.core import NUDGE, SYSTEM_PROMPT
    from legacybridge.dictionary import DEFAULT_PATH as DICT_PATH
    from legacybridge.llm.router import embed_model_id, load_config

    dsn = os.environ.get("LB_DSN", "postgresql://lb_ro:lb_ro@localhost:5433/legacy")
    with psycopg.connect(dsn, connect_timeout=3) as conn:
        data = {t: conn.execute(f"SELECT COUNT(*), md5(string_agg(x::text, '|' ORDER BY x::text)) "
                                f"FROM {t} x").fetchone()
                for t in ("cliemae", "artmae", "almexi", "pedenc", "peddet")}
        model_id = embed_model_id()
        rag = conn.execute("SELECT COUNT(*), md5(string_agg(content_hash, '|' ORDER BY source, chunk_index)) "
                           "FROM rag.chunks WHERE embed_model = %s", (model_id,)).fetchone()
    conf = load_config()["providers"]
    models = {}
    for p in providers:
        for name in (conf if p == "cascade" else [p]):
            models[name] = os.environ.get(conf[name]["model_env"], "?")
    return {
        "git": _git(),
        "datasets": {s: _sha((QUESTIONS_DIR / f"{s}.jsonl").read_bytes()) for s in ("dev", "test")},
        "prompt": _sha(SYSTEM_PROMPT, NUDGE),
        "tools": _sha(json.dumps(agent.toolbox.specs, sort_keys=True, ensure_ascii=False)),
        "dictionary": _sha(DICT_PATH.read_bytes()),
        "data": {"rows": {t: v[0] for t, v in data.items()}, "sha": _sha(*(v[1] for v in data.values()))},
        "rag_index": {"embed_model": model_id, "chunks": rag[0], "sha": (rag[1] or "")[:16]},
        "models": models,
        "agent": {"max_steps": agent.max_steps, "max_sql_retries": agent.max_sql_retries,
                  "max_tokens": agent.max_tokens},
        "guardrails": _guardrails_fingerprint(agent.guardrails),
        "swar_confidence": SWAR_CONFIDENCE,
    }


# ---------------------------------------------------------------- corrida

def make_agent(provider: str):
    """Agente de evaluación: guardrails del entorno, pero bitácora y propuestas en memoria
    (una evaluación no llena ops.audit_log ni registra propuestas; nunca se confirma ninguna)."""
    from legacybridge.agent import Agent
    from legacybridge.agent.proposals import ListProposalStore
    from legacybridge.guardrails import GuardrailPipeline
    from legacybridge.guardrails.audit import ListAuditSink

    return Agent(provider=provider, guardrails=GuardrailPipeline.from_env(audit=ListAuditSink()),
                 proposals=ListProposalStore(), user="evals")


def evaluate(questions: list[dict], providers: list[str], repeats: int, raw_path: Path) -> list[dict]:
    from legacybridge.mcp_servers.sql_readonly import ReadOnlyExecutor

    done = {}
    if raw_path.exists():   # reanudar: se conservan los ítems ya evaluados
        for line in raw_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                it = json.loads(line)
                done[(it["provider"], it["repeat"], it["id"])] = it
    db, price, aliases = ReadOnlyExecutor(), bedrock_price(), catalog_aliases()
    gold_cache: dict[str, dict] = {}
    total = len(questions) * repeats * len(providers)
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    with raw_path.open("a", encoding="utf-8") as raw:
        for provider in providers:
            agent = make_agent(provider)
            for rep in range(1, repeats + 1):
                for q in questions:
                    key = (provider, rep, q["id"])
                    if key in done:
                        continue
                    if q["level"] != "adversarial" and q["id"] not in gold_cache:
                        gold_cache[q["id"]] = db.run(q["gold_sql"], max_rows=200)
                    t0 = time.perf_counter()
                    try:
                        r = agent.ask(q["question"])
                        item = score_item(q, r, gold_cache.get(q["id"]), price, provider, rep, aliases)
                    except Exception as e:  # noqa: BLE001 — un fallo del harness no detiene la corrida
                        item = _crashed(q, provider, rep, e, time.perf_counter() - t0)
                    raw.write(json.dumps(item, ensure_ascii=False) + "\n")
                    raw.flush()
                    done[key] = item
                    verdict = ("✅" if (item["adversarial_ok"] if q["level"] == "adversarial" else item["match"])
                               else "❌")
                    print(f"[{len(done):>4}/{total}] {provider} r{rep} {q['id']:5} {verdict} "
                          f"{item['outcome'] or item['stop_reason']:14} {item['latency_s'] or 0:6.1f}s", flush=True)
    return [done[(p, rep, q["id"])] for p in providers for rep in range(1, repeats + 1) for q in questions]


def _crashed(q: dict, provider: str, rep: int, e: Exception, latency: float) -> dict:
    return {"id": q["id"], "split": q["split"], "level": q["level"], "defects": q["defects"],
            "provider": provider, "repeat": rep, "outcome": "", "stop_reason": f"harness_error: {type(e).__name__}",
            "finished": False, "has_evidence": False, "match": False, "match_strict": False,
            "adversarial_ok": False if q["level"] == "adversarial" else None, "leak": False,
            "confidence": 0.0, "model_confidence": None, "caveats": [], "sql_failures": 0, "llm_calls": 0,
            "input_tokens": 0, "output_tokens": 0, "latency_s": round(latency, 3), "cost_usd": 0.0,
            "bedrock_equiv_cost_usd": 0.0, "providers_used": {}, "tables": [], "sql": None,
            "row_count": None, "answer": f"{type(e).__name__}: {e}"[:500], "trace": []}


def summarize(items: list[dict], providers: list[str], repeats: int) -> dict:
    out = {}
    for p in providers:
        mine = [i for i in items if i["provider"] == p]
        runs = [run_metrics([i for i in mine if i["repeat"] == r]) for r in range(1, repeats + 1)]
        out[p] = {"metrics": aggregate(runs), "per_run": runs,
                  "by_level": breakdown(mine, "level"), "by_defect": breakdown(mine, "defects"),
                  "flaky": item_stability(mine)}
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="evals.run", description=__doc__.splitlines()[0])
    ap.add_argument("--split", default="test", choices=("dev", "test", "all", "holdout", "adversarial"))
    ap.add_argument("-p", "--provider", action="append",
                    help="local | omniroute | bedrock | cascade (repetible; default LLM_PROVIDER)")
    ap.add_argument("--repeats", type=int, default=1)
    ap.add_argument("--ids", help="subconjunto de ids separados por coma")
    ap.add_argument("--limit", type=int, help="primeras N preguntas (corridas rápidas)")
    ap.add_argument("--scratch", action="store_true", help="reporte de trabajo (no versionado)")
    ap.add_argument("--resume", help="JSONL crudo de una corrida anterior para continuarla")
    args = ap.parse_args(argv)
    logging.getLogger("httpx").setLevel(logging.WARNING)

    providers = args.provider or [os.environ.get("LLM_PROVIDER", "local")]
    questions = load(args.split)
    if args.ids:
        wanted = set(args.ids.split(","))
        questions = [q for q in load("all") if q["id"] in wanted]
    if args.limit:
        questions = questions[:args.limit]
    raw_path = Path(args.resume) if args.resume else RAW_DIR / f"{uuid.uuid4().hex[:8]}.jsonl"
    started = datetime.now(timezone.utc)
    print(f"{len(questions)} preguntas × {args.repeats} rep × {providers} -> {raw_path}", flush=True)

    fp = fingerprint(providers, make_agent(providers[0]))
    items = evaluate(questions, providers, args.repeats, raw_path)
    results = {"started_at": started.isoformat(timespec="seconds"),
               "finished_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
               "split": args.split if not args.ids else "custom", "questions": len(questions),
               "repeats": args.repeats, "providers": providers, "raw": str(raw_path.relative_to(ROOT)),
               "fingerprint": fp, "summary": summarize(items, providers, args.repeats)}

    out_dir = REPORTS_DIR / "scratch" if args.scratch or args.ids or args.limit else REPORTS_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{started:%Y-%m-%d}-{results['split']}-{'+'.join(providers)}"
    (out_dir / f"{stem}.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / f"{stem}.md").write_text(render_markdown(results, items), encoding="utf-8")
    print(f"\nReporte: {(out_dir / f'{stem}.md').relative_to(ROOT)}")
    for p, s in results["summary"].items():
        m = s["metrics"]
        print(f"{p}: execution_accuracy={m['execution_accuracy']['mean']} swar={m['swar']['mean']} "
              f"correct_refusal={m['correct_refusal_rate']['mean']} completion={m['completion_rate']['mean']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
