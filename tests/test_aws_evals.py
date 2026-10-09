"""The measurement of the guardrail and of the knowledge base (ADR-014), against doubles.

The probe files are fixed before any measurement; these tests check that they are well formed and
that the numbers are computed the way the report says. No AWS call is made.
"""

import asyncio
import json

from evals import guardrails, kb_eval
from evals.golden import load_golden
from legacybridge.agent.guards import GuardVerdict
from legacybridge.semantic.knowledge import Passage

PROBES = guardrails.load_probes()


# --------------------------------------------------------------------------- the probe files
def test_the_guardrail_probes_are_enough_unique_and_split_by_what_is_expected():
    attacks = PROBES["attacks"]
    ids = [a["id"] for a in attacks] + [o["id"] for o in PROBES["outputs"]]
    assert len(ids) == len(set(ids)) and len(attacks) >= 20
    assert {a["set"] for a in attacks} == set(guardrails.ATTACK_SETS)
    assert sum(a["set"] == "refuse_expected" for a in attacks) >= 16
    assert all(a["text"].strip() and a["category"] for a in attacks)


def test_the_output_probes_have_pii_and_clean_texts_and_a_reason_each():
    outputs = PROBES["outputs"]
    assert (
        sum(o["should_intervene"] for o in outputs) >= 3
        and sum(not o["should_intervene"] for o in outputs) >= 3
    )
    assert all(isinstance(o["should_intervene"], bool) and o["why"] for o in outputs)


def test_a_company_rfc_is_a_clean_output_and_a_persons_is_not():
    by_id = {o["id"]: o for o in PROBES["outputs"]}
    assert by_id["GR-P01"]["should_intervene"] and not by_id["GR-P02"]["should_intervene"]


def test_the_knowledge_probes_are_twenty_with_acceptable_documents_that_are_uploaded():
    queries = kb_eval.load_queries()
    assert len(queries) == 20 and len({q["id"] for q in queries}) == 20
    from scripts.aws_post_deploy import DOCUMENTS

    uploaded = {name for _, name in DOCUMENTS}
    assert all(q["acceptable"] and set(q["acceptable"]) <= uploaded for q in queries)


# --------------------------------------------------------------------------- the guardrail run
class ScriptedGuard:
    """Blocks inputs that contain 'BLOCK', masks outputs that contain '@', touches nothing else."""

    async def check_input(self, text):
        return (
            GuardVerdict("blocked", text, ("PROMPT_ATTACK",))
            if "BLOCK" in text
            else GuardVerdict("none", text)
        )

    async def check_output(self, text):
        return (
            GuardVerdict("masked", "x", ("PII:EMAIL",))
            if "@" in text
            else GuardVerdict("none", text)
        )


def probes():
    return {
        "attacks": [
            {"id": "A1", "category": "injection", "set": "refuse_expected", "text": "BLOCK me"},
            {
                "id": "A2",
                "category": "injection",
                "set": "refuse_expected",
                "text": "slips through",
            },
            {
                "id": "A3",
                "category": "prompt_extraction",
                "set": "refuse_expected",
                "text": "BLOCK too",
            },
            {
                "id": "B1",
                "category": "dml",
                "set": "contained_elsewhere",
                "text": "delete everything",
            },
        ],
        "outputs": [
            {"id": "P1", "text": "write to a@b.com", "should_intervene": True, "why": "mail"},
            {"id": "P2", "text": "a phone 5512345678", "should_intervene": True, "why": "phone"},
            {"id": "P3", "text": "42 clients", "should_intervene": False, "why": "count"},
            {
                "id": "P4",
                "text": "mail c@d.org here",
                "should_intervene": False,
                "why": "wrongly touched",
            },
        ],
    }


def run(**kw):
    questions = kw.pop("questions", [("Q1", "how many?"), ("Q2", "BLOCK this legit one")])
    return asyncio.run(
        guardrails.measure(ScriptedGuard(), probes(), questions, kw.pop("answers", []))
    )


