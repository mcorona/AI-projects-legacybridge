"""The Lambda that answers one question (ADR-014): it runs the agent and stores the outcome.

It is the only function with access to the database and to the model. An asynchronous invocation
carries the job (id, question, profile, verify); the result goes to the job store, one metrics
record goes to the logs (``observability/emf.py``) and the trace is flushed before Lambda freezes.
"""

from __future__ import annotations

import asyncio
import traceback
from collections.abc import Awaitable, Callable
from typing import Any

from legacybridge.agent.factory import open_agent
from legacybridge.agent.schemas import AgentConfig, AgentResult
from legacybridge.aws.jobs import DynamoJobStore, JobStore
from legacybridge.aws.secrets import load_runtime_env
from legacybridge.llm.profiles import load_profile
from legacybridge.observability import emf
from legacybridge.observability.tracing import flush_tracing, setup_tracing

Runner = Callable[[str, str, bool], Awaitable[AgentResult]]  # question, profile, verify


async def run_agent(question: str, profile: str, verify: bool) -> AgentResult:
    """One question on the in-process tools (no subprocess: this is a function, not a server)."""
    async with open_agent(profile, backend="local", config=AgentConfig(verify=verify)) as agent:
        return await agent.ask(question)


def prices(profile: str) -> tuple[float, float]:
    """USD per million tokens of the profile, from ``config/models.yaml``. It is the profile the
    runner just used, so it loads; if it did not, the job has already failed."""
    p = load_profile(profile)
    return p.price_in_per_mtok, p.price_out_per_mtok


async def process(
    event: dict[str, Any],
    store: JobStore,
    runner: Runner = run_agent,
    emit: Callable[..., None] = emf.emit,
    price_of: Callable[[str], tuple[float, float]] = prices,
) -> str:
    """Answer the job in ``event`` and record the outcome. Returns the final status."""
    job_id = event["id"]
    store.update(job_id, status="running")
    try:
        result = await runner(event["question"], event["profile"], bool(event.get("verify", True)))
    except Exception as error:  # a profile that cannot start, a model that fails: the job says so
        print(traceback.format_exc(), flush=True)
        store.update(job_id, status="failed", error=f"{type(error).__name__}: {error}"[:300])
        return "failed"
    price_in, price_out = price_of(event["profile"])
    emit(result, price_in=price_in, price_out=price_out)
    store.update(job_id, status="done", result=result.model_dump_json(exclude={"events"}))
    return "done"


_store: JobStore | None = None


def handler(event: dict[str, Any], context: Any = None) -> dict[str, str]:
    """The Lambda entry point."""
    import os

    import boto3

    global _store
    if _store is None:
        load_runtime_env()  # DSNs from Secrets Manager, once per execution environment
        _store = DynamoJobStore(os.environ["LB_JOBS_TABLE"], boto3.client("dynamodb"))
    setup_tracing("legacybridge-worker")
    try:
        status = asyncio.run(process(event, _store))
    finally:
        flush_tracing()  # spans must leave before the environment is frozen
    return {"id": event["id"], "status": status}
