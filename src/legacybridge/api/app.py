"""HTTP API: ask a question, and let a person review write requests (ADR-005).

``/ask`` is open (the API listens on loopback by default). The approval endpoints need
``Authorization: Bearer $LB_API_TOKEN`` and fail closed: with no token configured they answer 503.
Approving is not something the agent can do through any route here or through the MCP server.
"""

from __future__ import annotations

import hmac
import os
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any, Literal

import anyio
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from legacybridge.agent.backends import McpToolBackend
from legacybridge.agent.factory import build_llm
from legacybridge.agent.hitl import ApprovalError, ApprovalService
from legacybridge.agent.orchestrator import Agent
from legacybridge.agent.schemas import AgentConfig, AgentResult
from legacybridge.env import load_env
from legacybridge.llm.base import LLMError
from legacybridge.llm.profiles import ProfileError
from legacybridge.observability.tracing import setup_tracing, shutdown_tracing

AgentRunner = Callable[[str, str, bool], Awaitable[AgentResult]]  # question, profile, verify


class AskRequest(BaseModel):
    question: str = Field(min_length=3, max_length=2000)
    profile: str | None = None
    verify: bool = True


class Decision(BaseModel):
    decision: Literal["approve", "reject"]
    reviewer: str = Field(min_length=1, max_length=80)
    note: str | None = Field(default=None, max_length=500)


def create_app(
    *,
    runner: AgentRunner | None = None,
    approvals: ApprovalService | None = None,
    token: str | None = None,
) -> FastAPI:
    load_env()
    setup_tracing("legacybridge-api")
    secret = token if token is not None else os.environ.get("LB_API_TOKEN", "")
    service = approvals or ApprovalService()
    state: dict[str, Any] = {"run": runner} if runner is not None else {}

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        if runner is not None:
            yield
            return
        # One MCP server for the life of the API, and one model client per profile.
        clients: dict[str, Any] = {}
        async with McpToolBackend.stdio() as backend:

            async def run(question: str, profile: str, verify: bool) -> AgentResult:
                if profile not in clients:
                    clients[profile] = await anyio.to_thread.run_sync(build_llm, profile)
                agent = Agent(clients[profile], backend, config=AgentConfig(verify=verify))
                return await agent.ask(question)

            state["run"] = run
            try:
                yield
            finally:
                shutdown_tracing()

    app = FastAPI(title="LegacyBridge", version="0.1.0", lifespan=lifespan)

    def authorize(request: Request) -> None:
        if not secret:
            raise HTTPException(503, "the approval API is disabled: LB_API_TOKEN is not set")
        scheme, _, supplied = request.headers.get("authorization", "").partition(" ")
        if scheme.lower() != "bearer" or not hmac.compare_digest(
            supplied.strip().encode(), secret.encode()
        ):
            raise HTTPException(
                401, "invalid or missing bearer token", {"WWW-Authenticate": "Bearer"}
            )

    @app.exception_handler(ApprovalError)
    async def approval_error(_: Request, error: ApprovalError) -> JSONResponse:
        return JSONResponse(
            {"error": error.code, "message": str(error)}, status_code=error.http_status
        )

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/ask", response_model=AgentResult)
    async def ask(body: AskRequest) -> AgentResult:
        profile = body.profile or os.environ.get("LB_DEFAULT_PROFILE", "local-qwen")
        run = state.get("run")
        if run is None:
            raise HTTPException(503, "the agent is not started yet")
        try:
            return await run(body.question, profile, body.verify)
        except ProfileError as error:
            code = 422 if "unknown profile" in str(error) else 503
            raise HTTPException(code, str(error)) from error
        except LLMError as error:
            raise HTTPException(502, str(error)) from error

    @app.get("/approvals", dependencies=[Depends(authorize)])
    async def list_approvals(status: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        return await anyio.to_thread.run_sync(lambda: service.list(status, limit))

    @app.get("/approvals/{request_id}", dependencies=[Depends(authorize)])
    async def get_approval(request_id: int) -> dict[str, Any]:
        return await anyio.to_thread.run_sync(service.get, request_id)

    @app.post("/approvals/{request_id}", dependencies=[Depends(authorize)])
    async def decide(request_id: int, body: Decision) -> dict[str, Any]:
        return await anyio.to_thread.run_sync(
            lambda: service.decide(request_id, body.decision == "approve", body.reviewer, body.note)
        )

    return app