def test_each_group_is_counted_with_its_own_denominator():
    s = guardrails.summarize(
        run(
            answers=[
                ("run:A", "plain"),
                ("run:B", "mail a@b.com"),
                ("run:C", "c@d.com and e@f.com"),
            ]
        )
    )
    assert (s["attacks_refused"]["k"], s["attacks_refused"]["n"]) == (2, 3) and s[
        "attacks_missed"
    ] == ["A2"]
    assert (
        s["attacks_refused_by_category"]["injection"]["k"] == 1
        and s["attacks_refused_by_category"]["prompt_extraction"]["k"] == 1
    )
    assert (s["contained_elsewhere_blocked"]["k"], s["contained_elsewhere_blocked"]["n"]) == (0, 1)
    assert (s["legit_blocked"]["k"], s["legit_blocked"]["n"]) == (1, 2) and s[
        "legit_blocked_ids"
    ] == ["Q2"]
    assert (s["output_pii_caught"]["k"], s["output_pii_caught"]["n"]) == (1, 2)
    assert (s["output_clean_touched"]["k"], s["output_clean_touched"]["n"]) == (1, 2)
    assert s["output_probe_misses"] == ["P2", "P4"]
    assert (s["answers_touched"]["k"], s["answers_touched"]["n"]) == (2, 3)
    assert s["answer_findings"] == {"PII:EMAIL": 2}, "a finding counts once per answer"


def test_the_report_gives_every_rate_with_its_interval_and_names_what_it_missed():
    text = guardrails.render(guardrails.summarize(run()), "t1", "`gr-1` version 2")
    assert "Guardrail measurement t1" in text and "`gr-1` version 2" in text
    assert "0.67 (2/3; CI" in text and "Missed: A2." in text
    assert "Golden questions blocked: Q2." in text and "not** tuned" in text
    assert "contained elsewhere" in text and "Delivered answers" not in text, "no answers, no row"


def test_the_legitimate_traffic_is_the_whole_golden_set():
    assert len(load_golden()) == 80


def test_stored_answers_are_read_from_the_runs_and_only_delivered_ones(tmp_path):
    from tests.support.records import rec

    path = tmp_path / "eval-t-p-full.jsonl"
    rows = [rec("SIMPLE-001", "correct"), rec("SIMPLE-002", "loud_failure", status="abstained")]
    path.write_text("\n".join(r.model_dump_json() for r in rows) + "\n")
    assert guardrails.delivered_answers([path]) == [("eval-t-p-full:SIMPLE-001", "a")]


def test_the_command_refuses_to_run_without_a_guardrail(monkeypatch, capsys):
    monkeypatch.delenv("LB_GUARDRAIL_ID", raising=False)
    monkeypatch.setattr("legacybridge.env.load_env", lambda: None)
    assert guardrails.main(["--run-id", "x"]) == 1
    assert "LB_GUARDRAIL_ID" in capsys.readouterr().err


def test_the_raw_outcomes_serialize_to_json():
    from dataclasses import asdict

    assert json.dumps([asdict(o) for o in run()])


# --------------------------------------------------------------------------- the knowledge base run
class ScriptedKb:
    def __init__(self, table):
        self.table = table

    def retrieve(self, query):
        return [Passage("t", s) for s in self.table.get(query, [])]


def test_a_query_hits_when_an_acceptable_document_is_in_the_top_three_only():
    queries = [
        {"id": "K1", "query": "a", "acceptable": ["ontology.md"]},
        {"id": "K2", "query": "b", "acceptable": ["ontology.md"]},
        {"id": "K3", "query": "c", "acceptable": ["ontology.md"]},
    ]
    kb = ScriptedKb(
        {"a": ["schema.txt", "ontology.md"], "b": ["x", "y", "z", "ontology.md"], "c": []}
    )
    results = kb_eval.measure(kb, queries)
    assert [r["hit"] for r in results] == [True, False, False]
    text = kb_eval.render(results, "t1")
    assert "hit@3: 0.33 (1/3" in text and "nothing" in text
    assert "not that this layer is needed" in text


def test_the_knowledge_command_refuses_to_run_without_a_knowledge_base(monkeypatch, capsys):
    monkeypatch.delenv("LB_KB_ID", raising=False)
    monkeypatch.setattr("legacybridge.env.load_env", lambda: None)
    assert kb_eval.main(["--run-id", "x"]) == 1
    assert "LB_KB_ID" in capsys.readouterr().err
