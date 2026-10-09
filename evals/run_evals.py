"""Run the evaluation of ADR-013: the 45 attacks and the 80 golden cases, per profile and arm.

    uv run python -m evals.run_evals --profiles local-qwen,omniroute-free --set golden,attacks
    uv run python -m evals.run_evals --profiles local-qwen --arm no-verify,no-semantic --set golden

Results are JSONL files in ``evals/results/`` named after the run id, one line per question, and a
stopped run resumes where it left off (same ``--run-id``). ``python -m evals.report`` turns them
into the report. Questions run one at a time: they share the database, and a free tier rate-limits.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from evals.attacks import AttackOutcome, load_attacks, run_attack, save_outcomes
from evals.golden import GoldenCase, load_golden
from evals.runner import ARMS, RESULTS, fingerprint, models_used, run_golden
from legacybridge.agent.factory import build_llm
from legacybridge.db import connect
from legacybridge.llm.base import LLMError, Message
from legacybridge.llm.profiles import ProfileError
from legacybridge.mcp_server.tools import LegacyTools


def default_run_id() -> str:
    return datetime.now(UTC).strftime("%Y%m%d")


def golden_path(run_id: str, profile: str, arm: str, results: Path = RESULTS) -> Path:
    return results / f"eval-{run_id}-{profile}-{arm}.jsonl"


def attacks_path(run_id: str, results: Path = RESULTS) -> Path:
    return results / f"attacks-{run_id}.json"


def run_attacks(log: Callable[[str], None] = print) -> list[AttackOutcome]:
    """Every attack against the real tools. None may change business data or queue anything."""
    tools = LegacyTools()
    with connect("admin") as admin:
        before = (
            fingerprint(admin),
            admin.execute("SELECT count(*) FROM lb_ops.write_requests").fetchone()[0],
        )
        admin.rollback()
    outcomes = [run_attack(tools, a) for a in load_attacks()]
    with connect("admin") as admin:
        after = (
            fingerprint(admin),
            admin.execute("SELECT count(*) FROM lb_ops.write_requests").fetchone()[0],
        )
        admin.rollback()
    if before != after:
        raise RuntimeError(
            "an attack changed business data or queued a request: this must never happen"
        )
    failed = [o for o in outcomes if not o.passed]
    log(f"attacks: {len(outcomes) - len(failed)}/{len(outcomes)} blocked or neutralized")
    for o in failed:
        log(f"  FAILED {o.attack_id}: {o.detail}")
    return outcomes


async def probe(profile: str, log: Callable[[str], None]) -> None:
    """Fail in seconds, not after an hour, if a profile cannot answer (a free model may be gone)."""
    client = build_llm(profile)
    try:
        reply = await client.chat([Message.user("Reply with the single word: ok")], max_tokens=64)
    except LLMError as error:
        raise SystemExit(
            f"profile {profile} cannot answer right now, nothing was run: {error}"
        ) from error
    log(f"probe {profile}: served by {reply.model}")


def pick_cases(ids: str | None, limit: int | None) -> list[GoldenCase]:
    cases = load_golden()
    if ids:
        wanted = set(ids.split(","))
        unknown = wanted - {c.id for c in cases}
        if unknown:
            raise SystemExit(f"unknown case ids: {', '.join(sorted(unknown))}")
        cases = [c for c in cases if c.id in wanted]
    return cases[:limit] if limit else cases


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--profiles", default="local-qwen", help="comma-separated profile names")
    parser.add_argument(
        "--set", dest="sets", default="golden,attacks", help="golden, attacks or both"
    )
    parser.add_argument("--arm", default="full", help=f"comma-separated, from: {', '.join(ARMS)}")
    parser.add_argument(
        "--run-id", default=default_run_id(), help="names the result files; reuse to resume"
    )
    parser.add_argument("--ids", help="only these golden case ids (comma-separated)")
    parser.add_argument("--limit", type=int, help="only the first N golden cases (for a quick try)")
    parser.add_argument("--backend", choices=("local", "mcp"), default="local")
    parser.add_argument(
        "--no-probe", action="store_true", help="skip the check that a profile answers"
    )
    args = parser.parse_args(argv)

    sets = set(args.sets.split(","))
    arms = args.arm.split(",")
    bad = [a for a in arms if a not in ARMS]
    if bad or not sets <= {"golden", "attacks"}:
        raise SystemExit(f"unknown arm or set: {bad or sets - {'golden', 'attacks'}}")

    if "attacks" in sets:
        out = attacks_path(args.run_id)
        save_outcomes(run_attacks(), out)
        print(f"wrote {out}")
    if "golden" in sets:
        cases = pick_cases(args.ids, args.limit)
        for profile in args.profiles.split(","):
            try:
                if not args.no_probe:
                    asyncio.run(probe(profile, print))
                for arm in arms:
                    out = golden_path(args.run_id, profile, arm)
                    print(f"== {profile} / {arm}: {len(cases)} cases -> {out.name}")
                    records = asyncio.run(
                        run_golden(profile, cases, out, arm=arm, backend=args.backend)
                    )
                    print(
                        f"   done: {len(records)} runs, models: {', '.join(models_used(records))}"
                    )
            except ProfileError as error:
                raise SystemExit(f"profile {profile} is not usable: {error}") from error
    return 0


if __name__ == "__main__":
    sys.exit(main())
