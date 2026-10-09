"""Build the evaluation report from stored results (ADR-013). Every number comes from a file.

    uv run python -m evals.report                      # the latest run id found in evals/results/
    uv run python -m evals.report --run-id 20261009 --docs
    uv run python -m evals.report --run-id 20261009 --relabel --rescore

Writes ``evals/reports/eval-<run-id>.md`` and PNG charts in ``evals/reports/eval-<run-id>/`` (the
charts need the ``eval`` dependency group and are skipped without it). With ``--docs`` the same
tables also go to ``docs/eval-report.md`` together with the reading notes.
"""

from __future__ import annotations

import argparse
import itertools
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from evals.attacks import AttackOutcome, load_outcomes
from evals.golden import load_golden
from evals.metrics import TAU, THRESHOLDS, compare_arms, summarize
from evals.runner import ARMS, RESULTS, Record, load_records, models_used
from legacybridge.llm.profiles import CONFIG_PATH

ROOT = Path(__file__).resolve().parent
REPORTS = ROOT / "reports"
DOCS = ROOT.parent / "docs" / "eval-report.md"
NOTES = ROOT / "notes"  # hand-written reading of one run: <run-id>.md
REFERENCE_ARM = "full"
ABLATION_PROFILE = "local-qwen"
GOLDEN = {c.id: c for c in load_golden()}


# ============================================================================ loading
@dataclass
class Run:
    run_id: str
    arms: dict[tuple[str, str], list[Record]] = field(default_factory=dict)  # (profile, arm)
    attacks: list[AttackOutcome] | None = None

    @property
    def profiles(self) -> list[str]:
        return sorted({p for p, _ in self.arms})

    def records(self, profile: str, arm: str = REFERENCE_ARM) -> list[Record]:
        return self.arms.get((profile, arm), [])

    def all_records(self) -> list[Record]:
        return [r for rs in self.arms.values() for r in rs]


def _split_name(stem: str, run_id: str) -> tuple[str, str] | None:
    rest = stem.removeprefix(f"eval-{run_id}-")
    for arm in ARMS:
        if rest.endswith(f"-{arm}"):
            return rest.removesuffix(f"-{arm}"), arm
    return None


def latest_run_id(results: Path = RESULTS) -> str | None:
    ids = sorted({p.stem.split("-")[1] for p in results.glob("eval-*-*.jsonl")})
    return ids[-1] if ids else None


def load_run(run_id: str, results: Path = RESULTS) -> Run:
    run = Run(run_id)
    for path in sorted(results.glob(f"eval-{run_id}-*.jsonl")):
        key = _split_name(path.stem, run_id)
        if key:
            run.arms[key] = load_records(path)
    attacks = results / f"attacks-{run_id}.json"
    if attacks.exists():
        run.attacks = load_outcomes(attacks)
    return run


def prices(profile: str, path: Path = CONFIG_PATH) -> tuple[float, float]:
    """Per-million-token prices from config/models.yaml, read without needing the profile's env."""
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))["profiles"].get(profile, {})
    return float(raw.get("price_in_per_mtok", 0)), float(raw.get("price_out_per_mtok", 0))


# ============================================================================ formatting
def fmt(p: dict[str, Any] | None, digits: int = 2) -> str:
    if not p or p["p"] is None:
        return "n/a"
    return f"{p['p']:.{digits}f} ({p['k']}/{p['n']}; CI {p['lo']:.2f}-{p['hi']:.2f})"


def short(p: dict[str, Any] | None) -> str:
    return "n/a" if not p or p["p"] is None else f"{p['p']:.2f} ({p['k']}/{p['n']})"


def secs(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.1f} s"


def table(header: list[str], rows: list[list[str]]) -> list[str]:
    out = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    out += ["| " + " | ".join(r) + " |" for r in rows]
    return out + [""]


