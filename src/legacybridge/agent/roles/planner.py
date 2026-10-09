"""planner: decides query / clarify / abstain / write. It has no tools (ADR-002)."""

from __future__ import annotations

import json
from typing import Literal

from pydantic import BaseModel, ValidationError

from legacybridge.agent.events import Recorder
from legacybridge.agent.prompt_loader import load_prompt
from legacybridge.llm.base import InvalidModelOutput, LLMClient, LLMError, Message


class PlannerDecision(BaseModel):
    action: Literal["query", "clarify", "abstain", "write"]
    language: str = "es"
    intent: str = ""
    clarification: str | None = None
    reason: str | None = None


def extract_json(text: str) -> dict:
    """The first balanced JSON object in ``text`` (models wrap it in prose or code fences)."""
    start = text.find("{")
    while start != -1:
        depth, in_string, escaped = 0, False, False
        for i in range(start, len(text)):
            ch = text[i]
            if in_string:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == '"':
                    in_string = False
            elif ch == '"':
                in_string = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        parsed = json.loads(text[start : i + 1])
                    except json.JSONDecodeError:
                        break
                    if isinstance(parsed, dict):
                        return parsed
                    break
        start = text.find("{", start + 1)
    raise InvalidModelOutput("no JSON object in the model reply")


async def run_planner(
    llm: LLMClient, rec: Recorder, question: str, digest_text: str, semantic: bool = True
) -> PlannerDecision:
    system = load_prompt("planner.md", semantic).replace("{digest}", digest_text)
    messages = [Message.system(system), Message.user(question)]
    last_error: Exception | None = None
    for _ in range(2):  # one retry with a reminder, then give up
        with rec.llm_call("planner", llm, messages) as call:
            try:
                response = await llm.chat(messages)
            except LLMError as error:
                call.fail(error)
                raise
            call.ok(response)
        try:
            decision = PlannerDecision.model_validate(extract_json(response.text))
        except (InvalidModelOutput, ValidationError) as error:
            last_error = error
            messages += [
                Message.assistant(response.text),
                Message.user("Reply with ONE JSON object in the format described, nothing else."),
            ]
            continue
        if decision.language not in ("es", "en"):
            decision.language = "es"
        return decision
    raise InvalidModelOutput(f"planner reply was not usable: {last_error}")
