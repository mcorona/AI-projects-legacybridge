"""Deterministic checks on an answer, and the confidence that comes out of them (ADR-002).

The model's own confidence is not used: in practice it is almost always 1.0. Confidence starts from
the independent verifier's verdict and is lowered by things we can observe.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

from legacybridge.agent.loop import QueryRun
from legacybridge.agent.roles.verifier import Verdict

_NUMBER = re.compile(r"(?<![\w.])-?\d[\d.,]*\d|(?<![\w.])-?\d")
YEAR_RANGE = range(1990, 2036)


def parse_number(token: str) -> float | None:
    """'1,234.56', '1.234,56', '829' and '4752727.57' -> float. Ambiguous separators are guessed."""
    token = token.strip().rstrip(".,")
    if not token:
        return None
    if "," in token and "." in token:
        decimal = "," if token.rfind(",") > token.rfind(".") else "."
        thousands = "." if decimal == "," else ","
        token = token.replace(thousands, "").replace(decimal, ".")
    elif "," in token:
        head, _, tail = token.rpartition(",")
        token = token.replace(",", "") if len(tail) == 3 and head else token.replace(",", ".")
    elif token.count(".") > 1:
        token = token.replace(".", "")
    try:
        return float(token)
    except ValueError:
        return None


def _numbers(text: str) -> list[float]:
    return [n for t in _NUMBER.findall(text) if (n := parse_number(t)) is not None]


def _cells(rows: Iterable[Iterable[Any]]) -> set[float]:
    out: set[float] = set()
    for row in rows:
        for cell in row:
            if isinstance(cell, bool):
                continue
            if isinstance(cell, int | float):
                out.add(round(float(cell), 2))
            elif isinstance(cell, str):
                out.update(round(n, 2) for n in _numbers(cell))
    return out


def numbers_not_in_evidence(
    answer: str, question: str, assumptions: list[str], evidence: QueryRun
) -> list[str]:
    """Figures in the answer (three digits or more) that no query result, question or assumption
    contains. A model that does arithmetic in its head, or invents a figure, shows up here."""
    known = _cells(evidence.rows) | {round(n, 2) for n in _numbers(question)}
    known |= {round(n, 2) for a in assumptions for n in _numbers(a)}
    # a derived total is legitimate, so allow the sum of a numeric column as well
    for col in zip(*evidence.rows, strict=False):
        nums = [c for c in col if isinstance(c, int | float) and not isinstance(c, bool)]
        if len(nums) == len(col) and nums:
            known.add(round(float(sum(nums)), 2))
    unknown = []
    for token in _NUMBER.findall(answer):
        n = parse_number(token)
        if n is None or abs(n) < 100 or (float(n).is_integer() and int(n) in YEAR_RANGE):
            continue
        if round(n, 2) not in known:
            unknown.append(token.strip().rstrip(".,"))
    return sorted(set(unknown))


def is_numeric_answer(evidence: QueryRun) -> bool:
    return any(
        isinstance(c, int | float) and not isinstance(c, bool) for r in evidence.rows for c in r
    )


def calibrate(
    *,
    verdict: Verdict | None,
    evidence: QueryRun | None,
    invented: list[str],
    pending_write: bool = False,
) -> float:
    """Confidence in [0.05, 0.99] from what can be observed."""
    if verdict is None:
        confidence = 0.55  # nobody checked it
    elif not verdict.agrees:
        confidence = min(verdict.confidence, 0.3)
    else:
        confidence = verdict.confidence
        if verdict.checks_run == 0 and not pending_write:
            confidence = min(confidence, 0.6)  # it re-derived nothing
    if evidence is not None:
        unresolved = sum(1 for v in evidence.violations if v.get("severity") == "warning")
        confidence -= 0.15 * min(unresolved, 3)
        if evidence.truncated:
            confidence = min(confidence, 0.5)
        if evidence.row_count == 0 and not pending_write:
            confidence = min(confidence, 0.4)  # a confident answer about nothing
    if invented:
        confidence = min(confidence, 0.4)
    return round(min(0.99, max(0.05, confidence)), 2)