# ============================================================================ sections
def provenance(run: Run) -> list[str]:
    records = run.all_records()
    commits = sorted({r.commit for r in records if r.commit})
    dirty = any(r.dirty for r in records)
    lines = [
        f"- Run id **{run.run_id}**; generated {datetime.now(UTC):%Y-%m-%d %H:%M UTC}.",
        f"- Commits the runs were made at: {', '.join(commits) or 'unknown'}"
        + (
            " (**the working tree had uncommitted changes in at least one run**)." if dirty else "."
        ),
        f"- Cases: **{len(GOLDEN)}** golden (`review: draft`, not yet validated by a person) and "
        f"**{len(run.attacks) if run.attacks else 0}** attacks. One run per arm: run-to-run noise is "
        "not measured and the intervals do not include it.",
    ]
    for profile in run.profiles:
        served = models_used(run.records(profile)) or ["unknown"]
        lines.append(f"- `{profile}` was answered by: {', '.join(served)}.")
    return lines + [""]


def headline(run: Run, summaries: dict[tuple[str, str], dict[str, Any]]) -> list[str]:
    cols = run.profiles
    rows = [
        ("Execution accuracy", lambda s: fmt(s["execution_accuracy"])),
        ("Coverage (answerable questions answered)", lambda s: fmt(s["coverage"])),
        ("Handled correctly (all 80)", lambda s: fmt(s["handled_correctly"])),
        (
            f"**SWAR rate** (confidence >= {TAU}, delivered answers)",
            lambda s: fmt(s["swar"]["swar"]),
        ),
        ("Silently wrong rate (any confidence)", lambda s: fmt(s["swar"]["silently_wrong"])),
        ("Abstention quality (ambiguous + out of scope)", lambda s: fmt(s["abstention_quality"])),
        ("Write correctness", lambda s: fmt(s["write_correctness"])),
        ("Defect compliance (traps)", lambda s: fmt(s["defect_compliance"]["overall"])),
        ("Injection traps (agent level)", lambda s: fmt(s["agent_injection"]["result"])),
        (
            "Latency p50 / p95",
            lambda s: f"{secs(s['latency']['p50'])} / {secs(s['latency']['p95'])}",
        ),
        ("Tokens per question (in / out)", lambda s: _tokens(s["usage"])),
        ("Cost per question", lambda s: _cost(s["usage"])),
    ]
    body = [[name] + [fn(summaries[(p, REFERENCE_ARM)]) if (p, REFERENCE_ARM) in summaries else "not run"
                      for p in cols] for name, fn in rows]  # fmt: skip
    return table(["Metric (full arm)"] + cols, body)


def _tokens(u: dict[str, Any]) -> str:
    return "n/a" if not u["n"] else f"{u['input_tokens']:.0f} / {u['output_tokens']:.0f}"


def _cost(u: dict[str, Any]) -> str:
    if not u["n"]:
        return "n/a"
    note = "" if u["priced"] else " (unpriced: local or free tier)"
    return f"${u['usd_per_question']:.4f}{note}"


def security_section(run: Run) -> list[str]:
    if run.attacks is None:
        return ["_The attacks were not run for this run id._", ""]
    s = summarize([], GOLDEN, attacks=run.attacks)["security"]
    lines = [
        f"**Security pass rate: {fmt(s['pass_rate'])}** across {s['categories']} attack categories "
        f"(an attack passes when the tool blocks it or neutralizes it). Defense in depth: of the "
        f"attacks that also have a database-level check, {fmt(s['database_refuses'])} are refused by "
        "`lb_reader` with no guard at all.",
        "",
    ]
    if s["failed"]:
        lines += ["Attacks that got through:", ""] + [
            f"- {f['id']}: {f['detail']}" for f in s["failed"]
        ]
        lines.append("")
    inj = {p: summarize(run.records(p), GOLDEN)["agent_injection"] for p in run.profiles}
    lines.append(
        "Prompt injection that only the agent can fall for (it needs a model, so it is a golden "
        "case, not a tool attack): "
        + "; ".join(f"`{p}` {short(v['result'])}" for p, v in inj.items())
        + f" ({', '.join(next(iter(inj.values()))['ids'] if inj else [])})."
    )
    return lines + [""]


def category_section(run: Run, summaries: dict) -> list[str]:
    cats = sorted({c for s in summaries.values() for c in s["by_category"]})
    rows = []
    for c in cats:
        row = [c]
        for p in run.profiles:
            s = summaries.get((p, REFERENCE_ARM))
            cell = s["by_category"].get(c) if s else None
            extra = (
                f", {cell['unverifiable']} unverifiable" if cell and cell["unverifiable"] else ""
            )
            row.append(short(cell) + extra if cell else "not run")
        rows.append(row)
    return table(["Category (handled correctly)"] + run.profiles, rows)


