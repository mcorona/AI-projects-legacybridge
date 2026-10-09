"""A demo that needs no model (Phase 8): the pieces around the model, working on the real database.

    uv run python -m evals.demo                # the three acts
    uv run python -m evals.demo --act 2        # only one

There is no language model here. Act 1 drives the real agent with an *oracle* that runs the SQL it
is given, so what you see is what the tools, the rule engine, the verifier and the SWAR detector do
with a query, not what a model would write. The model-driven agent is ``legacybridge ask``.

1. **A trap.** One question, two queries: the naive one gets a plausible wrong number, and the
   rule engine says why; the reference one gets the right number and no warning.
2. **Attacks.** Four of the 45 attacks of ``evals/attacks.yaml`` sent to the tools exactly as an
   agent would, and whether the database would refuse them on its own.
3. **A write.** A change request queues without changing anything, a person lists it and rejects
   it, and the business data is shown to be identical before and after.

Everything runs against the synthetic database; it refuses to run against one that does not say it
is synthetic, and it removes the request it created.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from evals.attacks import AttackOutcome, load_attacks, run_attack
from evals.golden import GoldenCase, load_golden
from evals.labels import label_run
from evals.runner import TABLES, Oracle, cleanup_requests, describe_rows, max_request_id
from legacybridge.agent.backends import McpToolBackend
from legacybridge.agent.hitl import ApprovalService
from legacybridge.agent.orchestrator import Agent
from legacybridge.agent.schemas import AgentConfig
from legacybridge.db import connect
from legacybridge.mcp_server.tools import LegacyTools

TRAP_ID = "TRAP-002"
GLOSS = {"TRAP-002": "How many active invoices were there in 2004?"}  # the questions are Spanish
ATTACK_IDS = ("ATK-002", "ATK-004", "ATK-010", "ATK-034")
WRITE = {"table": "clientes", "update": {"status": "I"}, "where": {"clave": "0000000006"}}
REVIEWER = "demo-reviewer"
WIDTH = 100


class Style:
    """ANSI colors, only on a terminal and unless switched off."""

    def __init__(self, color: bool):
        self.color = color

    def _wrap(self, code: str, text: str) -> str:
        return f"\033[{code}m{text}\033[0m" if self.color else text

    def bold(self, text: str) -> str:
        return self._wrap("1", text)

    def dim(self, text: str) -> str:
        return self._wrap("2", text)

    def good(self, text: str) -> str:
        return self._wrap("32", text)

    def bad(self, text: str) -> str:
        return self._wrap("31", text)

    def warn(self, text: str) -> str:
        return self._wrap("33", text)


def short(text: str, n: int = 78) -> str:
    one = " ".join(str(text).split())
    return one if len(one) <= n else one[: n - 1] + "…"


class NotSynthetic(RuntimeError):
    pass


def require_synthetic() -> None:
    """The demo changes nothing, but it only runs where the data is declared synthetic."""
    with connect("reader", connect_timeout=3) as conn:
        marker = conn.execute("SELECT current_setting('lb.synthetic', true)").fetchone()[0]
    if marker != "true":
        raise NotSynthetic(
            "this database is not marked synthetic (lb.synthetic); not running the demo"
        )


def fingerprint() -> tuple[str, ...]:
    """A digest of every business table: equal before and after means nothing was written."""
    with connect("admin") as conn:
        digest = tuple(
            conn.execute(
                f"SELECT md5(coalesce(string_agg(t::text, '|' ORDER BY t::text), '')) FROM {table} t"
            ).fetchone()[0]
            for table in TABLES
        )
        conn.rollback()
    return digest


# ============================================================================ act 1
@dataclass
class Variant:
    name: str
    sql: str
    value: Any
    violations: list[dict[str, Any]]
    confidence: float
    swar_risk: float
    swar_level: str
    label: str
    reason: str


async def run_variant(tools: LegacyTools, case: GoldenCase, name: str, sql: str) -> Variant:
    probe = tools.run_query(sql, max_rows=500)
    async with McpToolBackend.in_memory(tools) as backend:
        agent = Agent(
            Oracle(sql, describe_rows(probe["rows"])),
            backend,
            config=AgentConfig(swar_action="annotate"),
        )
        result = await agent.ask(case.question)
    with connect("reader", autocommit=True) as reader:
        label = label_run(case, result, reader)
    evidence = result.evidence
    return Variant(
        name=name,
        sql=sql,
        value=evidence.rows[0][0] if evidence and evidence.rows else None,
        violations=list(evidence.violations) if evidence else [],
        confidence=result.confidence,
        swar_risk=result.swar_risk,
        swar_level=result.swar_level,
        label=label.kind,
        reason=label.reason,
    )


async def act_trap(tools: LegacyTools, say: Callable[[str], None], st: Style) -> list[Variant]:
    case = next(c for c in load_golden() if c.id == TRAP_ID)
    assert case.naive_sql, "the trap needs its naive query"
    say(st.bold(f"ACT 1  The same question, two queries   ({TRAP_ID}: {', '.join(case.defects)})"))
    say(f"  {case.question}  {st.dim('(' + GLOSS[TRAP_ID] + ')')}")
    say("")
    variants = []
    for name, sql in (("naive", case.naive_sql), ("reference", case.expected_sql)):
        v = await run_variant(tools, case, name, sql)
        variants.append(v)
        wrong = v.label != "correct"
        say(f"  {st.bold(name + ' SQL'):<20} {st.dim(short(v.sql, 74))}")
        say(f"    result        {st.bad(str(v.value)) if wrong else st.good(str(v.value))}"
            f"   -> {st.bad('silently wrong') if wrong else st.good('correct')}")  # fmt: skip
        if v.violations:
            for violation in v.violations:
                say(f"    rule engine   {st.warn(violation['defect'] + ' ' + violation['rule'])}: "
                    f"{short(violation['message'], 60)}")  # fmt: skip
        else:
            say(f"    rule engine   {st.good('no violations')}")
        say(f"    detector      swar_risk {v.swar_risk:.2f} ({v.swar_level}); the agent's own confidence "
            f"was {v.confidence:.2f}")  # fmt: skip
        say("")
    naive, reference = variants
    say(f"  The naive query is off by {naive.value - reference.value}: invoices captured twice are "
        "counted twice.")  # fmt: skip
    say(
        st.dim(
            "  Same question, same data: only the warning and the risk score tell the two apart."
        )
    )
    return variants


# ============================================================================ act 2
def act_attacks(tools: LegacyTools, say: Callable[[str], None], st: Style) -> list[AttackOutcome]:
    attacks = {a.id: a for a in load_attacks()}
    say(st.bold("ACT 2  Attacks sent to the tools, as an agent would send them"))
    say("")
    outcomes = []
    for attack_id in ATTACK_IDS:
        attack = attacks[attack_id]
        outcome = run_attack(tools, attack)
        outcomes.append(outcome)
        blocked = attack.expected.value == "blocked"
        mark = (
            st.good("blocked" if blocked else "neutralized")
            if outcome.passed
            else st.bad("GOT THROUGH")
        )
        say(f"  {attack.id}  {st.bold(attack.category):<28} {mark}")
        say(f"    {st.dim(short(str(attack.input.get('sql', attack.input)), 90))}")
        if blocked:
            say(f"    refused at the {attack.stage}: {attack.reason}")
        else:
            say(f"    {short(attack.note or outcome.detail, 92)}")
        if outcome.db_refused is not None:
            say(
                f"    without the guard, the read-only role {'also refuses it' if outcome.db_refused else 'does not refuse it'}"
            )
        say("")
    say(st.dim("  All 45 attacks run in CI on every change (make eval-gate)."))
    return outcomes


# ============================================================================ act 3
@dataclass
class WriteFacts:
    request_id: int
    affected_rows: int
    pending_listed: bool
    decision: str
    unchanged: bool
    leftovers: int
    preview: list[dict[str, Any]] = field(default_factory=list)


def act_write(tools: LegacyTools, say: Callable[[str], None], st: Style) -> WriteFacts:
    say(st.bold("ACT 3  A write the agent may only propose"))
    first = max_request_id()
    before = fingerprint()
    try:
        proposal = tools.propose_write(WRITE["table"], WRITE["update"], WRITE["where"], "demo")
        assert proposal["ok"], proposal
        say(f"  agent proposes   set {WRITE['update']} in {WRITE['table']} where {WRITE['where']}")
        say(f"  tool answers     {st.good('queued as request ' + str(proposal['request_id']))}, "
            f"{proposal['affected_rows']} row, nothing changed")  # fmt: skip
        for row in proposal["preview"]:
            changes = ", ".join(f"{c} {row.get(c)} -> {v}" for c, v in WRITE["update"].items())
            say(st.dim(f"                   would change: {row.get('nombre', row)}: {changes}"))
        service = ApprovalService()
        listed = [r for r in service.list("pending") if r["id"] == proposal["request_id"]]
        say(f"  a person lists   {len(listed)} pending request(s): id {proposal['request_id']}")
        decided = service.decide(proposal["request_id"], False, REVIEWER, "rejected in the demo")
        say(f"  a person decides {st.warn(str(decided.get('status', decided)))} by {REVIEWER}")
        after = fingerprint()
        same = before == after
        say(f"  business data    {st.good('identical before and after') if same else st.bad('CHANGED')}"
            f" ({len(TABLES)} tables compared)")  # fmt: skip
        say(st.dim("  The agent has no tool to approve, and the reviewer cannot be the proposer."))
    finally:
        cleanup_requests(first)
    leftovers = max_request_id() - first
    return WriteFacts(
        request_id=proposal["request_id"],
        affected_rows=proposal["affected_rows"],
        pending_listed=bool(listed),
        decision=str(decided.get("status", "")),
        unchanged=same,
        leftovers=max(leftovers, 0),
        preview=proposal["preview"],
    )


# ============================================================================ command
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--act", type=int, choices=(1, 2, 3), action="append", help="run only this act"
    )
    parser.add_argument("--no-color", action="store_true")
    args = parser.parse_args(argv)
    st = Style(color=sys.stdout.isatty() and not args.no_color)
    say = print
    try:
        require_synthetic()
    except NotSynthetic as error:
        print(error, file=sys.stderr)
        return 1
    except Exception as error:  # the database is not up
        print(
            f"cannot reach the database ({error}); run `make db-up && make seed`", file=sys.stderr
        )
        return 1
    tools = LegacyTools()
    acts = args.act or [1, 2, 3]
    if 1 in acts:
        asyncio.run(act_trap(tools, say, st))
        say("")
    if 2 in acts:
        act_attacks(tools, say, st)
        say("")
    if 3 in acts:
        act_write(tools, say, st)
    return 0


if __name__ == "__main__":
    sys.exit(main())
