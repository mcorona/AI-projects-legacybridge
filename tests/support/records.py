"""Builds labelled evaluation records by hand, so a test knows every figure it expects."""

from evals.golden import load_golden
from evals.runner import Record
from legacybridge.agent.schemas import AgentResult, Evidence, Usage

GOLDEN = {c.id: c for c in load_golden()}


def rec(case_id, kind, *, status="answered", confidence=0.9, risk=0.0, ms=1000.0,
        tin=100, tout=50, violations=(), has_evidence=True, calls=3):  # fmt: skip
    case = GOLDEN[case_id]
    evidence = (
        Evidence(query_id="q1", sql="SELECT 1", columns=["n"], rows=[[1]], row_count=1,
                 truncated=False, violations=list(violations))
        if has_evidence else None
    )  # fmt: skip
    result = AgentResult(
        status=status, question="q", answer="a", confidence=confidence, swar_risk=risk,
        evidence=evidence, usage=Usage(llm_calls=calls, input_tokens=tin, output_tokens=tout, ms=ms),
    )  # fmt: skip
    return Record(case_id=case_id, category=case.category.value, variant="live", profile="p",
                  result=result, kind=kind, reason="r")  # fmt: skip