def defect_section(run: Run, summaries: dict) -> list[str]:
    defects = sorted({d for s in summaries.values() for d in s["defect_compliance"]["per_defect"]})
    rows = [[d] + [short(summaries[(p, REFERENCE_ARM)]["defect_compliance"]["per_defect"].get(d))
                   if (p, REFERENCE_ARM) in summaries else "not run" for p in run.profiles]
            for d in defects]  # fmt: skip
    rows.append(["_rule-clean answers (circular)_"] + [
        short(summaries[(p, REFERENCE_ARM)]["defect_compliance"]["rule_clean_circular"])
        if (p, REFERENCE_ARM) in summaries else "not run" for p in run.profiles
    ])  # fmt: skip
    return [
        'A case counts for every defect it lists. "Compliance" means the result is the expected one. '
        "The last row counts answers whose SQL raises no rule violation; the rules and the traps "
        "share an author, so that row is **circular** and is shown only as a cross-check.",
        "",
    ] + table(["Defect"] + run.profiles, rows)


def detector_section(run: Run, summaries: dict) -> list[str]:
    lines = [
        "Delivered answers with a known label; a risk at or above the threshold is a flag.",
        "",
    ]
    rows = []
    for p in run.profiles:
        d = summaries.get((p, REFERENCE_ARM), {}).get("detector")
        if not d:
            continue
        for t in THRESHOLDS:
            c = d[f"{t:.1f}"]
            rows.append([p, f">= {t:.1f}", f"{c['tp']}/{c['fp']}/{c['fn']}/{c['tn']}",
                         fmt(c["precision"]), fmt(c["recall"])])  # fmt: skip
        rows.append([p, "positives", f"{d['positives']} of {d['population']}", "", ""])
    return lines + table(["Profile", "Threshold", "TP/FP/FN/TN", "Precision", "Recall"], rows)


def unfinished(records: list[Record]) -> int:
    """Runs where the writer's loop did not finish (the 10-step limit or no finish call)."""
    return sum(1 for r in records if r.result.abstain_reason == "no_answer")


def step_limit_line(run: Run, arms: list[str]) -> list[str]:
    counts = ", ".join(
        f"{arm} {unfinished(run.records(ABLATION_PROFILE, arm))}/"
        f"{len(run.records(ABLATION_PROFILE, arm))}"
        for arm in arms
    )
    return [
        f"Runs that ended because the writer did not finish within its step limit (`no_answer`): "
        f"{counts}. Part of what an arm loses in coverage is steps, not wrong answers.",
        "",
    ]


def confidence_cap_line(run: Run, arms: list[str]) -> list[str]:
    if "no-verify" not in arms:
        return []
    delivered = [
        r.result.confidence
        for r in run.records(ABLATION_PROFILE, "no-verify")
        if r.result.status in ("answered", "pending_approval")
    ]
    top = max(delivered, default=0.0)
    if top >= TAU:
        return []
    return [
        f"Without the verifier nobody checked the answer, so its confidence starts at 0.55: the "
        f"highest confidence `no-verify` delivered is {top:.2f}, below tau = {TAU}. Its SWAR rate "
        "is 0 **by construction** and says nothing about its wrong answers; compare *Silently "
        "wrong*, which has no confidence condition.",
        "",
    ]


def _rows_key(record: Record) -> tuple | None:
    evidence = record.result.evidence
    if evidence is None:
        return None
    return tuple(sorted(tuple(str(c) for c in row) for row in evidence.rows))


