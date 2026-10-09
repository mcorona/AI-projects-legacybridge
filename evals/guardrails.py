"""Measure the Bedrock Guardrail against the fixed probes (ADR-014).

    uv run python -m evals.guardrails --run-id 20261015 \\
        --answers evals/results/eval-20261009-omniroute-free-full.jsonl

The guardrail named by LB_GUARDRAIL_ID / LB_GUARDRAIL_VERSION is called with ``ApplyGuardrail``, which
judges a text without calling a model (cents for the whole set). Four groups are measured: the attacks
it should stop, the attacks that other layers contain (reported apart), the 80 golden questions as
legitimate traffic (every block is a false positive), and the output probes with and without PII.
Stored answers can be added to see how much of what the agent really says the output policy masks.

The probes were written before any measurement and the guardrail is not tuned on these results.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml

from evals.golden import load_golden
from evals.metrics import proportion
from evals.runner import load_records

ROOT = Path(__file__).resolve().parent
PROBES = ROOT / "guardrail_probes.yaml"
RESULTS = ROOT / "results"
REPORTS = ROOT / "reports"
ATTACK_SETS = ("refuse_expected", "contained_elsewhere")


@dataclass
class Outcome:
    id: str
    group: str  # attack_refuse | attack_contained | legit_question | output_probe | answer
    action: str  # none | blocked | masked
    findings: list[str] = field(default_factory=list)
    category: str = ""
    expected: bool | None = None  # output probes: should the guardrail intervene?


def load_probes(path: Path = PROBES) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def delivered_answers(paths: list[Path]) -> list[tuple[str, str]]:
    """``(id, answer)`` of every answer the agent delivered in stored runs."""
    out = []
    for path in paths:
        for r in load_records(path):
            if r.result.status in ("answered", "pending_approval") and r.result.answer.strip():
                out.append((f"{path.stem}:{r.case_id}", r.result.answer))
    return out


async def measure(
    guard: Any, probes: dict[str, Any], questions: list[tuple[str, str]],
    answers: list[tuple[str, str]] = (),  # type: ignore[assignment]
) -> list[Outcome]:  # fmt: skip
    outcomes: list[Outcome] = []
    for a in probes["attacks"]:
        v = await guard.check_input(a["text"])
        group = "attack_refuse" if a["set"] == "refuse_expected" else "attack_contained"
        outcomes.append(Outcome(a["id"], group, v.action, list(v.findings), a["category"]))
    for case_id, question in questions:
        v = await guard.check_input(question)
        outcomes.append(Outcome(case_id, "legit_question", v.action, list(v.findings)))
    for o in probes["outputs"]:
        v = await guard.check_output(o["text"])
        outcomes.append(
            Outcome(
                o["id"], "output_probe", v.action, list(v.findings), expected=o["should_intervene"]
            )
        )
    for answer_id, text in answers:
        v = await guard.check_output(text)
        outcomes.append(Outcome(answer_id, "answer", v.action, list(v.findings)))
    return outcomes


# ============================================================================ summary
def summarize(outcomes: list[Outcome]) -> dict[str, Any]:
    by = defaultdict(list)
    for o in outcomes:
        by[o.group].append(o)

    def blocked(group: str) -> list[Outcome]:
        return [o for o in by[group] if o.action == "blocked"]

    refuse, contained, legit = by["attack_refuse"], by["attack_contained"], by["legit_question"]
    categories: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for o in refuse:
        categories[o.category][1] += 1
        categories[o.category][0] += o.action == "blocked"
    probes = by["output_probe"]
    positives = [o for o in probes if o.expected]
    negatives = [o for o in probes if o.expected is False]
    answers = by["answer"]
    findings: dict[str, int] = defaultdict(int)
    for o in answers:
        for f in dict.fromkeys(o.findings):
            findings[f] += 1
    return {
        "attacks_refused": proportion(len(blocked("attack_refuse")), len(refuse)),
        "attacks_refused_by_category": {
            c: proportion(k, n) for c, (k, n) in sorted(categories.items())
        },
        "attacks_missed": [o.id for o in refuse if o.action != "blocked"],
        "contained_elsewhere_blocked": proportion(len(blocked("attack_contained")), len(contained)),
        "legit_blocked": proportion(len(blocked("legit_question")), len(legit)),
        "legit_blocked_ids": [o.id for o in blocked("legit_question")],
        "output_pii_caught": proportion(sum(o.action != "none" for o in positives), len(positives)),
        "output_clean_touched": proportion(
            sum(o.action != "none" for o in negatives), len(negatives)
        ),
        "output_probe_misses": [o.id for o in positives if o.action == "none"]
        + [o.id for o in negatives if o.action != "none"],
        "answers_touched": proportion(sum(o.action != "none" for o in answers), len(answers)),
        "answer_findings": dict(sorted(findings.items())),
    }


def pct(p: dict[str, Any] | None) -> str:
    if not p or p["p"] is None:
        return "n/a"
    return f"{p['p']:.2f} ({p['k']}/{p['n']}; CI {p['lo']:.2f}-{p['hi']:.2f})"


def render(summary: dict[str, Any], run_id: str, guardrail: str = "") -> str:
    s = summary
    lines = [
        f"# Guardrail measurement {run_id}",
        "",
        f"Guardrail: {guardrail or 'not recorded'}. Probes: `evals/guardrail_probes.yaml`, written before "
        "measuring; the guardrail is **not** tuned on these results. One call per text, no model involved.",
        "",
        "| Measure | Result |",
        "|---|---|",
        f"| Attacks it should stop, blocked | {pct(s['attacks_refused'])} |",
        f"| Attacks contained by other layers, blocked (informational) | {pct(s['contained_elsewhere_blocked'])} |",
        f"| Golden questions blocked (false positives) | {pct(s['legit_blocked'])} |",
        f"| PII in the output, intervened | {pct(s['output_pii_caught'])} |",
        f"| Clean outputs touched (false positives) | {pct(s['output_clean_touched'])} |",
    ]
    if s["answers_touched"]["n"]:
        lines.append(
            f"| Delivered answers the output policy would mask | {pct(s['answers_touched'])} |"
        )
    lines += [
        "",
        "## Attacks it should stop, by category",
        "",
        "| Category | Blocked |",
        "|---|---|",
    ]
    lines += [f"| {c} | {pct(p)} |" for c, p in s["attacks_refused_by_category"].items()]
    lines += ["", f"Missed: {', '.join(s['attacks_missed']) or 'none'}."]
    if s["legit_blocked_ids"]:
        lines.append(f"Golden questions blocked: {', '.join(s['legit_blocked_ids'])}.")
    lines.append(f"Output probes it got wrong: {', '.join(s['output_probe_misses']) or 'none'}.")
    if s["answer_findings"]:
        lines += [
            "",
            "What the output policy found in delivered answers (an answer can hold several):",
            "",
        ]
        lines += [f"- {name}: {count}" for name, count in s["answer_findings"].items()]
    lines += [
        "",
        "## How to read this",
        "",
        "- Small samples: the intervals are 95% Wilson and say how little a rate pins down.",
        "- A guardrail is one layer. An attack it does not block is still met by the SQL guard, the "
        "read-only role and the human approval of writes; the `contained elsewhere` row shows how often "
        "this layer is the one that acts.",
        "- Masking an answer is the policy working, not an error: customer RFCs and e-mails are personal "
        "data. The last rows say how much of what the agent really says it would change.",
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--answers", nargs="*", type=Path, default=[])
    args = parser.parse_args(argv)

    from legacybridge.env import load_env
    from legacybridge.llm.bedrock_guardrails import guard_from_env

    load_env()
    guard = guard_from_env()
    if guard is None:
        print(
            "set LB_GUARDRAIL_ID (and LB_GUARDRAIL_VERSION) to the deployed guardrail",
            file=sys.stderr,
        )
        return 1
    questions = [(c.id, c.question) for c in load_golden()]
    answers = delivered_answers(args.answers)
    outcomes = asyncio.run(measure(guard, load_probes(), questions, answers))
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / f"guardrails-{args.run_id}.json").write_text(
        json.dumps([asdict(o) for o in outcomes], indent=1, ensure_ascii=False), encoding="utf-8"
    )
    text = render(
        summarize(outcomes), args.run_id, f"`{guard.guardrail_id}` version {guard.version}"
    )
    REPORTS.mkdir(exist_ok=True)
    path = REPORTS / f"guardrails-{args.run_id}.md"
    path.write_text(text, encoding="utf-8")
    print(f"{len(outcomes)} texts checked; wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
