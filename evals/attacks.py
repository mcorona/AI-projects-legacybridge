"""Loader for evals/attacks.yaml (Phase 2): every attack must end blocked or neutralized."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

import psycopg
import yaml
from pydantic import BaseModel, Field, model_validator

from legacybridge import policy
from legacybridge.db import connect

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


# ============================================================================ running an attack
@dataclass
class AttackOutcome:
    attack_id: str
    category: str
    passed: bool  # the tool ended blocked or neutralized, as the case demands
    detail: str  # why it did not, or how it ended
    db_refused: bool | None = None  # db_refuses attacks: did lb_reader refuse it with no guard?


def call_tool(tools: Any, attack: Attack) -> dict:
    """Send the attack to the tool it targets, exactly as an agent would."""
    i = attack.input
    if attack.tool == "run_query":
        return tools.run_query(i["sql"], i.get("max_rows", policy.DEFAULT_ROWS))
    if attack.tool == "describe_table":
        return tools.describe_table(i["name"])
    if attack.tool == "sample_rows":
        return tools.sample_rows(i["name"], i["n"])
    if attack.tool == "propose_write":
        return tools.propose_write(i["table"], i["update"], i["where"], i.get("reason"))
    return tools.explain_business_term(i["term"])


def evaluate(attack: Attack, result: dict) -> tuple[bool, str]:
    """Did the tool's answer end the attack the way the case says it must?"""
    if attack.expected == Expected.BLOCKED:
        if result.get("ok") is not False:
            return False, f"got through: {str(result)[:160]}"
        got = (result.get("stage"), result.get("reason"))
        if got != (attack.stage, attack.reason):
            return False, f"blocked at {got}, expected {(attack.stage, attack.reason)}"
        return True, "blocked"
    if result.get("ok") is not True:
        return False, f"expected a neutralized result, got {str(result)[:160]}"
    check = attack.check
    if "max_rows" in check and result["row_count"] > check["max_rows"]:
        return False, f"{result['row_count']} rows exceed the cap of {check['max_rows']}"
    if "truncated" in check and result["truncated"] is not check["truncated"]:
        return False, f"truncated is {result['truncated']}, expected {check['truncated']}"
    if "warning_contains" in check and not any(
        check["warning_contains"] in w for w in result["warnings"]
    ):
        return False, f"no warning contains {check['warning_contains']!r}"
    if "row_contains" in check:
        cells = [str(cell) for row in result["rows"] for cell in row]
        if not any(check["row_contains"] in cell for cell in cells):
            return False, "the hostile text did not come back verbatim"
    if result.get("data_notice") != policy.DATA_NOTICE:
        return False, "the result carries no data notice"
    return True, "neutralized"


def database_refuses(attack: Attack) -> bool:
    """The same SQL, sent to lb_reader with no guard, must be refused (defense in depth)."""
    try:
        with connect("reader", autocommit=True) as conn:
            conn.execute(attack.input["sql"])
    except psycopg.Error:
        return True
    return False


def run_attack(tools: Any, attack: Attack, *, check_database: bool = True) -> AttackOutcome:
    passed, detail = evaluate(attack, call_tool(tools, attack))
    refused = database_refuses(attack) if (check_database and attack.db_refuses) else None
    return AttackOutcome(attack.id, attack.category, passed, detail, refused)
