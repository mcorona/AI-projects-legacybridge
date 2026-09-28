"""Smoke test de los proveedores: python -m scripts.smoke_llm [local|omniroute|bedrock|cascade]."""
import sys

from legacybridge.llm.router import chat

providers = sys.argv[1:] or ["local", "omniroute", "bedrock"]
for p in providers:
    try:
        r = chat([{"role": "user", "content": "Responde solo: SELECT 1"}], provider=p, max_tokens=64)
        print(f"{p:10} OK  {r.model:45} {r.latency_s:5.2f}s  ${r.cost_usd:.6f}  -> {r.text[:40]!r}")
    except Exception as e:  # noqa: BLE001
        print(f"{p:10} FAIL {type(e).__name__}: {e}"[:160])
