"""Telemetría por turno del agente (observabilidad, Fase 5).

Portado de inventory-copilot (`src/telemetry.py`) y adaptado. Cada turno produce un registro JSONL
(LB_TELEMETRY_PATH) con latencia por etapa, llamadas al LLM, herramientas, tokens, costo real y costo
equivalente en Bedrock, escalamientos de la cascada y hallazgos de guardrails. **Nunca** se guarda
el texto de la pregunta ni de la respuesta (pueden traer PII): solo un hash de la pregunta.

Correspondencia con servicios administrados:
- CloudWatch Embedded Metric Format: latency_s, llm_calls, input/output_tokens, cost_* como
  métricas; provider, outcome y stop_reason como dimensiones.
- AgentCore Observability / OpenTelemetry: el turno es el span raíz; cada entrada de `llm_calls` y
  `tools` es un span hijo (gen_ai.request.model, gen_ai.usage.input_tokens, ...).

Uso:
    python -m legacybridge.telemetry summary [--path var/telemetry.jsonl]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import statistics
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_PATH = Path(__file__).resolve().parents[2] / "var" / "telemetry.jsonl"


def _bedrock_price() -> tuple[float, float]:
    from legacybridge.llm.router import load_config
    p = load_config()["providers"]["bedrock"]["cost_per_mtok"]
    return p["input"], p["output"]


def turn_record(res, provider: str | None = None, extra: dict | None = None) -> dict:
    """Registro de un AgentResult, sin texto de pregunta ni respuesta."""
    pin, pout = _bedrock_price()
    llm_s = sum(c["latency_s"] for c in res.llm_trace)
    tools_s = sum(s.ms for s in res.steps) / 1000
    return {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "question_sha": hashlib.sha256(res.question.encode("utf-8")).hexdigest()[:16],
        "provider": provider or "default", "providers": res.providers,
        "outcome": res.outcome, "stop_reason": res.stop_reason, "confidence": res.confidence,
        "latency_s": res.latency_s,
        "latency_breakdown_s": {"llm": round(llm_s, 3), "tools": round(tools_s, 3),
                                "guardrails": round(res.guardrail_s, 4),
                                "other": round(max(res.latency_s - llm_s - tools_s - res.guardrail_s, 0), 3)},
        "llm_calls": res.llm_trace,
        "tools": [{"tool": s.tool, "ok": s.ok, "ms": s.ms} for s in res.steps],
        "input_tokens": res.input_tokens, "output_tokens": res.output_tokens,
        "sql_failures": res.sql_failures, "evidence_queries": len(res.evidence),
        "guardrail_findings": res.guardrail_findings,
        "cost_actual_usd": round(res.cost_usd, 6),
        "cost_bedrock_equiv_usd": round((res.input_tokens * pin + res.output_tokens * pout) / 1e6, 6),
        **(extra or {}),
    }


class JsonlTelemetrySink:
    def __init__(self, path: Path | str = DEFAULT_PATH):
        self.path = Path(path)

    def write(self, record: dict) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        except OSError as e:   # la telemetría nunca tumba la respuesta al usuario
            print(f"[telemetry] no se pudo escribir: {e}", file=sys.stderr)


class ListTelemetrySink:
    def __init__(self):
        self.records: list[dict] = []

    def write(self, record: dict) -> None:
        self.records.append(record)


def sink_from_env():
    """LB_TELEMETRY_PATH vacío o 'off' desactiva la telemetría."""
    path = os.environ.get("LB_TELEMETRY_PATH", "")
    return None if path in ("", "off") else JsonlTelemetrySink(path)


def summarize(records: list[dict]) -> dict:
    if not records:
        return {"turns": 0}
    lat = sorted(r["latency_s"] for r in records)
    pct = lambda p: lat[min(len(lat) - 1, round((len(lat) - 1) * p))]  # noqa: E731
    stage = {k: round(statistics.fmean(r["latency_breakdown_s"][k] for r in records), 3)
             for k in ("llm", "tools", "guardrails", "other")}
    return {
        "turns": len(records), "latency_p50_s": pct(0.5), "latency_p95_s": pct(0.95),
        "avg_latency_by_stage_s": stage,
        "outcomes": dict(Counter(r["outcome"] or r["stop_reason"] for r in records)),
        "providers_calls": dict(sum((Counter(r["providers"]) for r in records), Counter())),
        "escalated_turns": sum(bool(r.get("escalations")) for r in records),
        "tokens": {"input": sum(r["input_tokens"] for r in records), "output": sum(r["output_tokens"] for r in records)},
        "cost_actual_usd": round(sum(r["cost_actual_usd"] for r in records), 4),
        "cost_bedrock_equiv_usd": round(sum(r["cost_bedrock_equiv_usd"] for r in records), 4),
        "guardrail_findings": dict(Counter(f.split(":")[0] for r in records for f in r["guardrail_findings"])),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="legacybridge.telemetry", description="Resumen de telemetría por turno")
    ap.add_argument("cmd", choices=["summary"])
    ap.add_argument("--path", default=os.environ.get("LB_TELEMETRY_PATH") or str(DEFAULT_PATH))
    args = ap.parse_args(argv)
    path = Path(args.path)
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()] \
        if path.exists() else []
    print(json.dumps(summarize(records), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
