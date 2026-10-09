"""Loader for evals/attacks.yaml (Phase 2): every attack must end blocked or neutralized."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, model_validator

ATTACKS_PATH = Path(__file__).with_name("attacks.yaml")
TOOLS = {"run_query", "describe_table", "sample_rows", "propose_write", "explain_business_term"}


class Expected(StrEnum):
    BLOCKED = "blocked"
    NEUTRALIZED = "neutralized"


class Attack(BaseModel):
    id: str
    category: str
    tool: str
    input: dict[str, Any]
    expected: Expected
    stage: str | None = None  # blocked: the stage reported by the tool
    reason: str | None = None  # blocked: the reason reported by the tool
    check: dict[str, Any] = Field(default_factory=dict)  # neutralized: what must hold
    db_refuses: bool = False  # the SQL, sent to lb_reader without the guard, must fail
    note: str | None = None

    @model_validator(mode="after")
    def _consistent(self) -> Attack:
        if self.tool not in TOOLS:
            raise ValueError(f"{self.id}: unknown tool {self.tool}")
        if self.expected == Expected.BLOCKED and not (self.stage and self.reason):
            raise ValueError(f"{self.id}: a blocked attack needs stage and reason")
        if self.expected == Expected.NEUTRALIZED and not self.check:
            raise ValueError(f"{self.id}: a neutralized attack needs a check")
        if self.db_refuses and "sql" not in self.input:
            raise ValueError(f"{self.id}: db_refuses needs input.sql")
        return self


def load_attacks(path: Path = ATTACKS_PATH) -> list[Attack]:
    return [Attack.model_validate(item) for item in yaml.safe_load(path.read_text("utf-8"))]
