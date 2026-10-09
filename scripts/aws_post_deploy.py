"""After ``cdk deploy`` (ADR-014): build the database, upload the documents, index them.

    uv run python -m scripts.aws_post_deploy                  # reads infra/cdk-outputs.json
    uv run python -m scripts.aws_post_deploy --skip-kb

The stack creates empty things: an RDS instance with no tables and a knowledge base with no
documents. This script fills them. It is safe to repeat: the database initializer leaves an
initialized database alone, and the ingestion job only reads what changed.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import boto3

ROOT = Path(__file__).resolve().parents[1]
OUTPUTS = ROOT / "infra" / "cdk-outputs.json"
# (source file, name in the bucket). The schema is plain SQL, which the knowledge base cannot read
# as such, so it goes up as text.
DOCUMENTS = [
    (ROOT / "docs" / "legacy-defects.md", "legacy-defects.md"),
    (ROOT / "docs" / "ontology.md", "ontology.md"),
    (ROOT / "db" / "init" / "01_facturacion.sql", "schema-facturacion.txt"),
]
POLL_S = 5
INGEST_TIMEOUT_S = 600


def load_outputs(path: Path = OUTPUTS) -> dict[str, str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return next(iter(data.values()))  # one stack per file


def initialize_database(lam, function_name: str) -> dict:
    response = lam.invoke(FunctionName=function_name, Payload=b"{}")
    body = json.loads(response["Payload"].read() or b"null")
    if response.get("FunctionError"):
        raise RuntimeError(f"the database initializer failed: {body}")
    return body


def upload_documents(s3, bucket: str, documents=DOCUMENTS) -> list[str]:
    names = []
    for source, name in documents:
        s3.put_object(Bucket=bucket, Key=f"docs/{name}", Body=source.read_bytes())
        names.append(name)
    return names


def ingest(
    agent, knowledge_base_id: str, data_source_id: str, *, sleep=time.sleep, clock=time.monotonic
) -> dict:
    job = agent.start_ingestion_job(knowledgeBaseId=knowledge_base_id, dataSourceId=data_source_id)
    job_id = job["ingestionJob"]["ingestionJobId"]
    deadline = clock() + INGEST_TIMEOUT_S
    while True:
        state = agent.get_ingestion_job(
            knowledgeBaseId=knowledge_base_id, dataSourceId=data_source_id, ingestionJobId=job_id
        )["ingestionJob"]
        if state["status"] in ("COMPLETE", "FAILED", "STOPPED"):
            return state
        if clock() > deadline:
            raise TimeoutError(
                f"ingestion job {job_id} still {state['status']} after {INGEST_TIMEOUT_S} s"
            )
        sleep(POLL_S)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--outputs", type=Path, default=OUTPUTS)
    parser.add_argument("--skip-kb", action="store_true")
    args = parser.parse_args(argv)

    out = load_outputs(args.outputs)
    session = boto3.Session(profile_name=os.environ.get("AWS_PROFILE") or None)
    print("database:", initialize_database(session.client("lambda"), out["DbInitFunctionName"]))
    if not args.skip_kb:
        names = upload_documents(session.client("s3"), out["DocsBucketName"])
        print("uploaded:", ", ".join(names))
        state = ingest(session.client("bedrock-agent"), out["KnowledgeBaseId"], out["DataSourceId"])
        print("ingestion:", state["status"], state.get("statistics", {}))
        if state["status"] != "COMPLETE":
            print(state.get("failureReasons", []), file=sys.stderr)
            return 1
    print("API:", out["ApiUrl"])
    print(
        f"token: aws secretsmanager get-secret-value --secret-id {out['AskTokenSecretName']} "
        "--query SecretString --output text"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