def agreement_section(run: Run) -> list[str]:
    """Wrong answers that two different models give in the same way point at the question, the
    golden answer or the shared semantic layer rather than at luck."""
    pairs = list(itertools.combinations([p for p in run.profiles if run.records(p)], 2))
    if not pairs:
        return ["_Only one profile was run, so there is nothing to compare._", ""]
    lines = [
        "Query answers (not writes) that both profiles delivered wrong, and how many of those "
        "have the identical result set. Two independent models agreeing with each other and not "
        "with the golden answer is a lead on the question, the golden answer or the shared "
        "semantic layer, not proof of which of them is right.",
        "",
    ]
    rows = []
    for a, b in pairs:
        first = {r.case_id: r for r in run.records(a) if r.kind == "silently_wrong"}
        second = {r.case_id: r for r in run.records(b) if r.kind == "silently_wrong"}
        both = sorted(c for c in first if c in second and first[c].category != "write")
        same = [
            c for c in both if (k := _rows_key(first[c])) is not None and k == _rows_key(second[c])
        ]
        rows.append([f"{a} / {b}", str(len(both)), ", ".join(both) or "-",
                     f"{len(same)}: " + (", ".join(same) or "-")])  # fmt: skip
    return lines + table(["Pair", "Wrong in both", "Cases", "Identical result set"], rows)


def ablation_section(run: Run, summaries: dict) -> list[str]:
    present = [a for a in ARMS if (ABLATION_PROFILE, a) in summaries]
    if len(present) < 2:
        return [f"_No ablation arms were run for `{ABLATION_PROFILE}` in this run id._", ""]
    rows = []
    for arm in present:
        s = summaries[(ABLATION_PROFILE, arm)]
        rows.append([arm, fmt(s["handled_correctly"]), fmt(s["execution_accuracy"]), fmt(s["coverage"]),
                     fmt(s["swar"]["swar"]), fmt(s["swar"]["silently_wrong"]),
                     fmt(s["abstention_quality"]), secs(s["latency"]["p50"]),
                     f"{s['usage']['llm_calls']:.1f}"])  # fmt: skip
    lines = table(["Arm", "Handled correctly", "Accuracy", "Coverage", "SWAR rate", "Silently wrong",
                   "Abstention", "p50", "Calls/q"], rows)  # fmt: skip
    lines += step_limit_line(run, present)
    lines += confidence_cap_line(run, present)
    lines += [
        "The `no-verify` arm removes the independent verifier; `no-semantic` removes the ontology, "
        "the business summary in the prompts, the ontology tool and the rule warnings. **In the "
        "`no-semantic` arm the detector also loses its semantic signals**, so its detector figures "
        "are not comparable with the other arms.",
        "",
        "Paired by case on *handled correctly* (exact McNemar on the discordant pairs; two "
        "comparisons specified in advance):",
        "",
    ]
    ref = run.records(ABLATION_PROFILE, REFERENCE_ARM)
    cmp_rows = []
    for arm in present:
        if arm == REFERENCE_ARM:
            continue
        c = compare_arms(ref, run.records(ABLATION_PROFILE, arm))
        verdict = _verdict(c)
        cmp_rows.append([f"{REFERENCE_ARM} vs {arm}", str(c["paired"]),
                         f"{c['only_reference']} / {c['only_other']}", f"{c['p_value']:.4f}", verdict])  # fmt: skip
    lines += table(
        ["Comparison", "Paired", "Only reference / only other", "p", "Reading"], cmp_rows
    )
    for arm in present:
        if arm == REFERENCE_ARM:
            continue
        c = compare_arms(ref, run.records(ABLATION_PROFILE, arm))
        if c["reference_only_ids"]:
            lines.append(
                f"- Correct only with `{REFERENCE_ARM}` (vs `{arm}`): {', '.join(c['reference_only_ids'])}"
            )
        if c["other_only_ids"]:
            lines.append(f"- Correct only with `{arm}`: {', '.join(c['other_only_ids'])}")
    return lines + [""]


def _verdict(c: dict[str, Any], alpha: float = 0.05, other: str = "other arm") -> str:
    if c["p_value"] >= alpha:
        return f"not distinguishable at {alpha:g}"
    better = "reference" if c["only_reference"] > c["only_other"] else other
    return f"distinguishable (p < {alpha:g}); the {better} handled more cases correctly"


# Fixed in ADR-014 before the Bedrock run: the reference model first, and a significance of 0.05
# split over the two comparisons.
MODEL_COMPARISONS = [("bedrock-claude", "local-qwen"), ("bedrock-claude", "omniroute-free")]
MODEL_ALPHA = 0.05 / len(MODEL_COMPARISONS)


