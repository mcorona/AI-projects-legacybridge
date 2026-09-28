"""Indexa el conocimiento del esquema en pgvector.

Uso:
    python -m legacybridge.rag.index              # sincroniza (incremental)
    python -m legacybridge.rag.index --dry-run    # muestra qué cambiaría, sin embeber
    python -m legacybridge.rag.index --provider bedrock
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter

from legacybridge.llm.router import embed, embed_model_id
from legacybridge.rag.sources import build_chunks
from legacybridge.rag.store import sync_index

BATCH = 32


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="rag.index", description=__doc__.splitlines()[0])
    ap.add_argument("--provider", help="proveedor de embeddings (default: EMBED_PROVIDER)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    chunks = build_chunks()
    model_id = embed_model_id(args.provider)
    tokens = 0

    def embed_fn(texts: list[str]) -> list[list[float]]:
        nonlocal tokens
        out = []
        for i in range(0, len(texts), BATCH):
            r = embed(texts[i:i + BATCH], provider=args.provider)
            if r.model_id != model_id:
                raise RuntimeError(f"modelo {r.model_id} != {model_id} esperado")
            out += r.vectors
            tokens += r.input_tokens
        return out

    kinds = ", ".join(f"{k}={n}" for k, n in sorted(Counter(c.kind for c in chunks).items()))
    print(f"{len(chunks)} fragmentos ({kinds}) -> {model_id}{' [dry-run]' if args.dry_run else ''}")
    rep = sync_index(chunks, embed_fn, model_id, dry_run=args.dry_run)
    print(f"nuevos={rep.added} modificados={rep.updated} borrados={rep.deleted} "
          f"sin cambios={rep.unchanged} embebidos={0 if args.dry_run else rep.embedded} tokens={tokens}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
