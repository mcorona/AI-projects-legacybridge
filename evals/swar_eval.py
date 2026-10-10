"""Measure the SWAR detector against golden-set labels (ADR-012).

    uv run python -m evals.swar_eval baseline                       # no LLM: static signals
    uv run python -m evals.swar_eval live --profile local-qwen      # the 20-case subset, live
    uv run python -m evals.swar_eval report evals/results/<file>.jsonl [...] --name local-qwen

``live`` runs with ``swar_action=annotate`` so the early warning cannot hide the very answers we
need to score; what it would have done is read back from the stored events. Raw results go to
``evals/results/`` (not versioned); the report goes to ``evals/reports/`` (versioned).
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import UTC, datetime
from pathlib import Path

import yaml

from evals.golden import APPROVED_ON, Behavior, GoldenCase, all_approved, load_golden
from evals.labels import label_run
from evals.metrics import (  # noqa: F401 - re-exported
    Confusion,
    confusion,
    population,
    wilson,
)
from evals.runner import (  # noqa: F401 - re-exported
    RESULTS,
    Record,
    describe_rows,
    load_records,
    run_golden,
    run_oracle,
)
from legacybridge.agent.schemas import AgentResult
from legacybridge.db import connect
from legacybridge.observability.swar_config import SwarConfig, load_swar_config
from legacybridge.observability.swar_detector import assess, combine, facts_from_result, risk_at
from legacybridge.semantic.loader import Ontology, load_ontology

ROOT = Path(__file__).resolve().parent
SUBSET_PATH = ROOT / "swar_subset.yaml"
REPORTS = ROOT / "reports"
THRESHOLDS = (0.3, 0.5, 0.7)
STATIC_SIGNALS = {"unresolved_violations", "question_defect_violated", "unstated_parameter"}
RULE_SIGNALS = {"unresolved_violations", "question_defect_violated"}


def load_subset(path: Path = SUBSET_PATH) -> list[GoldenCase]:
    golden = {c.id: c for c in load_golden()}
    ids = yaml.safe_load(path.read_text(encoding="utf-8"))["cases"]
    return [golden[i] for i in ids]


def relabel(records: list[Record]) -> list[Record]:
    """Label stored runs again with the current rules (see evals/labels.py).

    Write cases keep their stored label: it was computed from the queued request, which is deleted
    after each run so that the queue stays clean, so it cannot be read back."""
    golden = {c.id: c for c in load_golden()}
    out = []
    with connect("reader", autocommit=True) as reader:
        for r in records:
            case = golden[r.case_id]
            if case.expected_behavior == Behavior.PROPOSE_WRITE:
                out.append(r)
                continue
            label = label_run(case, r.result, reader)
            out.append(r.model_copy(update={"kind": label.kind, "reason": label.reason}))
    return out


def rescore(
    records: list[Record], ontology: Ontology | None = None, cfg: SwarConfig | None = None
) -> list[Record]:
    """Score stored runs again with the current detector.

    The detector is a pure function of what a result stores (events, evidence, verdicts, answer),
    so a stored run can be re-assessed after the detector is fixed. The labels do not change."""
    ontology, cfg = ontology or load_ontology(), cfg or load_swar_config()
    out = []
    for r in records:
        facts = facts_from_result(r.result)
        facts.events = [
            e for e in r.result.events if e.escalated == (r.result.escalated_from is not None)
        ]
        fresh = assess(facts, ontology, cfg, "final")
        merged = {s.name: s for s in (r.result.swar_signals if r.result.early_warning else [])}
        for sig in fresh.signals:
            merged.setdefault(sig.name, sig)
        risk = max(fresh.risk, r.result.swar_risk if r.result.early_warning else 0.0)
        result = r.result.model_copy(
            update={
                "swar_risk": risk,
                "swar_level": cfg.level(risk),
                "swar_signals": list(merged.values()),
            }
        )
        out.append(r.model_copy(update={"result": result}))
    return out


# ============================================================================ metrics
def risk_with(
    result: AgentResult, cfg: SwarConfig, keep: set[str] | None, drop: set[str] | None = None
) -> float:
    signals = [
        s for s in result.swar_signals
        if (keep is None or s.name in keep) and (drop is None or s.name not in drop)
    ]  # fmt: skip
    return combine(signals, cfg)


def fmt_ratio(value: float | None, successes: int, n: int) -> str:
    if value is None:
        return "n/a (no cases)"
    lo, hi = wilson(successes, n)
    return f"{value:.2f} ({successes}/{n}; 95% CI {lo:.2f}-{hi:.2f})"


def confusion_table(rows: list[tuple[float, bool]]) -> list[str]:
    out = [
        "| threshold | flagged | TP | FP | FN | TN | precision | recall |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for t in THRESHOLDS:
        c = confusion(rows, t)
        out.append(
            f"| >= {t:.1f} | {c.tp + c.fp} | {c.tp} | {c.fp} | {c.fn} | {c.tn} | "
            f"{fmt_ratio(c.precision, c.tp, c.tp + c.fp)} | {fmt_ratio(c.recall, c.tp, c.tp + c.fn)} |"
        )
    return out


# ============================================================================ report
def build_report(
    records: list[Record],
    name: str,
    ontology: Ontology | None = None,
    cfg: SwarConfig | None = None,
    notes: list[str] | None = None,
    as_run: list[Record] | None = None,
) -> str:
    ontology, cfg = ontology or load_ontology(), cfg or load_swar_config()
    pop = population(records)
    wrong = [r for r in pop if r.kind == "silently_wrong"]
    by_kind = {
        k: sum(1 for r in records if r.kind == k)
        for k in ("correct", "silently_wrong", "loud_failure", "unverifiable")
    }
    rows = [(r.result.swar_risk, r.kind == "silently_wrong") for r in pop]
    now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        f"# SWAR detector measurement: {name}",
        "",
        f"Generated {now} by `evals/swar_eval.py`. Method and limits: `docs/adr/ADR-012-swar-detector.md`.",
        "",
        f"- Runs: **{len(records)}** ({', '.join(f'{v} {k}' for k, v in by_kind.items())}).",
        f"- Population for precision/recall: **{len(pop)}** delivered answers with a known label, "
        f"of which **{len(wrong)} silently wrong**.",
        "- Weights and thresholds are priors from `config/swar.yaml`; none was fitted to these results.",
        "",
    ]
    lines += [f"> {n}" for n in (notes or [])] + ([""] if notes else [])

    lines += [
        "## Per run",
        "",
        "| case | variant | status | label | risk | level | signals |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in records:
        sig = ", ".join(s.name for s in r.result.swar_signals) or "-"
        lines.append(
            f"| {r.case_id} | {r.variant} | {r.result.status} | {r.kind} | {r.result.swar_risk:.2f} | "
            f"{r.result.swar_level} | {sig} |"
        )

    lines += ["", "## Precision and recall (all signals)", ""]
    lines += confusion_table(rows)
    mean = lambda xs: sum(xs) / len(xs) if xs else float("nan")  # noqa: E731
    lines += [
        "",
        f"Mean risk: silently wrong **{mean([r for r, w in rows if w]):.2f}** "
        f"(n={sum(1 for _, w in rows if w)}), correct **{mean([r for r, w in rows if not w]):.2f}** "
        f"(n={sum(1 for _, w in rows if not w)}).",
    ]

    if as_run is not None:
        before = [(r.result.swar_risk, r.kind == "silently_wrong") for r in population(as_run)]
        lines += [
            "", "## As it ran, before the detector was fixed", "",
            "These runs were scored and labelled while they ran, with the detector and the labelling rules as "
            "they were then. Both were corrected afterwards (ADR-012, change log), and the tables above are "
            "the same stored runs **rescored and relabelled**. Because the defects were seen in these very "
            "runs, the corrected numbers are not an independent test.",
            "",
        ] + confusion_table(before) + ["", "Cases whose risk or label changed:", ""]  # fmt: skip
        changed = [
            (a, b)
            for a, b in zip(as_run, records, strict=True)
            if abs(a.result.swar_risk - b.result.swar_risk) > 1e-9 or a.kind != b.kind
        ]
        lines += [
            f"- {a.case_id}: risk {a.result.swar_risk:.2f} -> {b.result.swar_risk:.2f}, "
            f"label {a.kind} -> {b.kind} ({b.reason[:60]})"
            for a, b in changed
        ] or ["- none"]

    lines += ["", "## Static signals and dynamic signals, separately", "",
              "Static = derivable from the SQL and the question alone (rule violations, an unstated period). "
              "Dynamic = what only a run shows (verifier, trajectory, figures). The rules, the traps and the "
              "metrics share an author and a catalogue, so the static numbers are partly circular.", ""]  # fmt: skip
    for label, keep, drop in (
        ("static only", STATIC_SIGNALS, None),
        ("dynamic only", None, STATIC_SIGNALS),
    ):
        sub = [(risk_with(r.result, cfg, keep, drop), r.kind == "silently_wrong") for r in pop]
        lines += [f"**{label}**", ""] + confusion_table(sub) + [""]

    blind = [r for r in wrong if not (RULE_SIGNALS & {s.name for s in r.result.swar_signals})]
    lines += ["## Errors the rules cannot see", "",
              f"{len(blind)} of {len(wrong)} silently wrong answers raised no rule signal "
              "(a wrong result with no violation). The static rules do not see them, by construction; "
              "whether the dynamic signals did is the risk shown on each line.", ""]  # fmt: skip
    lines += [
        f"- {r.case_id} ({r.variant}): {r.reason}; risk {r.result.swar_risk:.2f}" for r in blind
    ] or ["- none"]

    lines += ["", "## Early warning: risk from the first k events", "",
              f"Replay of the stored events at the action threshold ({cfg.action_threshold:.2f}). "
              "C2 is where the early warning acts before the verifier is spent; the final row adds the verifier.", ""]  # fmt: skip
    lines += [
        "| first k events | silently wrong flagged | correct flagged (false alarms) |",
        "|---|---|---|",
    ]

    def writer_end(r: Record) -> int:
        """The events up to the end of the writer's last turn: where checkpoint C2 looks."""
        return (
            max((i for i, e in enumerate(r.result.events) if e.role == "sql_writer"), default=-1)
            + 1
        )

    def size(r: Record, k: int | str) -> int:
        return len(r.result.events) if k == "all" else writer_end(r) if k == "C2" else int(k)

    for k in (3, 6, 9, "C2", "all"):

        def flagged(r: Record, k: int | str = k) -> bool:
            return risk_at(r.result, size(r, k), ontology, cfg).risk >= cfg.action_threshold

        tp = sum(1 for r in wrong if flagged(r))
        fp = sum(1 for r in pop if r.kind == "correct" and flagged(r))
        n_ok = sum(1 for r in pop if r.kind == "correct")
        label = {"C2": "C2 (end of the writer)", "all": "all (final)"}.get(k, str(k))
        lines.append(f"| {label} | {tp}/{len(wrong)} | {fp}/{n_ok} |")

    acted = [r for r in records if r.result.early_warning]
    lines += ["", f"The early warning acted in {len(acted)} of {len(records)} runs "
              "(0 is expected in `annotate` mode).", ""]  # fmt: skip

    usage = [r.result.usage for r in records]
    if usage:
        lines += [
            "## Cost",
            "",
            f"Mean per run: {sum(u.ms for u in usage) / len(usage) / 1000:.1f} s, "
            f"{sum(u.llm_calls for u in usage) / len(usage):.1f} model calls, "
            f"{sum(u.input_tokens for u in usage) / len(usage):.0f} input and "
            f"{sum(u.output_tokens for u in usage) / len(usage):.0f} output tokens.",
            "",
        ]
    lines += [
        "## Limits of this measurement",
        "",
        "- **Small sample.** The intervals say how little a handful of cases can tell. Do not quote a point value.",
        "- **Circularity.** Defect rules, traps, metrics and the catalogue come from one author; "
        "recall on rule signals holds by construction. The verifier's briefing includes the rule warnings.",
        (
            f"- **The golden set has one reviewer, its author** (`review: approved` since "
            f"{APPROVED_ON}). A label is only as good as the case behind it."
            if all_approved(load_golden())
            else "- **The golden set is `review: draft`.** A label is only as good as the case behind it."
        ),
        "- **Strict labels.** Columns are compared by position (ADR-011); extra columns are tolerated. "
        "A correct answer phrased as a different query shape can be mislabelled wrong.",
        "- **Free-tier models change from day to day** (ADR-010): a result for `omniroute-free` describes "
        "that model on that day.",
        "",
    ]
    return "\n".join(lines)


