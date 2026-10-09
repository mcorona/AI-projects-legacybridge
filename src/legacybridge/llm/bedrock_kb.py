"""Amazon Bedrock Knowledge Bases as a documentation search (ADR-014), through ``Retrieve``.

The knowledge base is defined in the CDK stack; this module only queries it. It is switched on by
``LB_KB_ID`` and is absent otherwise.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any

import boto3
from botocore.config import Config

from legacybridge.semantic.knowledge import Passage

MAX_QUERY_CHARS = 1000
MAX_PASSAGE_CHARS = 600


def source_name(location: Mapping[str, Any]) -> str:
    """The file name of a retrieved passage (the part after the last slash of its S3 URI)."""
    uri = location.get("s3Location", {}).get("uri", "") or location.get("webLocation", {}).get(
        "url", ""
    )
    return uri.rsplit("/", 1)[-1] or "unknown"


class BedrockKnowledgeBase:
    def __init__(self, knowledge_base_id: str, *, client: Any, top_k: int = 3):
        self.knowledge_base_id, self.top_k, self._client = knowledge_base_id, top_k, client

    def retrieve(self, query: str) -> list[Passage]:
        response = self._client.retrieve(
            knowledgeBaseId=self.knowledge_base_id,
            retrievalQuery={"text": query[:MAX_QUERY_CHARS]},
            retrievalConfiguration={"vectorSearchConfiguration": {"numberOfResults": self.top_k}},
        )
        passages = []
        for hit in response.get("retrievalResults", []):
            text = (hit.get("content", {}).get("text") or "").strip()
            if text:
                passages.append(
                    Passage(
                        text=text[:MAX_PASSAGE_CHARS],
                        source=source_name(hit.get("location", {})),
                        score=hit.get("score"),
                    )
                )
        return passages


def kb_from_env(
    env: Mapping[str, str] | None = None, client: Any = None
) -> BedrockKnowledgeBase | None:
    """The knowledge base named by ``LB_KB_ID``, or None when it is not set."""
    env = os.environ if env is None else env
    knowledge_base_id = env.get("LB_KB_ID", "").strip()
    if not knowledge_base_id:
        return None
    if client is None:
        session = boto3.Session(
            profile_name=env.get("AWS_PROFILE", "").strip() or None,
            region_name=env.get("AWS_REGION", "").strip() or "us-east-1",
        )
        client = session.client(
            "bedrock-agent-runtime",
            config=Config(retries={"total_max_attempts": 2}, read_timeout=15, connect_timeout=5),
        )
    return BedrockKnowledgeBase(knowledge_base_id, client=client)
