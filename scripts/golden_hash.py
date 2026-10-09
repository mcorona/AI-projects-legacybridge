"""Compute and verify ``expected_result_hash`` for evals/golden_set.yaml (ADR-011).

Usage:
    uv run python -m scripts.golden_hash --check            # stored hashes match the live data
    uv run python -m scripts.golden_hash --write            # replace 'auto' placeholders
    uv run python -m scripts.golden_hash --write --refresh  # also recompute draft hashes

Queries run as ``lb_reader``. Cases marked ``review: approved`` are never rewritten.
A trap's ``naive_sql`` must either fail in the database or return a different result set.
"""

from __future__ import annotations

import argparse
import re
import sys

import psycopg

from evals.golden import GOLDEN_PATH, Behavior, Category, Review, load_golden
from evals.hashing import run_and_hash
from legacybridge.db import connect

ID_LINE = re.compile(r"^- id:\s*(\S+)")
HASH_LINE = re.compile(r"^(\s+expected_result_hash:\s*)(auto|sha256:[0-9a-f]{64})\s*$")


def compute(conn) -> dict[str, str]:
    cases = [c for c in load_golden() if c.expected_behavior == Behavior.ANSWER]
    return {c.id: run_and_hash(conn, c.expected_sql) for c in cases}


def naive_bites(conn, case) -> bool:
    """True when the defect-unaware query fails or returns something other than the expected."""
    try:
        return run_and_hash(conn, case.naive_sql) != run_and_hash(conn, case.expected_sql)
    except psycopg.Error:
        conn.rollback()
        return True


def rewrite(text: str, hashes: dict[str, str], writable: set[str], refresh: bool) -> str:
    out, current = [], None
    for line in text.splitlines(keepends=True):
        if m := ID_LINE.match(line):
            current = m.group(1)
        elif (m := HASH_LINE.match(line)) and current in hashes:
            if current in writable and (m.group(2) == "auto" or refresh):
                line = f"{m.group(1)}{hashes[current]}\n"
        out.append(line)
    return "".join(out)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--check", action="store_true")
    group.add_argument("--write", action="store_true")
    parser.add_argument("--refresh", action="store_true", help="with --write: recompute drafts")
    args = parser.parse_args()

    cases = load_golden()
    with connect("reader", autocommit=True) as conn:
        hashes = compute(conn)
        if args.write:
            writable = {c.id for c in cases if c.review == Review.DRAFT}
            text = GOLDEN_PATH.read_text(encoding="utf-8")
            GOLDEN_PATH.write_text(rewrite(text, hashes, writable, args.refresh), encoding="utf-8")
            print(f"wrote hashes for {len(writable & set(hashes))} draft cases")
            return 0

        problems = []
        for case in cases:
            if case.expected_behavior != Behavior.ANSWER:
                continue
            if case.expected_result_hash != hashes[case.id]:
                problems.append(
                    f"{case.id}: stored {case.expected_result_hash} != live {hashes[case.id]}"
                )
            if case.category == Category.DEFECT_TRAP and not naive_bites(conn, case):
                problems.append(
                    f"{case.id}: naive_sql returns the expected result (the trap does not bite)"
                )
    for problem in problems:
        print("FAIL", problem)
    print(f"{len(hashes)} hashes checked, {len(problems)} problems")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