def model_comparison_section(run: Run) -> list[str]:
    rows, notes = [], []
    for reference, other in MODEL_COMPARISONS:
        a, b = run.records(reference), run.records(other)
        if not a or not b:
            continue
        c = compare_arms(a, b)
        rows.append([f"{reference} vs {other}", str(c["paired"]), f"{c['only_reference']} / {c['only_other']}",
                     f"{c['p_value']:.4f}", _verdict(c, MODEL_ALPHA, other)])  # fmt: skip
        if c["reference_only_ids"]:
            notes.append(
                f"- Correct only with `{reference}` (vs `{other}`): {', '.join(c['reference_only_ids'])}"
            )
        if c["other_only_ids"]:
            notes.append(
                f"- Correct only with `{other}` (vs `{reference}`): {', '.join(c['other_only_ids'])}"
            )
    if not rows:
        return []
    return (
        [
            "Paired by case on *handled correctly* (exact McNemar on the discordant pairs). Two comparisons "
            f"were specified in advance (ADR-014), so each is read at {MODEL_ALPHA:g}, not 0.05. These are "
            "different models on different days: the test says whether the two sets of correct cases "
            "differ, not why.",
            "",
        ]
        + table(["Comparison", "Paired", "Only reference / only other", "p", "Reading"], rows)
        + notes
        + [""]
    )


def appendix(run: Run) -> list[str]:
    lines = ["Every run that was not labelled correct, so nothing is hidden behind a rate.", ""]
    for (profile, arm), records in sorted(run.arms.items()):
        rows = [[r.case_id, r.result.status, r.kind, f"{r.result.confidence:.2f}",
                 f"{r.result.swar_risk:.2f}", r.reason[:70]]
                for r in records if r.kind != "correct"]  # fmt: skip
        if rows:
            lines += [f"**{profile} / {arm}**", ""] + table(
                ["Case", "Status", "Label", "Confidence", "SWAR risk", "Why"], rows
            )
    return lines


READING_NOTES = [
    "- **Small samples.** 80 cases split into six categories: a category of 5 cannot be told from "
    "chance. The intervals are 95% Wilson and say how little a proportion pins down.",
    "- **The golden set is a draft.** `review: draft` on all 80 cases: no person has validated the "
    "expected results, so accuracy inherits that uncertainty.",
    "- **Different models.** The profiles run different models on different days; they are shown side "
    "by side and not tested against each other. A free-tier model describes that model on that day "
    "(ADR-010).",
    "- **Labels.** Strict by design (columns by position, ADR-011) with three presentation "
    "tolerances, frozen in ADR-013. A correct answer in an unusual shape can still be marked wrong.",
    "- **Circularity.** Rules, traps and metrics share an author; compliance by result is the "
    "primary figure and the rule-based one only a cross-check.",
    "- **What the CI gate does not measure.** The gate runs a deterministic oracle, not a model; it "
    "protects the guard, tools, labels and detector, not the quality of any model.",
    "- **Cost.** Local and free-tier profiles cost nothing here. A priced profile uses the per-million-"
    "token prices in `config/models.yaml`, copied from the provider's price list (never estimated); "
    "tokens are what the model reported, so retries that failed before answering are not counted.",
]


# ============================================================================ corrections
def corrections(as_run: list[Record], corrected: list[Record]) -> list[str]:
    changed = [
        (a, b) for a, b in zip(as_run, corrected, strict=True)
        if a.kind != b.kind or abs(a.result.swar_risk - b.result.swar_risk) > 1e-9
    ]  # fmt: skip
    return [
        f"- {a.case_id} ({a.profile}/{a.arm}): label {a.kind} -> {b.kind}, "
        f"risk {a.result.swar_risk:.2f} -> {b.result.swar_risk:.2f}"
        for a, b in changed
    ] or ["- none"]


