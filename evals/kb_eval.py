"""Measure the knowledge base retrieval against the fixed queries (ADR-014).

    uv run python -m evals.kb_eval --run-id 20261015

Each query names the documents that would be a right answer; it hits when one of them is among the
top 3 passages. 20 queries, written before measuring, no model involved.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

import yaml

from evals.metrics import proportion
from legacybridge.semantic.knowledge import KnowledgeBase

ROOT = Path(__file__).resolve().parent
PROBES = ROOT / "kb_probes.yaml"
REPORTS = ROOT / "reports"
TOP = 3


def load_queries(path: Path = PROBES) -> list[dict[str, Any]]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))["queries"]


def measure(kb: KnowledgeBase, queries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for q in queries:
        sources = [p.source for p in kb.retrieve(q["query"])][:TOP]
        out.append({"id": q["id"], "query": q["query"], "sources": sources,
                    "hit": any(s in q["acceptable"] for s in sources)})  # fmt: skip
    return out


def render(results: list[dict[str, Any]], run_id: str) -> str:
    hits = proportion(sum(r["hit"] for r in results), len(results))
    lines = [
        f"# Knowledge base retrieval {run_id}", "",
        f"**hit@{TOP}: {hits['p']:.2f} ({hits['k']}/{hits['n']}; CI {hits['lo']:.2f}-{hits['hi']:.2f})** "
        "on `evals/kb_probes.yaml` (written before measuring). A query hits when an acceptable document "
        f"is among the top {TOP} passages.", "",
        "| Query | Hit | Top passages from |", "|---|---|---|",
    ]  # fmt: skip
    lines += [f"| {r['query']} | {'yes' if r['hit'] else '**no**'} | {', '.join(r['sources']) or 'nothing'} |"
              for r in results]  # fmt: skip
    lines += [
        "",
        "Twenty queries are a small sample, and the corpus is three documents that overlap "
        "(the ontology document repeats the defect catalogue), so a hit says the right text was "
        "found, not that this layer is needed: the ontology still answers every term it knows.",
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args(argv)

    from legacybridge.env import load_env
    from legacybridge.llm.bedrock_kb import kb_from_env

    load_env()
    kb = kb_from_env()
    if kb is None:
        print("set LB_KB_ID to the deployed knowledge base", file=sys.stderr)
        return 1
    text = render(measure(kb, load_queries()), args.run_id)
    REPORTS.mkdir(exist_ok=True)
    path = REPORTS / f"kb-{args.run_id}.md"
    path.write_text(text, encoding="utf-8")
    print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
