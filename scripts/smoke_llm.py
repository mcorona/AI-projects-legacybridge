"""Smoke test de proveedores LLM (criterio de aceptación de la Fase 0).

Uso:
    python -m scripts.smoke_llm                          # local, omniroute y bedrock
    python -m scripts.smoke_llm --provider local         # uno (repetible o separado por comas)
    python -m scripts.smoke_llm local bedrock            # forma posicional (compatibilidad)
    python -m scripts.smoke_llm --provider cascade --max-tokens 64

Cada proveedor debe devolver texto no vacío y sin bloques `<think>`. Que la respuesta sea
exactamente `SELECT 1` se reporta como advertencia (calidad), no como falla.
Sale con código 1 si algún proveedor falla.
"""
from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass

from legacybridge.llm.router import chat, extract_sql

DEFAULT_PROVIDERS = ["local", "omniroute", "bedrock"]
PROMPT = "Responde solo: SELECT 1"
EXPECTED = "SELECT 1"
# Qwen3.x razona antes de responder: con presupuestos chicos agota max_tokens en <think>.
DEFAULT_MAX_TOKENS = 1024
_THINK_TAG = re.compile(r"</?think>", re.IGNORECASE)


@dataclass
class Check:
    status: str          # OK | WARN | FAIL
    detail: str = ""


def evaluate(text: str) -> Check:
    """Valida la respuesta de un proveedor según el criterio de la Fase 0."""
    if not text.strip():
        return Check("FAIL", "respuesta vacía")
    if _THINK_TAG.search(text):
        return Check("FAIL", "contiene etiquetas <think>")
    if re.sub(r"\s+", " ", extract_sql(text)).upper() != EXPECTED:
        return Check("WARN", "respuesta distinta de 'SELECT 1'")
    return Check("OK")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(prog="smoke_llm", description=__doc__.splitlines()[0])
    ap.add_argument("positional", nargs="*", metavar="PROVIDER", help=argparse.SUPPRESS)
    ap.add_argument("-p", "--provider", action="append", default=[],
                    help="local | omniroute | bedrock | cascade (repetible o 'a,b')")
    ap.add_argument("--max-tokens", type=int, default=DEFAULT_MAX_TOKENS)
    args = ap.parse_args(argv)
    names = [n.strip() for item in args.provider + args.positional
             for n in item.split(",") if n.strip()]
    args.providers = list(dict.fromkeys(names)) or DEFAULT_PROVIDERS
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    print(f"{'provider':10} {'status':6} {'model':44} {'latency':>8} {'tokens':>9} "
          f"{'cost':>10}  {'stop':10} answer")
    failed = 0
    for p in args.providers:
        try:
            r = chat([{"role": "user", "content": PROMPT}], provider=p,
                     max_tokens=args.max_tokens)
        except Exception as e:  # noqa: BLE001 — reportar y seguir con el siguiente
            failed += 1
            cause = f" <- {e.__cause__}" if e.__cause__ else ""
            print(f"{p:10} {'FAIL':6} {type(e).__name__}: {e}{cause}"[:240])
            continue
        check = evaluate(r.text)
        failed += check.status == "FAIL"
        via = f"  via {r.provider}" if r.provider != p else ""
        note = f"  [{check.detail}]" if check.detail else ""
        print(f"{p:10} {check.status:6} {r.model[:44]:44} {r.latency_s:7.2f}s "
              f"{r.input_tokens:>4}/{r.output_tokens:<4} ${r.cost_usd:9.6f}  "
              f"{r.stop_reason or '-':10} {r.text[:40]!r}{via}{note}")
        if len(r.attempts) > 1:
            print(f"{'':10} attempts: {', '.join(r.attempts)}")
    print(f"\n{len(args.providers) - failed}/{len(args.providers)} proveedores OK")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
