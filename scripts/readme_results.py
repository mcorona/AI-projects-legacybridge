"""The results block of the README, generated from the archived run (Phase 8).

    uv run python -m scripts.readme_results --write     # rewrite the block in README.md
    uv run python -m scripts.readme_results --check     # exit 1 when README.md is out of date
    uv run python -m scripts.readme_results             # print the block

Every figure comes from ``evals/archive/<run id>/`` through the same functions that build
``docs/eval-report.md`` (``evals.metrics.summarize``, ``compare_arms``), so a number can only be in
the README if the data says it. A test and a CI job run ``--check``.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from evals import report
from evals.guardrails import Outcome
from evals.guardrails import summarize as summarize_guardrail
from evals.metrics import compare_arms, summarize
from evals.runner import models_used

ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"
ARCHIVE = ROOT / "evals" / "archive"
RUN_ID = "20261009"
START, END = "<!-- results:start -->", "<!-- results:end -->"
ORDER = [
    "local-qwen",
    "omniroute-free",
    "bedrock-claude",
    "bedrock-claude-sonnet",
    "bedrock-claude-opus",
]


def count(p: dict) -> str:
    """``k/n`` for a proportion: in prose a count reads better than a rate with an interval."""
    return f"{p['k']}/{p['n']}"


def cases(n: int) -> str:
    return f"{n} case" if n == 1 else f"{n} cases"


def _cost(usage: dict) -> str:
    if not usage["n"]:
        return "n/a"
    return f"${usage['usd_per_question']:.3f}" if usage["priced"] else "free (local or free tier)"


def _summaries(run: report.Run) -> dict:
    return {
        key: summarize(records, report.GOLDEN, prices=report.prices(key[0]), attacks=run.attacks)
        for key, records in run.arms.items()
        if records
    }


def profile_table(run: report.Run, summaries: dict) -> list[str]:
    rows = []
    for profile in [p for p in ORDER if (p, "full") in summaries]:
        s = summaries[(profile, "full")]
        model = ", ".join(models_used(run.records(profile))) or "?"
        rows.append(
            [
                f"`{profile}`",
                f"`{model}`",
                report.short(s["handled_correctly"]),
                report.short(s["swar"]["swar"]),
                report.short(s["swar"]["silently_wrong"]),
                report.short(s["defect_compliance"]["overall"]),
                _cost(s["usage"]),
                report.secs(s["latency"]["p50"]),
            ]
        )
    head = [
        "Profile",
        "Model",
        "Handled correctly (80)",
        "SWAR rate¹",
        "Silently wrong²",
        "Defect traps (20)",
        "Cost / question",
        "Latency p50",
    ]
    return report.table(head, rows)


def comparison_lines(run: report.Run) -> list[str]:
    lines = []
    for _family, pairs in report.COMPARISON_FAMILIES:
        alpha = report.comparison_alpha(pairs)
        for reference, other in pairs:
            a, b = run.records(reference), run.records(other)
            if not a or not b:
                continue
            c = compare_arms(a, b)
            verdict = report._verdict(c, alpha, other, reference)
            lines.append(
                f"- `{reference}` vs `{other}`: right only with the first {c['only_reference']}, "
                f"only with the second {c['only_other']}; exact McNemar p = {c['p_value']:.4f}, "
                f"{verdict}."
            )
    return lines


def ablation_lines(run: report.Run) -> list[str]:
    ref = run.records(report.ABLATION_PROFILE, "full")
    out = []
    for arm, what in (
        ("no-verify", "the independent verifier"),
        ("no-semantic", "the semantic layer"),
    ):
        records = run.records(report.ABLATION_PROFILE, arm)
        if not records or not ref:
            continue
        c = compare_arms(ref, records)
        stuck = report.unfinished(records)
        note = (
            f"; {stuck} of {len(records)} runs ended at the writer's step limit, so the drop mixes "
            "lost steps with wrong answers"
            if stuck > len(ref) / 4
            else ""
        )
        verdict = report._verdict(c, 0.05, "the ablated system", "the full system")
        out.append(
            f"- Without {what} (`{report.ABLATION_PROFILE}`): "
            f"{cases(c['only_reference'])} only the full system got right against "
            f"{c['only_other']}, p = {c['p_value']:.2f}, {verdict}{note}."
        )
    return out


def guardrail_line(path: Path) -> str | None:
    if not path.exists():
        return None
    outcomes = [Outcome(**o) for o in json.loads(path.read_text(encoding="utf-8"))]
    s = summarize_guardrail(outcomes)
    return (
        f"- **Guardrail** (Bedrock, deployed and measured on AWS, then destroyed): "
        f"{count(s['attacks_refused'])} of the attacks it should stop were blocked; "
        f"{count(s['legit_blocked'])} of the golden questions were blocked (one of them a "
        f"legitimate write request: a real false positive); output PII masked on "
        f"{count(s['output_pii_caught'])} probes."
    )


def build_block(archive: Path = ARCHIVE, run_id: str = RUN_ID) -> str:
    directory = archive / run_id
    run = report.load_run(run_id, directory)
    summaries = _summaries(run)
    sec = summarize([], report.GOLDEN, attacks=run.attacks)["security"]
    lines = [START, "", *profile_table(run, summaries)]
    lines += [
        "¹ Delivered answers with confidence ≥ 0.8 that are wrong. "
        "² Delivered answers that are wrong, at any confidence.",
        "",
        f"One run per model over the 80 golden cases (`review: draft`: no person has validated the "
        f"expected results). Run `{run_id}`, raw results in `evals/archive/{run_id}/`.",
        "",
        f"- **Security:** {count(sec['pass_rate'])} attacks blocked or neutralized; "
        f"{count(sec['database_refuses'])} of those with a database-level check are "
        "refused by the read-only role alone. This does not depend on a model.",
    ]
    if line := guardrail_line(directory / f"guardrails-{run_id}.json"):
        lines.append(line)
    lines += ["", "Paired by case, read at the level fixed before the runs (ADR-014):", ""]
    lines += comparison_lines(run)
    lines += ["", "Ablations (ADR-013), also paired:", ""]
    lines += ablation_lines(run)
    lines += ["", END]
    return "\n".join(lines)


def replace_block(text: str, block: str) -> str:
    if START not in text or END not in text:
        raise SystemExit(f"README.md needs the markers {START} and {END}")
    head, rest = text.split(START, 1)
    _, tail = rest.split(END, 1)
    return head + block + tail


def current_block(text: str) -> str:
    head, rest = text.split(START, 1)
    body, _ = rest.split(END, 1)
    return START + body + END


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--write", action="store_true")
    mode.add_argument("--check", action="store_true")
    parser.add_argument("--readme", type=Path, default=README)
    args = parser.parse_args(argv)

    block = build_block()
    if not (args.write or args.check):
        print(block)
        return 0
    text = args.readme.read_text(encoding="utf-8")
    if args.check:
        if current_block(text) != block:
            print(
                f"{args.readme.name} is out of date: run `uv run python -m scripts.readme_results "
                "--write`",
                file=sys.stderr,
            )
            return 1
        print("README results block is up to date")
        return 0
    args.readme.write_text(replace_block(text, block), encoding="utf-8")
    print(f"wrote the results block of {args.readme}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