# ============================================================================ charts
def make_charts(run: Run, summaries: dict, out: Path) -> list[Path]:
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return []
    out.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    def err(p: dict[str, Any]) -> tuple[float, float]:
        value = p["p"] or 0.0
        return value - p["lo"], p["hi"] - value

    cats = sorted({c for s in summaries.values() for c in s["by_category"]})
    fig, ax = plt.subplots(figsize=(9, 4.5))
    width = 0.8 / max(1, len(run.profiles))
    for i, profile in enumerate(run.profiles):
        s = summaries.get((profile, REFERENCE_ARM))
        if not s:
            continue
        vals = [s["by_category"].get(c) for c in cats]
        xs = [j + i * width for j in range(len(cats))]
        ys = [v["p"] if v and v["p"] is not None else 0 for v in vals]
        yerr = [[err(v)[0] if v and v["p"] is not None else 0 for v in vals],
                [err(v)[1] if v and v["p"] is not None else 0 for v in vals]]  # fmt: skip
        ax.bar(xs, ys, width, yerr=yerr, capsize=3, label=profile)
    ax.set_xticks(
        [j + width * (len(run.profiles) - 1) / 2 for j in range(len(cats))], cats, rotation=20
    )
    ax.set_ylim(0, 1.2)
    ax.set_ylabel("handled correctly")
    ax.set_title("Handled correctly by category (95% Wilson interval)")
    ax.legend(loc="upper center", ncol=max(1, len(run.profiles)), frameon=False)
    fig.tight_layout()
    path = out / "accuracy_by_category.png"
    fig.savefig(path, dpi=130)
    plt.close(fig)
    written.append(path)

    present = [a for a in ARMS if (ABLATION_PROFILE, a) in summaries]
    if len(present) >= 2:
        fig, axes = plt.subplots(1, 2, figsize=(9, 4))
        for ax, key, title in ((axes[0], "handled_correctly", "Handled correctly"),
                               (axes[1], "silently_wrong", "Silently wrong rate")):  # fmt: skip
            vals = [summaries[(ABLATION_PROFILE, a)]["swar"]["silently_wrong"] if key == "silently_wrong"
                    else summaries[(ABLATION_PROFILE, a)][key] for a in present]  # fmt: skip
            ax.bar(present, [v["p"] or 0 for v in vals],
                   yerr=[[err(v)[0] for v in vals], [err(v)[1] for v in vals]], capsize=4)  # fmt: skip
            ax.set_ylim(0, 1.05)
            ax.set_title(title)
        fig.suptitle(f"Ablation on {ABLATION_PROFILE} (95% Wilson interval)")
        fig.tight_layout()
        path = out / "ablation.png"
        fig.savefig(path, dpi=130)
        plt.close(fig)
        written.append(path)

    fig, axes = plt.subplots(1, max(1, len(run.profiles)), figsize=(4.5 * max(1, len(run.profiles)), 4),
                             squeeze=False)  # fmt: skip
    for ax, profile in zip(axes[0], run.profiles, strict=False):
        pop = [r for r in run.records(profile) if r.result.status in ("answered", "pending_approval")
               and r.kind in ("correct", "silently_wrong")]  # fmt: skip
        for x, kind in enumerate(("correct", "silently_wrong")):
            ys = [r.result.swar_risk for r in pop if r.kind == kind]
            ax.scatter([x + (i % 7 - 3) * 0.04 for i in range(len(ys))], ys, alpha=0.7)
        ax.axhline(0.7, ls="--", lw=1, color="grey")
        ax.set_xticks([0, 1], ["correct", "silently wrong"])
        ax.set_ylim(-0.05, 1.05)
        ax.set_title(profile)
    axes[0][0].set_ylabel("swar_risk (dashed: action threshold)")
    fig.tight_layout()
    path = out / "risk_distribution.png"
    fig.savefig(path, dpi=130)
    plt.close(fig)
    written.append(path)

    fig, ax = plt.subplots(figsize=(6, 4))
    names = [p for p in run.profiles if (p, REFERENCE_ARM) in summaries]
    p50 = [summaries[(p, REFERENCE_ARM)]["latency"]["p50"] or 0 for p in names]
    p95 = [summaries[(p, REFERENCE_ARM)]["latency"]["p95"] or 0 for p in names]
    xs = range(len(names))
    ax.bar([x - 0.2 for x in xs], p50, 0.4, label="p50")
    ax.bar([x + 0.2 for x in xs], p95, 0.4, label="p95")
    ax.set_xticks(list(xs), names)
    ax.set_ylabel("seconds per question")
    ax.set_title("Latency")
    ax.legend()
    fig.tight_layout()
    path = out / "latency.png"
    fig.savefig(path, dpi=130)
    plt.close(fig)
    written.append(path)
    return written


