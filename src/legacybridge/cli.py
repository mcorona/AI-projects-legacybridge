"""``legacybridge`` command line: ask a question, serve the API, review write requests."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence

import anyio

from legacybridge.agent.factory import open_agent
from legacybridge.agent.hitl import ApprovalError, ApprovalService
from legacybridge.agent.schemas import AgentConfig, AgentResult
from legacybridge.env import load_env
from legacybridge.llm.base import LLMClient, LLMError
from legacybridge.llm.profiles import ProfileError
from legacybridge.observability.tracing import setup_tracing, shutdown_tracing


def format_result(r: AgentResult) -> str:
    head = f"[{r.status}]"
    if r.status in ("answered", "pending_approval"):
        head += f" confidence {r.confidence:.2f}, {'verified' if r.verified else 'not verified'}"
    lines = [head, r.answer]
    if r.sql:
        lines += ["", f"SQL: {r.sql}"]
    if r.request_id is not None:
        lines += [
            f"Request: #{r.request_id}  (review with: legacybridge approvals show {r.request_id})"
        ]
    if r.assumptions:
        lines += ["", "Assumptions:", *[f"  - {a}" for a in r.assumptions]]
    if r.warnings:
        lines += ["", "Warnings:", *[f"  - {w}" for w in r.warnings]]
    if r.abstain_reason:
        lines += [f"Reason: {r.abstain_reason}"]
    u = r.usage
    lines += [
        "",
        f"{r.profile}: {u.llm_calls} model calls, {u.tool_calls} tool calls, "
        f"{u.input_tokens}/{u.output_tokens} tokens, {u.ms / 1000:.1f} s",
    ]
    return "\n".join(lines)


async def _ask(args: argparse.Namespace, llm: LLMClient | None) -> AgentResult:
    config = AgentConfig(
        verify=not args.no_verify, max_steps=args.max_steps, swar_action=args.swar_action
    )
    async with open_agent(
        args.profile,
        backend=args.backend,
        config=config,
        llm=llm,
        escalation_profile=args.escalation_profile,
    ) as agent:
        return await agent.ask(args.question)


def _approvals(args: argparse.Namespace, service: ApprovalService) -> int:
    try:
        if args.action == "list":
            rows = service.list(status=args.status)
            for r in rows:
                change = (
                    f"set {json.dumps(r['update_values'])} where {json.dumps(r['where_values'])}"
                )
                print(
                    f"#{r['id']:<5} {r['status']:<9} {r['tbl']:<10} {change} "
                    f"({r['affected_rows']} rows, by {r['requested_by']})"
                )
            if not rows:
                print("no requests")
        elif args.action == "show":
            print(json.dumps(service.get(args.id), indent=2, ensure_ascii=False))
        else:
            done = service.decide(args.id, args.action == "approve", args.reviewer, args.note)
            print(f"request #{done['id']} is now {done['status']} (reviewer {done['decided_by']})")
    except ApprovalError as error:
        print(f"{error.code}: {error}", file=sys.stderr)
        return 1
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="legacybridge", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    ask = sub.add_parser("ask", help="ask a question about the legacy data")
    ask.add_argument("question")
    ask.add_argument(
        "--profile",
        default="local-qwen",
        help="a profile of config/models.yaml: local-qwen | omniroute-free | bedrock-claude | "
        "bedrock-claude-sonnet | bedrock-claude-opus",
    )
    ask.add_argument("--json", action="store_true", help="print the full result as JSON")
    ask.add_argument("--no-verify", action="store_true", help="skip the independent verifier")
    ask.add_argument(
        "--backend",
        choices=("mcp", "local"),
        default="mcp",
        help="mcp starts the real server (default); local runs it in-process",
    )
    ask.add_argument("--max-steps", type=int, default=10)
    ask.add_argument(
        "--swar-action",
        choices=("gate", "annotate", "off"),
        default="gate",
        help="early warning: gate acts (escalate, clarify, abstain); annotate only reports",
    )
    ask.add_argument(
        "--escalation-profile",
        help="profile to rerun on when the early warning escalates (or LB_ESCALATION_PROFILE)",
    )

    serve = sub.add_parser("serve", help="run the HTTP API")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)

    appr = sub.add_parser("approvals", help="review write requests (a person, not the agent)")
    actions = appr.add_subparsers(dest="action", required=True)
    lst = actions.add_parser("list")
    lst.add_argument("--status", choices=("pending", "rejected", "executed", "failed"))
    show = actions.add_parser("show")
    show.add_argument("id", type=int)
    for name in ("approve", "reject"):
        p = actions.add_parser(name)
        p.add_argument("id", type=int)
        p.add_argument("--reviewer", required=True, help="who is deciding")
        p.add_argument("--note")
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    llm: LLMClient | None = None,
    service: ApprovalService | None = None,
) -> int:
    load_env()
    args = build_parser().parse_args(argv)
    try:
        if args.command == "ask":
            setup_tracing("legacybridge")
            try:
                result = anyio.run(_ask, args, llm)
            finally:
                shutdown_tracing()  # a short-lived process must flush its spans before it exits
            print(result.model_dump_json(indent=2) if args.json else format_result(result))
            return 0
        if args.command == "serve":
            import uvicorn

            if args.host not in ("127.0.0.1", "localhost", "::1"):
                print(
                    "warning: serving on a non-loopback address; /ask has no authentication",
                    file=sys.stderr,
                )
            uvicorn.run(
                "legacybridge.api.app:create_app", factory=True, host=args.host, port=args.port
            )
            return 0
        return _approvals(args, service or ApprovalService())
    except (ProfileError, LLMError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


def _entry() -> None:  # pragma: no cover
    sys.exit(main())


if __name__ == "__main__":  # pragma: no cover
    _entry()
