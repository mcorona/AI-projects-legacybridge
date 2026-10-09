"""Where the asynchronous API keeps a question while a worker answers it (ADR-014).

``POST /ask`` stores a pending job and starts a worker; ``GET /ask/{id}`` reads the job. The store
is a small contract with two implementations: memory (tests and local use) and DynamoDB (AWS).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

Status = Literal["pending", "running", "done", "failed"]
TTL_S = 24 * 3600  # DynamoDB deletes a job a day after it was created


@dataclass
class Job:
    id: str
    question: str
    profile: str
    verify: bool = True
    status: Status = "pending"
    created: float = field(default_factory=time.time)
    result: str | None = None  # the AgentResult as JSON, once done
    error: str | None = None


class JobStore(Protocol):
    def create(self, job: Job) -> None: ...

    def get(self, job_id: str) -> Job | None: ...

    def update(self, job_id: str, **fields: Any) -> None: ...


class MemoryJobStore:
    def __init__(self) -> None:
        self.jobs: dict[str, Job] = {}

    def create(self, job: Job) -> None:
        self.jobs[job.id] = job

    def get(self, job_id: str) -> Job | None:
        return self.jobs.get(job_id)

    def update(self, job_id: str, **fields: Any) -> None:
        for key, value in fields.items():
            setattr(self.jobs[job_id], key, value)


class DynamoJobStore:
    """One item per job in an on-demand table keyed by ``id``. Written with the low-level client so
    every attribute type is explicit."""

    STRINGS = ("question", "profile", "status", "result", "error")

    def __init__(self, table: str, client: Any):
        self.table, self._client = table, client

    def create(self, job: Job) -> None:
        item: dict[str, Any] = {
            "id": {"S": job.id},
            "verify": {"BOOL": job.verify},
            "created": {"N": str(int(job.created))},
            "ttl": {"N": str(int(job.created) + TTL_S)},
        }
        item |= {k: {"S": getattr(job, k)} for k in self.STRINGS if getattr(job, k) is not None}
        # a job id is never reused, so refusing an overwrite catches a bug rather than hiding it
        self._client.put_item(
            TableName=self.table, Item=item, ConditionExpression="attribute_not_exists(id)"
        )

    def get(self, job_id: str) -> Job | None:
        out = self._client.get_item(
            TableName=self.table, Key={"id": {"S": job_id}}, ConsistentRead=True
        )
        item = out.get("Item")
        if not item:
            return None
        text = {k: item[k]["S"] for k in self.STRINGS if k in item}
        return Job(
            id=job_id,
            question=text.get("question", ""),
            profile=text.get("profile", ""),
            verify=item.get("verify", {}).get("BOOL", True),
            status=text.get("status", "pending"),  # type: ignore[arg-type]
            created=float(item["created"]["N"]),
            result=text.get("result"),
            error=text.get("error"),
        )

    def update(self, job_id: str, **fields: Any) -> None:
        names = {f"#{k}": k for k in fields}
        values = {f":{k}": {"S": str(v)} for k, v in fields.items()}
        assignments = ", ".join(f"#{k} = :{k}" for k in fields)
        self._client.update_item(
            TableName=self.table,
            Key={"id": {"S": job_id}},
            UpdateExpression=f"SET {assignments}",
            ExpressionAttributeNames=names,
            ExpressionAttributeValues=values,
            ConditionExpression="attribute_exists(id)",
        )
