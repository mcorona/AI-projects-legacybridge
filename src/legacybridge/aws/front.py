"""The public side of the asynchronous API (ADR-014): accept a question, hand it to a worker, and
report on it later.

API Gateway ends a request after 30 seconds and an agent on a hosted model can take longer, so
``POST /ask`` answers 202 with a job id and ``GET /ask/{id}`` is polled for the result. This
function has no database, no model and no VPC: it checks the bearer token, writes a job and starts
the worker. Whoever breaks into it can read jobs and start questions, and nothing else.
"""

from __future__ import annotations

import hmac
import json
import os
import re
import time
import uuid
from collections.abc import Callable
from typing import Any

import boto3
from fastapi import Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

from legacybridge.aws.jobs import DynamoJobStore, Job, JobStore
from legacybridge.aws.secrets import read_token

JOB_ID = r"^[0-9a-f]{32}$"
DEADLINE_S = 360  # a job still running after this long lost its worker (the worker times out first)

Dispatch = Callable[[Job], None]


class AskRequest(BaseModel):
    question: str = Field(min_length=3, max_length=2000)
    profile: str | None = Field(default=None, pattern=r"^[a-z0-9][a-z0-9-]{0,39}$")
    verify: bool = True


class LambdaDispatcher:
    """Starts the worker with an asynchronous invocation: the call returns at once and Lambda does
    not retry it (a retry would pay for the same question twice; the stack turns retries off)."""

    def __init__(self, function_name: str, client: Any):
        self.function_name, self._client = function_name, client

    def __call__(self, job: Job) -> None:
        payload = {
            "id": job.id,
            "question": job.question,
            "profile": job.profile,
            "verify": job.verify,
        }
        self._client.invoke(
            FunctionName=self.function_name,
            InvocationType="Event",
            Payload=json.dumps(payload).encode(),
        )


def public_view(job: Job, now: float, deadline_s: float) -> dict[str, Any]:
    status = job.status
    if status in ("pending", "running") and now - job.created > deadline_s:
        status = "timed_out"
    out: dict[str, Any] = {"id": job.id, "status": status, "question": job.question}
    if job.result is not None:
        out["result"] = json.loads(job.result)
    if job.error:
        out["error"] = job.error
    return out


def create_front_app(
    store: JobStore,
    dispatch: Dispatch,
    token: str | None,
    *,
    require_auth: bool = True,
    default_profile: str = "bedrock-claude",
    deadline_s: float = DEADLINE_S,
    now: Callable[[], float] = time.time,
) -> FastAPI:
    if require_auth and not token:
        raise RuntimeError("the API requires a bearer token and none is configured (fail closed)")
    app = FastAPI(title="LegacyBridge", version="1.0.0")

    def authorize(request: Request) -> None:
        if not token:
            return  # only reachable with require_auth=False: local use
        scheme, _, supplied = request.headers.get("authorization", "").partition(" ")
        if scheme.lower() != "bearer" or not hmac.compare_digest(
            supplied.strip().encode(), token.encode()
        ):
            raise HTTPException(
                401, "invalid or missing bearer token", {"WWW-Authenticate": "Bearer"}
            )

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/ask", status_code=202, dependencies=[Depends(authorize)])
    def ask(body: AskRequest) -> dict[str, str]:
        job = Job(
            id=uuid.uuid4().hex,
            question=body.question,
            profile=body.profile or default_profile,
            verify=body.verify,
            created=now(),
        )
        store.create(job)
        try:
            dispatch(job)
        except Exception as error:  # the worker did not start: say so instead of leaving it pending
            store.update(
                job.id, status="failed", error=f"could not start the worker: {error}"[:300]
            )
            raise HTTPException(502, "the worker could not be started") from error
        return {"id": job.id, "status": "pending", "poll": f"/ask/{job.id}"}

    @app.get("/ask/{job_id}", dependencies=[Depends(authorize)])
    def get_job(job_id: str) -> dict[str, Any]:
        job = store.get(job_id) if re.fullmatch(JOB_ID, job_id) else None
        if job is None:
            raise HTTPException(404, "no such job")
        return public_view(job, now(), deadline_s)

    return app


def app_from_env() -> FastAPI:
    """The app Lambda runs (``uvicorn --factory legacybridge.aws.front:app_from_env``). It refuses
    to start without the token, so a lost secret closes the API rather than opening it."""
    function = os.environ["LB_WORKER_FUNCTION"]
    return create_front_app(
        DynamoJobStore(os.environ["LB_JOBS_TABLE"], boto3.client("dynamodb")),
        LambdaDispatcher(function, boto3.client("lambda")),
        read_token(),
        require_auth=True,
        default_profile=os.environ.get("LB_DEFAULT_PROFILE", "bedrock-claude"),
    )


def make_handler(app: FastAPI) -> Callable[[dict[str, Any], Any], dict[str, Any]]:
    """The Lambda handler for an HTTP API (payload format 2.0) in front of ``app``."""
    from mangum import Mangum

    return Mangum(app, lifespan="off")  # no startup work: the app is built before the first call


_handler: Callable[[dict[str, Any], Any], dict[str, Any]] | None = None


def handler(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
    """The Lambda entry point. The app is built on the first call, once per environment."""
    global _handler
    if _handler is None:
        _handler = make_handler(app_from_env())
    return _handler(event, context)
