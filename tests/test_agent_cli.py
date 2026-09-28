"""Presentación del resultado del agente en la CLI."""
import json

from legacybridge.agent import __main__ as cli
from legacybridge.agent.core import AgentResult, QueryEvidence, Step


def result(rows=12):
    return AgentResult(
        "¿Cuántos?", answer="Hay 1 cliente activo.", outcome="answer", confidence=0.8,
        model_confidence=0.9, caveats=["NULL = inactivo (D4)"], stop_reason="submitted",
        evidence=[QueryEvidence("SELECT … LIMIT 100", ["cliemae"], ["clicve", "clinom"],
                                [[f"C{i:05}", "X"] for i in range(rows)], rows, False)],
        steps=[Step("run_query", {"sql": "…"}, True, 3.0, f"{rows} filas"),
               Step("submit_answer", {}, True, 0.0, "answer")],
        llm_calls=2, input_tokens=300, output_tokens=40, cost_usd=0.0, latency_s=2.5,
        providers={"local": 2}, sql_failures=1)


def test_render_shows_answer_evidence_trace_and_cost():
    text = cli.render(result())
    assert "Hay 1 cliente activo." in text and "confianza=0.80 (modelo 0.90)" in text
    assert "SQL: SELECT … LIMIT 100" in text and "Tablas: cliemae" in text
    assert "C00009" in text and "C00010" not in text and "… 2 filas más" in text
    assert "✓ run_query" in text and "local×2" in text and "reintentos SQL 1" in text


def test_main_json_and_exit_code(monkeypatch, capsys):
    class FakeAgent:
        def __init__(self, provider=None):
            assert provider == "bedrock"

        def ask(self, q):
            assert q == "hola mundo"
            return result(1)
    monkeypatch.setattr(cli, "Agent", FakeAgent)
    assert cli.main(["-p", "bedrock", "--json", "hola", "mundo"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["primary_evidence"]["tables"] == ["cliemae"] and out["outcome"] == "answer"
