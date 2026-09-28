"""Cascada de costo a nivel de RESPUESTA (Fase 5, ADR-007).

El agente completo corre con el nivel más barato (Qwen local); si el resultado no pasa la política
de `config/models.yaml` (cascade.escalate_when), la pregunta se repite desde cero con el siguiente
nivel (Bedrock Haiku 4.5). Es la adaptación a nivel de agente de `CascadeRouter.run(fn, accept)`
de inventory-copilot.

- Cada nivel es un `Agent` con proveedor fijo: una conversación nunca mezcla proveedores.
- No se escalan decisiones deterministas o seguras: rechazos, propuestas de cambio pendientes y
  entradas bloqueadas por guardrails (escalarlas solo costaría dinero).
- Tokens, costo, latencia y traza de llamadas se ACUMULAN entre niveles: es el costo real.
- `escalations` registra de qué nivel se escaló y por qué.
"""
from __future__ import annotations

import time

from legacybridge.agent.core import Agent, AgentResult
from legacybridge.agent.tools import build_toolbox
from legacybridge.llm.router import load_config

NO_ESCALATE_OUTCOMES = ("refusal", "proposal")


def escalation_reason(res: AgentResult, policy: dict) -> str | None:
    """Motivo para escalar, o None si el resultado es aceptable."""
    if res.stop_reason in ("blocked_input", "confirmation_required") or res.outcome in NO_ESCALATE_OUTCOMES:
        return None
    if res.stop_reason == "llm_error":
        return "provider_error" if policy.get("provider_error", True) else None
    if res.stop_reason in ("sql_retries_exhausted", "max_steps"):
        return "unfinished" if policy.get("unfinished", True) else None
    if res.sql_failures >= policy.get("guard_failures", 2):
        return "sql_failures"
    if res.outcome == "cannot_answer" and policy.get("cannot_answer", True):
        return "cannot_answer"
    if res.outcome == "answer":
        if not res.evidence and policy.get("answer_without_evidence", True):
            return "no_evidence"
        if res.confidence < policy.get("min_confidence", 0.6):
            return "low_confidence"
    return None


class CascadeAgent:
    def __init__(self, tiers: list[str] | None = None, policy: dict | None = None, telemetry="env",
                 **agent_kwargs):
        conf = load_config()["cascade"]
        self.tiers = tiers or conf["agent_tiers"]
        self.policy = policy if policy is not None else conf["escalate_when"]
        agent_kwargs.setdefault("toolbox", build_toolbox())      # una sola toolbox para todos los niveles
        # los niveles no escriben telemetría: la cascada escribe un registro agregado por turno
        self.agents = [Agent(provider=t, telemetry=None, **agent_kwargs) for t in self.tiers]
        if telemetry == "env":
            from legacybridge.telemetry import sink_from_env
            telemetry = sink_from_env()
        self.telemetry = telemetry
        self._last: Agent | None = None
        first = self.agents[0]    # atributos que el harness usa en la huella
        self.toolbox, self.guardrails = first.toolbox, first.guardrails
        self.max_steps, self.max_sql_retries, self.max_tokens = first.max_steps, first.max_sql_retries, first.max_tokens
        self.provider = "cascade"

    def ask(self, question: str) -> AgentResult:
        t0 = time.perf_counter()
        spent = {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0, "llm_calls": 0}
        trace, providers, escalations = [], {}, []
        res = None
        for i, agent in enumerate(self.agents):
            res = agent.ask(question)
            self._last = agent
            for k in spent:
                spent[k] += getattr(res, k)
            trace += res.llm_trace
            for p, n in res.providers.items():
                providers[p] = providers.get(p, 0) + n
            reason = escalation_reason(res, self.policy)
            if reason is None or i == len(self.agents) - 1:
                break
            escalations.append({"from": self.tiers[i], "to": self.tiers[i + 1], "reason": reason,
                                "stop_reason": res.stop_reason, "outcome": res.outcome,
                                "confidence": res.confidence})
        for k, v in spent.items():
            setattr(res, k, v)
        res.llm_trace, res.providers, res.escalations = trace, providers, escalations
        res.latency_s = round(time.perf_counter() - t0, 3)
        if self.telemetry is not None:
            from legacybridge.telemetry import turn_record
            self.telemetry.write(turn_record(res, provider="cascade",
                                             extra={"escalations": escalations, "final_tier": self._tier(res)}))
        return res

    def resume(self, res: AgentResult, approve: bool, user: str | None = None) -> AgentResult:
        if self._last is None:
            raise ValueError("no hay ninguna propuesta pendiente de confirmación")
        return self._last.resume(res, approve=approve, user=user)

    def _tier(self, res: AgentResult) -> str:
        return self.tiers[len(res.escalations)]
