"""Amazon Bedrock Guardrails as a ``TextGuard`` (ADR-014), through ``ApplyGuardrail``.

``ApplyGuardrail`` judges a text without calling a model, so the same guardrail serves the question
(source ``INPUT``) and the answer (source ``OUTPUT``). The guardrail itself is defined in the CDK
stack; this module only calls it. It is switched on by ``LB_GUARDRAIL_ID`` and is absent otherwise.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Mapping
from typing import Any

from legacybridge.agent.guards import GuardVerdict
from legacybridge.llm.bedrock import make_runtime_client

MAX_BLOCK_CHARS = 20_000  # one text block per call stays well under the service limit


def findings(assessments: list[dict[str, Any]]) -> list[str]:
    """Readable labels for what the guardrail acted on: ``PROMPT_ATTACK``, ``PII:EMAIL``..."""
    out: list[str] = []
    for a in assessments:
        for f in a.get("contentPolicy", {}).get("filters", []):
            if f.get("action") != "NONE":
                out.append(f.get("type", "CONTENT"))
        for t in a.get("topicPolicy", {}).get("topics", []):
            out.append(f"TOPIC:{t.get('name')}")
        sensitive = a.get("sensitiveInformationPolicy", {})
        out += [f"PII:{e.get('type')}" for e in sensitive.get("piiEntities", [])]
        out += [f"REGEX:{r.get('name')}" for r in sensitive.get("regexes", [])]
        out += [f"WORD:{w.get('match')}" for w in a.get("wordPolicy", {}).get("customWords", [])]
    return out


def _blocked(assessments: list[dict[str, Any]]) -> bool:
    """True when any policy blocked (rather than only anonymized) something."""

    def actions(node: Any):
        if isinstance(node, dict):
            if "action" in node:
                yield node["action"]
            for value in node.values():
                yield from actions(value)
        elif isinstance(node, list):
            for item in node:
                yield from actions(item)

    return any(a == "BLOCKED" for a in actions(assessments))


class BedrockGuardrail:
    def __init__(self, guardrail_id: str, version: str = "DRAFT", *, client: Any):
        self.guardrail_id, self.version, self._client = guardrail_id, version, client

    async def check_input(self, text: str) -> GuardVerdict:
        return await self._apply(text, "INPUT")

    async def check_output(self, text: str) -> GuardVerdict:
        return await self._apply(text, "OUTPUT")

    async def _apply(self, text: str, source: str) -> GuardVerdict:
        pieces = [text[i : i + MAX_BLOCK_CHARS] for i in range(0, len(text), MAX_BLOCK_CHARS)]
        verdicts = [await self._apply_block(p, source) for p in pieces or [""]]
        labels = tuple(dict.fromkeys(f for v in verdicts for f in v.findings))
        if any(v.action == "blocked" for v in verdicts):
            return GuardVerdict("blocked", text, labels)
        if any(v.action == "masked" for v in verdicts):
            return GuardVerdict("masked", "".join(v.text for v in verdicts), labels)
        return GuardVerdict("none", text, labels)

    async def _apply_block(self, text: str, source: str) -> GuardVerdict:
        response = await asyncio.to_thread(
            self._client.apply_guardrail,
            guardrailIdentifier=self.guardrail_id,
            guardrailVersion=self.version,
            source=source,
            content=[{"text": {"text": text}}],
        )
        if response.get("action") != "GUARDRAIL_INTERVENED":
            return GuardVerdict("none", text)
        assessments = response.get("assessments") or []
        labels = tuple(findings(assessments))
        if _blocked(assessments):
            return GuardVerdict("blocked", text, labels)
        outputs = response.get("outputs") or []
        return GuardVerdict("masked", outputs[0].get("text", text) if outputs else text, labels)


def guard_from_env(
    env: Mapping[str, str] | None = None, client: Any = None
) -> BedrockGuardrail | None:
    """The guardrail named by ``LB_GUARDRAIL_ID``, or None when it is not set."""
    env = os.environ if env is None else env
    guardrail_id = env.get("LB_GUARDRAIL_ID", "").strip()
    if not guardrail_id:
        return None
    client = client or make_runtime_client(
        env.get("AWS_REGION", "").strip() or "us-east-1",
        env.get("AWS_PROFILE", "").strip() or None,
        timeout_s=30,
        max_attempts=3,
    )
    return BedrockGuardrail(
        guardrail_id, env.get("LB_GUARDRAIL_VERSION", "DRAFT").strip(), client=client
    )
