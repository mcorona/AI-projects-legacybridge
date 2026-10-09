"""Runs golden cases through the real agent and stores labelled results (ADR-013).

Shared by the full evaluation (``run_evals``), the SWAR measurement (``swar_eval``) and the CI gate
(``gate``). Raw results are JSONL files in ``evals/results/`` (not versioned), one line per run, so
a long run can be stopped and resumed.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel

from evals.golden import Behavior, GoldenCase
from evals.labels import label_run
from legacybridge.agent.backends import McpToolBackend
from legacybridge.agent.factory import open_agent
from legacybridge.agent.orchestrator import Agent
from legacybridge.agent.schemas import AgentConfig, AgentResult
from legacybridge.db import connect
from legacybridge.llm.base import LLMClient, LLMResponse, Message, ToolCall, ToolSpec
from legacybridge.mcp_server.tools import LegacyTools

ROOT = Path(__file__).resolve().parent
RESULTS = ROOT / "results"
TABLES = ("clientes", "productos", "faccab", "facdet", "invart", "invexi", "invmov", "invprv")
Arm = Literal["full", "no-verify", "no-semantic"]
ARMS: tuple[Arm, ...] = ("full", "no-verify", "no-semantic")


class Record(BaseModel):
    case_id: str
    category: str
    variant: str  # live | oracle-expected | oracle-naive
    profile: str
    result: AgentResult
    kind: str
    reason: str
    arm: str = "full"  # the fields below were added in Phase 6; older results load with defaults
    commit: str = ""
    dirty: bool = False


def git_state() -> tuple[str, bool]:
    """The commit a run belongs to and whether the tree had uncommitted changes (ADR-013)."""
    try:
        head = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        dirty = bool(
            subprocess.run(
                ["git", "status", "--porcelain", "--", "src", "evals", "config", "tests/fixtures"],
                cwd=ROOT.parent, capture_output=True, text=True, check=True,
            ).stdout.strip()
        )  # fmt: skip
        return head, dirty
    except (OSError, subprocess.CalledProcessError):
        return "unknown", True


def arm_config(arm: str, action: str = "annotate") -> AgentConfig:
    """The agent configuration of an arm. The early warning only annotates during a measurement, so
    it cannot hide the very answers that are being scored."""
    if arm == "full":
        return AgentConfig(swar_action=action)
    if arm == "no-verify":
        return AgentConfig(swar_action=action, verify=False)
    if arm == "no-semantic":
        return AgentConfig(swar_action=action, semantic=False)
    raise ValueError(f"unknown arm {arm!r}; choose one of {', '.join(ARMS)}")


def fingerprint(conn) -> list[str]:
    return [
        conn.execute(
            f"SELECT md5(string_agg(t::text, '|' ORDER BY t::text)) FROM {t} t"
        ).fetchone()[0]
        for t in TABLES
    ]


def cleanup_requests(first_id: int) -> None:
    with connect("admin") as conn:
        conn.execute("DELETE FROM lb_ops.write_requests WHERE id > %s", (first_id,))
        conn.commit()


def max_request_id() -> int:
    with connect("admin") as conn:
        value = conn.execute("SELECT coalesce(max(id), 0) FROM lb_ops.write_requests").fetchone()[0]
        conn.rollback()
        return value


def load_records(path: Path) -> list[Record]:
    return [
        Record.model_validate_json(line) for line in path.read_text().splitlines() if line.strip()
    ]


def models_used(records: Iterable[Record]) -> list[str]:
    """The model ids that actually answered (what the gateway served, not what was asked for)."""
    return sorted({e.name for r in records for e in r.result.events if e.kind == "llm" and e.ok})


# ============================================================================ live
async def run_golden(
    profile: str,
    cases: list[GoldenCase],
    out: Path,
    *,
    arm: str = "full",
    backend: str = "local",
    action: str = "annotate",
    escalation_profile: str | None = None,
    llm: LLMClient | None = None,
    log: Callable[[str], None] = print,
) -> list[Record]:
    """Ask every case, label the answer, store it. Cases already in ``out`` are skipped."""
    done = (
        {json.loads(line)["case_id"] for line in out.read_text().splitlines() if line.strip()}
        if out.exists()
        else set()
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    commit, dirty = git_state()
    with connect("admin") as admin:
        before = fingerprint(admin)
        admin.rollback()
    async with open_agent(
        profile,
        backend=backend,
        config=arm_config(arm, action),
        llm=llm,
        escalation_profile=escalation_profile,
    ) as agent:
        with connect("reader", autocommit=True) as reader:
            for n, case in enumerate(cases, 1):
                if case.id in done:
                    log(f"[{n}/{len(cases)}] {case.id}: already in {out.name}, skipped")
                    continue
                first_id = max_request_id()
                result = await agent.ask(case.question)
                label = label_run(case, result, reader)
                cleanup_requests(first_id)
                rec = Record(
                    case_id=case.id, category=case.category.value, variant="live", profile=profile,
                    result=result, kind=label.kind, reason=label.reason, arm=arm, commit=commit,
                    dirty=dirty,
                )  # fmt: skip
                with out.open("a", encoding="utf-8") as fh:
                    fh.write(rec.model_dump_json() + "\n")
                log(
                    f"[{n}/{len(cases)}] {case.id:11} {result.status:20} "
                    f"risk {result.swar_risk:.2f} "
                    f"{result.swar_level:8} -> {label.kind} ({label.reason[:50]}) "
                    f"{result.usage.ms / 1000:.0f}s"
                )
    with connect("admin") as admin:
        after = fingerprint(admin)
        admin.rollback()
    if before != after:
        raise RuntimeError("business data changed during the evaluation: this must never happen")
    return load_records(out)


# ============================================================================ oracle (no LLM)
class Oracle:
    """A model that executes one given SQL and reports its rows: no LLM, so no model noise.

    It lets the real orchestrator, tools and detector see a run whose SQL we control. The verifier
    re-runs the same SQL and agrees, so what is measured is everything around the model."""

    name = "oracle"

    def __init__(self, sql: str, answer: str):
        self.sql, self.answer = sql, answer
        self._writer = 0
        self._verifier = 0

    @staticmethod
    def _calls(*pairs: tuple[str, dict[str, Any]]) -> LLMResponse:
        calls = tuple(ToolCall(f"c{i}", n, a) for i, (n, a) in enumerate(pairs))
        return LLMResponse(text="", tool_calls=calls, model="oracle", stop_reason="tool_calls")

    async def chat(self, messages: list[Message], tools: list[ToolSpec] | None = None, **kw: Any):
        head = messages[0].content
        if head.startswith("You are the planner"):
            return LLMResponse(
                text=json.dumps({"action": "query", "language": "es", "intent": "oracle"}),
                model="oracle",
            )
        if head.startswith("You answer ONE question"):
            self._writer += 1
            if self._writer == 1:
                return self._calls(("run_query", {"sql": self.sql}))
            return self._calls(
                (
                    "submit_answer",
                    {"answer": self.answer, "outcome": "answer", "evidence_query": "q1"},
                )
            )
        self._verifier += 1
        if self._verifier == 1:
            return self._calls(("run_query", {"sql": self.sql}))
        return self._calls(("submit_verdict", {"agrees": True, "confidence": 0.9, "issues": []}))


def describe_rows(rows: list[list[Any]]) -> str:
    return "Resultado: " + "; ".join(", ".join(str(c) for c in row) for row in rows[:5])


async def run_oracle(
    cases: Iterable[GoldenCase], log: Callable[[str], None] = print
) -> list[Record]:
    tools = LegacyTools()
    records: list[Record] = []
    commit, dirty = git_state()
    with connect("reader", autocommit=True) as reader:
        async with McpToolBackend.in_memory(tools) as backend:
            for case in cases:
                if case.expected_behavior != Behavior.ANSWER:
                    continue
                variants = [("oracle-expected", case.expected_sql)]
                if case.naive_sql:
                    variants.append(("oracle-naive", case.naive_sql))
                for variant, sql in variants:
                    probe = tools.run_query(sql, max_rows=500)
                    if not probe["ok"]:  # a rejected or failing query is a loud failure, not SWAR
                        why = f"{probe.get('stage')}/{probe.get('reason')}"
                        log(f"{case.id} {variant}: skipped ({why})")
                        continue
                    agent = Agent(
                        Oracle(sql, describe_rows(probe["rows"])), backend,
                        config=AgentConfig(swar_action="annotate"),
                    )  # fmt: skip
                    result = await agent.ask(case.question)
                    label = label_run(case, result, reader)
                    records.append(
                        Record(
                            case_id=case.id,
                            category=case.category.value,
                            variant=variant,
                            profile="oracle",
                            result=result,
                            kind=label.kind,
                            reason=label.reason,
                            commit=commit,
                            dirty=dirty,
                        )  # fmt: skip
                    )
    return records