# ============================================================================ the report
def build(run: Run, *, charts: list[Path] | None = None, notes: list[str] | None = None,
          corrected: list[str] | None = None, commentary: str | None = None) -> str:  # fmt: skip
    summaries = {
        key: summarize(records, GOLDEN, prices=prices(key[0]), attacks=run.attacks)
        for key, records in run.arms.items() if records
    }  # fmt: skip
    lines = [f"# Evaluation report {run.run_id}", ""] + provenance(run)
    lines += [f"> {n}" for n in (notes or [])] + ([""] if notes else [])
    lines += ["## Headline", ""] + headline(run, summaries)
    lines += ["## Security", ""] + security_section(run)
    lines += ["## By category", ""] + category_section(run, summaries)
    lines += ["## Defect compliance", ""] + defect_section(run, summaries)
    lines += ["## Where the profiles fail together", ""] + agreement_section(run)
    if comparison := model_comparison_section(run):
        lines += ["## Model comparison", ""] + comparison
    lines += ["## The SWAR detector", ""] + detector_section(run, summaries)
    lines += ["## Ablation: what the verifier and the semantic layer are worth", ""]
    lines += ablation_section(run, summaries)
    if corrected is not None:
        lines += ["## Corrected after the run", "",
                  "Labels or the detector were corrected after seeing these results (ADR-013). The "
                  "tables above are the corrected figures; these are the cases that changed.", ""]  # fmt: skip
        lines += corrected + [""]
    if charts:
        lines += (
            ["## Charts", ""] + [f"![{p.stem}]({p.parent.name}/{p.name})" for p in charts] + [""]
        )
    lines += ["## Appendix: runs that were not correct", ""] + appendix(run)
    if commentary:
        lines += ["## Reading the failures", "",
                  "_Written by hand after reading the stored results. The numbers elsewhere in this "
                  "report are generated; these paragraphs are interpretation and can be wrong._", "",
                  commentary.strip(), ""]  # fmt: skip
    lines += ["## How to read this", ""] + READING_NOTES + [""]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--run-id")
    parser.add_argument("--docs", action="store_true", help="also write docs/eval-report.md")
    parser.add_argument(
        "--relabel", action="store_true", help="label stored runs again (needs the db)"
    )
    parser.add_argument(
        "--rescore", action="store_true", help="score stored runs with today's detector"
    )
    parser.add_argument("--no-charts", action="store_true")
    args = parser.parse_args(argv)

    run_id = args.run_id or latest_run_id()
    if not run_id:
        raise SystemExit("no results found in evals/results/; run python -m evals.run_evals first")
    run = load_run(run_id)
    if not run.arms:
        raise SystemExit(f"no golden results for run id {run_id}")

    corrected = None
    if args.relabel or args.rescore:
        from evals.swar_eval import relabel, rescore

        before = [r for key in sorted(run.arms) for r in run.arms[key]]
        for key, records in run.arms.items():
            fixed = relabel(records) if args.relabel else records
            run.arms[key] = rescore(fixed) if args.rescore else fixed
        after = [r for key in sorted(run.arms) for r in run.arms[key]]
        corrected = corrections(before, after)

    out_dir = REPORTS / f"eval-{run_id}"
    summaries = {k: summarize(v, GOLDEN) for k, v in run.arms.items() if v}
    charts = [] if args.no_charts else make_charts(run, summaries, out_dir)
    notes = (
        []
        if charts or args.no_charts
        else ["Charts were skipped: matplotlib is not installed (`uv sync --group eval`)."]
    )
    note_file = NOTES / f"{run_id}.md"
    commentary = note_file.read_text(encoding="utf-8") if note_file.exists() else None
    text = build(run, charts=charts, notes=notes, corrected=corrected, commentary=commentary)
    path = REPORTS / f"eval-{run_id}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    print(f"wrote {path}" + (f" and {len(charts)} charts" if charts else ""))
    if args.docs:
        DOCS.parent.mkdir(parents=True, exist_ok=True)
        DOCS.write_text(text.replace("(eval-", "(../evals/reports/eval-"), encoding="utf-8")
        print(f"wrote {DOCS}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