# ============================================================================ cli
def _stamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%MZ")


def write_report(text: str, name: str) -> Path:
    REPORTS.mkdir(parents=True, exist_ok=True)
    path = REPORTS / f"swar-{datetime.now(UTC):%Y-%m-%d}-{name}.md"
    path.write_text(text, encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    live = sub.add_parser("live", help="run the fixed subset against a real profile")
    live.add_argument("--profile", required=True)
    live.add_argument("--backend", choices=("local", "mcp"), default="local")
    live.add_argument("--action", choices=("annotate", "gate", "off"), default="annotate")
    live.add_argument("--escalation-profile")
    live.add_argument("--ids", help="comma-separated case ids (default: the whole subset)")
    live.add_argument("--out", type=Path, help="JSONL to write or resume")
    live.add_argument("--name", help="report name (default: the profile)")
    sub.add_parser("baseline", help="no LLM: run expected and naive SQL through the real agent")
    rep = sub.add_parser("report", help="build a report from stored results")
    rep.add_argument("files", nargs="+", type=Path)
    rep.add_argument("--name", required=True)
    rep.add_argument("--relabel", action="store_true",
                     help="label the stored runs again with the current rules (needs the database)")  # fmt: skip
    rep.add_argument("--rescore", action="store_true",
                     help="score the stored runs again with the current detector and show both")  # fmt: skip
    args = parser.parse_args(argv)

    if args.command == "baseline":
        records = asyncio.run(run_oracle(load_golden()))
        RESULTS.mkdir(parents=True, exist_ok=True)
        out = RESULTS / f"swar-baseline-{_stamp()}.jsonl"
        out.write_text("".join(r.model_dump_json() + "\n" for r in records), encoding="utf-8")
        notes = [
            "Deterministic baseline, no LLM: for every answer case the real agent runs the reference SQL "
            "(`oracle-expected`) and, for traps, the naive SQL (`oracle-naive`); the verifier agrees. "
            "It measures the **static** signals only and is **not** the accuracy of the detector.",
        ]
        print(f"wrote {write_report(build_report(records, 'baseline', notes=notes), 'baseline')}")
        return 0
    if args.command == "report":
        records = [r for f in args.files for r in load_records(f)]
        as_run = records if (args.rescore or args.relabel) else None
        if args.relabel:
            records = relabel(records)
        if args.rescore:
            records = rescore(records)
        print(f"wrote {write_report(build_report(records, args.name, as_run=as_run), args.name)}")
        return 0

    cases = load_subset()
    if args.ids:
        wanted = args.ids.split(",")
        cases = [c for c in cases if c.id in wanted]
    RESULTS.mkdir(parents=True, exist_ok=True)
    out = args.out or RESULTS / f"swar-{args.profile}-{args.action}-{_stamp()}.jsonl"
    records = asyncio.run(
        run_golden(args.profile, cases, out, backend=args.backend, action=args.action,
                   escalation_profile=args.escalation_profile)
    )  # fmt: skip
    name = args.name or args.profile
    print(f"results: {out}\nwrote {write_report(build_report(records, name), name)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
