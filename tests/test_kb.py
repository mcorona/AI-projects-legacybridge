"""Documentation passages behind explain_business_term (ADR-014). No AWS call is made.

The ontology stays the authority: the knowledge base only speaks for a term the ontology does not
know, and the tool still works when the knowledge base is absent or down.
"""

import os

from legacybridge.llm.bedrock_kb import BedrockKnowledgeBase, kb_from_env, source_name
from legacybridge.mcp_server.tools import LegacyTools
from legacybridge.semantic.knowledge import Passage

UNKNOWN = "zzz-no-such-term-in-the-ontology"


class FakeKb:
    def __init__(self, passages=(), error=None):
        self.passages, self.error, self.queries = list(passages), error, []

    def retrieve(self, query):
        self.queries.append(query)
        if self.error:
            raise self.error
        return self.passages


def tools(kb=None):
    return LegacyTools(
        connect=lambda *a, **k: (_ for _ in ()).throw(AssertionError("no db")), knowledge_base=kb
    )


# --------------------------------------------------------------------------- the tool
def test_a_term_the_ontology_knows_never_asks_the_knowledge_base():
    kb = FakeKb([Passage("x", "doc.md")])
    out = tools(kb).explain_business_term("ventas")
    assert out["ok"] is True and out["source"] == "ontology" and kb.queries == []


def test_an_unknown_term_gets_passages_marked_as_documentation_not_as_a_definition():
    kb = FakeKb([Passage("FAC-03: the status column mixes ...", "legacy-defects.md", 0.71)])
    out = tools(kb).explain_business_term(UNKNOWN)
    assert out["ok"] is False and out["reason"] == "unknown_term", "it is still not an ontology hit"
    assert out["passages"] == [
        {
            "text": "FAC-03: the status column mixes ...",
            "source": "legacy-defects.md",
            "score": 0.71,
        }
    ]
    assert out["passages_source"] == "knowledge_base" and "not canonical definitions" in out["hint"]
    assert kb.queries == [UNKNOWN]


def test_without_a_knowledge_base_the_answer_is_what_it_always_was():
    assert "passages" not in tools().explain_business_term(UNKNOWN)


def test_a_knowledge_base_that_fails_does_not_break_the_tool():
    out = tools(FakeKb(error=RuntimeError("endpoint unreachable"))).explain_business_term(UNKNOWN)
    assert out["ok"] is False and out["reason"] == "unknown_term" and "passages" not in out


def test_a_knowledge_base_with_nothing_relevant_adds_nothing():
    out = tools(FakeKb([])).explain_business_term(UNKNOWN)
    assert "passages" not in out and "Documentation passages" not in out["hint"]


def test_the_environment_switches_the_knowledge_base_on(monkeypatch):
    monkeypatch.delenv("LB_KB_ID", raising=False)
    assert LegacyTools(connect=None).knowledge_base is None
    monkeypatch.setenv("LB_KB_ID", "KB123")
    # no real client: building one would resolve the developer's AWS credentials
    monkeypatch.setattr(
        "legacybridge.llm.bedrock_kb.kb_from_env",
        lambda: BedrockKnowledgeBase(os.environ["LB_KB_ID"], client=object()),
    )
    assert LegacyTools(connect=None).knowledge_base.knowledge_base_id == "KB123"


# --------------------------------------------------------------------------- the adapter
class FakeAgentRuntime:
    def __init__(self, results):
        self.results, self.requests = results, []

    def retrieve(self, **request):
        self.requests.append(request)
        return {"retrievalResults": self.results}


def hit(text, uri, score=0.5):
    return {"content": {"text": text}, "location": {"s3Location": {"uri": uri}}, "score": score}


def test_the_request_names_the_knowledge_base_and_asks_for_the_top_results():
    runtime = FakeAgentRuntime([])
    BedrockKnowledgeBase("KB1", client=runtime, top_k=2).retrieve("q" * 2000)
    sent = runtime.requests[0]
    assert sent["knowledgeBaseId"] == "KB1" and len(sent["retrievalQuery"]["text"]) == 1000
    assert sent["retrievalConfiguration"] == {"vectorSearchConfiguration": {"numberOfResults": 2}}


def test_passages_carry_text_source_file_and_score_and_empty_ones_are_dropped():
    runtime = FakeAgentRuntime([
        hit("a" * 900, "s3://bucket/docs/legacy-defects.md", 0.9), hit("  ", "s3://b/x.md"),
        {"content": {"text": "no location"}},
    ])  # fmt: skip
    out = BedrockKnowledgeBase("KB1", client=runtime).retrieve("fac")
    assert [(p.source, p.score, len(p.text)) for p in out] == [
        ("legacy-defects.md", 0.9, 600),
        ("unknown", None, 11),
    ]
    assert source_name({"webLocation": {"url": "https://x/y/page.html"}}) == "page.html"


def test_the_knowledge_base_exists_only_when_its_id_is_set():
    assert kb_from_env({}) is None and kb_from_env({"LB_KB_ID": " "}) is None
    assert kb_from_env({"LB_KB_ID": "KB9"}, client=object()).knowledge_base_id == "KB9"
