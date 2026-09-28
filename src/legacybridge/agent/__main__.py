"""CLI del agente.

Uso:
    python -m legacybridge.agent "¿Cuántos clientes activos hay?"
    python -m legacybridge.agent --provider bedrock "..."     # local | omniroute | bedrock | cascade
    python -m legacybridge.agent --json "..."                 # resultado completo en JSON
"""
from __future__ import annotations

import argparse
import json
import logging
import sys

from legacybridge.agent.core import Agent, AgentResult


def render(r: AgentResult) -> str:
    out = [f"\n{r.answer}\n",
           f"outcome={r.outcome}  confianza={r.confidence:.2f}"
           + (f" (modelo {r.model_confidence:.2f})" if r.model_confidence is not None else "")
           + f"  stop={r.stop_reason}"]
    if r.caveats:
        out += ["\nAdvertencias:"] + [f"  - {c}" for c in r.caveats]
    ev = r.primary_public_evidence
    if ev:
        out += ["\nEvidencia:", f"  SQL: {ev.sql}", f"  Tablas: {', '.join(ev.tables)}",
                f"  Filas: {ev.row_count}{' (truncado)' if ev.truncated else ''}"]
        widths = [max(len(str(x)) for x in [c, *(row[i] for row in ev.rows[:10])])
                  for i, c in enumerate(ev.columns)]
        out.append("  " + " | ".join(str(c).ljust(w) for c, w in zip(ev.columns, widths)))
        out += ["  " + " | ".join(str(v).ljust(w) for v, w in zip(row, widths)) for row in ev.rows[:10]]
        if ev.row_count > 10:
            out.append(f"  … {ev.row_count - 10} filas más")
    out.append("\nTraza:")
    out += [f"  {'✓' if s.ok else '✗'} {s.tool:18} {s.summary}" for s in r.steps]
    providers = ", ".join(f"{p}×{n}" for p, n in r.providers.items())
    out.append(f"\n{r.latency_s:.1f}s · {r.llm_calls} llamadas LLM ({providers}) · "
               f"tokens {r.input_tokens}/{r.output_tokens} · ${r.cost_usd:.6f}"
               + (f" · reintentos SQL {r.sql_failures}" if r.sql_failures else ""))
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="legacybridge.agent", description=__doc__.splitlines()[0])
    ap.add_argument("question", nargs="+")
    ap.add_argument("-p", "--provider", help="local | omniroute | bedrock | cascade (default: LLM_PROVIDER)")
    ap.add_argument("--json", action="store_true", help="imprime el AgentResult completo en JSON")
    args = ap.parse_args(argv)
    logging.getLogger("httpx").setLevel(logging.WARNING)

    r = Agent(provider=args.provider).ask(" ".join(args.question))
    print(json.dumps(r.to_dict(), ensure_ascii=False, indent=2, default=str) if args.json else render(r))
    return 0 if r.stop_reason in ("submitted", "blocked_input") else 1


if __name__ == "__main__":
    sys.exit(main())
