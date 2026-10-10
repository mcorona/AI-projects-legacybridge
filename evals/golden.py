"""Loader and validation for evals/golden_set.yaml.

Each case is a Pydantic model, so a malformed case fails loudly at load time. The expected
behaviours map to the agent's ``status`` values (CLAUDE.md): answer -> answered,
clarify -> needs_clarification, abstain -> abstained, propose_write -> pending_approval.
"""

from __future__ import annotations

import re
from enum import StrEnum
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, model_validator

from legacybridge import policy

GOLDEN_PATH = Path(__file__).with_name("golden_set.yaml")
HASH_PATTERN = re.compile(r"^(auto|sha256:[0-9a-f]{64})$")
DEFECT_PATTERN = re.compile(r"^(FAC|SIC)-\d{2}$")


class Category(StrEnum):
    SIMPLE = "simple"
    JOIN_AGG = "join_agg"
    DEFECT_TRAP = "defect_trap"
    AMBIGUOUS = "ambiguous"
    OUT_OF_SCOPE = "out_of_scope"
    WRITE = "write"


class Behavior(StrEnum):
    ANSWER = "answer"
    CLARIFY = "clarify"
    ABSTAIN = "abstain"
    PROPOSE_WRITE = "propose_write"


class Review(StrEnum):
    DRAFT = "draft"
    APPROVED = "approved"


ID_PREFIX = {
    Category.SIMPLE: "SIMPLE",
    Category.JOIN_AGG: "JOIN",
    Category.DEFECT_TRAP: "TRAP",
    Category.AMBIGUOUS: "AMB",
    Category.OUT_OF_SCOPE: "OOS",
    Category.WRITE: "WRITE",
}
CATEGORY_BEHAVIOR = {
    Category.SIMPLE: Behavior.ANSWER,
    Category.JOIN_AGG: Behavior.ANSWER,
    Category.DEFECT_TRAP: Behavior.ANSWER,
    Category.AMBIGUOUS: Behavior.CLARIFY,
    Category.OUT_OF_SCOPE: Behavior.ABSTAIN,
    Category.WRITE: Behavior.PROPOSE_WRITE,
}
AGENT_STATUS = {
    Behavior.ANSWER: "answered",
    Behavior.CLARIFY: "needs_clarification",
    Behavior.ABSTAIN: "abstained",
    Behavior.PROPOSE_WRITE: "pending_approval",
}
EXPECTED_COUNTS = {
    Category.SIMPLE: 15,
    Category.JOIN_AGG: 20,
    Category.DEFECT_TRAP: 20,
    Category.AMBIGUOUS: 10,
    Category.OUT_OF_SCOPE: 5,
    Category.WRITE: 10,
}
# Write cases must stay inside what lb_writer may UPDATE; the single source of truth is policy.py.
WRITABLE = policy.WRITABLE


class WriteChange(BaseModel):
    table: str
    update: dict[str, str]
    where: dict[str, str]


class GoldenCase(BaseModel):
    id: str
    question: str
    category: Category
    expected_behavior: Behavior
    review: Review = Review.DRAFT
    defects: list[str] = Field(default_factory=list)
    expected_sql: str | None = None
    naive_sql: str | None = None
    expected_result_hash: str | None = None
    expected_change: WriteChange | None = None
    notes: str | None = None

    @property
    def agent_status(self) -> str:
        return AGENT_STATUS[self.expected_behavior]

    @model_validator(mode="after")
    def _consistent(self) -> GoldenCase:
        if not self.id.startswith(f"{ID_PREFIX[self.category]}-"):
            raise ValueError(f"{self.id}: id must start with {ID_PREFIX[self.category]}-")
        if self.expected_behavior != CATEGORY_BEHAVIOR[self.category]:
            raise ValueError(
                f"{self.id}: {self.category} implies {CATEGORY_BEHAVIOR[self.category]}"
            )
        bad = [d for d in self.defects if not DEFECT_PATTERN.match(d)]
        if bad:
            raise ValueError(f"{self.id}: malformed defect ids {bad}")
        answers = self.expected_behavior == Behavior.ANSWER
        if answers != (self.expected_sql is not None):
            raise ValueError(f"{self.id}: expected_sql is required exactly for answer cases")
        if answers != (self.expected_result_hash is not None):
            raise ValueError(
                f"{self.id}: expected_result_hash is required exactly for answer cases"
            )
        if self.expected_result_hash and not HASH_PATTERN.match(self.expected_result_hash):
            raise ValueError(f"{self.id}: expected_result_hash must be 'auto' or 'sha256:<hex>'")
        if self.review == Review.APPROVED and self.expected_result_hash == "auto":
            raise ValueError(f"{self.id}: an approved case cannot have an 'auto' hash")
        if self.category == Category.DEFECT_TRAP and not (self.defects and self.naive_sql):
            raise ValueError(f"{self.id}: a defect trap needs defects and naive_sql")
        if self.category != Category.DEFECT_TRAP and self.naive_sql:
            raise ValueError(f"{self.id}: only defect traps carry naive_sql")
        if (self.category == Category.WRITE) != (self.expected_change is not None):
            raise ValueError(f"{self.id}: expected_change is required exactly for write cases")
        return self


def load_golden(path: Path = GOLDEN_PATH) -> list[GoldenCase]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return [GoldenCase.model_validate(item) for item in raw]


APPROVED_ON = "2026-10-09"


def all_approved(cases) -> bool:
    """True when every case has been validated by the owner (``review: approved``)."""
    cases = list(cases)
    return bool(cases) and all(c.review == Review.APPROVED for c in cases)
