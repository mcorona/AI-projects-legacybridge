"""The AWS runtime (ADR-014): job store, asynchronous front, worker, secrets and metrics. All against
doubles; nothing here calls AWS."""

import json

import pytest
from fastapi.testclient import TestClient

from legacybridge.agent.schemas import AgentResult, GuardrailRecord, Usage
from legacybridge.aws import secrets, worker
from legacybridge.aws.front import (
    DEADLINE_S,
    LambdaDispatcher,
    create_front_app,
    public_view,
)
from legacybridge.aws.jobs import TTL_S, DynamoJobStore, Job, MemoryJobStore
from legacybridge.observability import emf

TOKEN = "front-token-123"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def result(**kw):
    base = dict(status="answered", question="q", answer="a", confidence=0.9, profile="bedrock-claude",
                usage=Usage(llm_calls=4, tool_calls=2, input_tokens=2000, output_tokens=500, ms=8000.0),
                swar_risk=0.2)  # fmt: skip
    return AgentResult(**{**base, **kw})


# --------------------------------------------------------------------------- the job store
def test_the_memory_store_creates_reads_and_updates():
    store = MemoryJobStore()
    store.create(Job("a" * 32, "¿Cuántos?", "bedrock-claude"))
    store.update("a" * 32, status="done", result="{}")
    job = store.get("a" * 32)
    assert (job.status, job.result) == ("done", "{}") and store.get("b" * 32) is None


class FakeDynamo:
    def __init__(self):
        self.items, self.calls = {}, []

    def put_item(self, **kw):
        self.calls.append(("put", kw))
        self.items[kw["Item"]["id"]["S"]] = dict(kw["Item"])

    def get_item(self, **kw):
        self.calls.append(("get", kw))
        item = self.items.get(kw["Key"]["id"]["S"])
        return {"Item": item} if item else {}

    def update_item(self, **kw):
        self.calls.append(("update", kw))
        item = self.items[kw["Key"]["id"]["S"]]
        for name, value in kw["ExpressionAttributeValues"].items():
            item[name[1:]] = value


def test_the_dynamodb_store_round_trips_a_job_with_a_ttl_and_no_overwrite():
    client = FakeDynamo()
    store = DynamoJobStore("jobs", client)
    store.create(Job("c" * 32, "pregunta", "bedrock-claude", verify=False, created=1_000.9))
    put = client.calls[0][1]
    assert put["TableName"] == "jobs" and put["ConditionExpression"] == "attribute_not_exists(id)"
    assert put["Item"]["ttl"] == {"N": str(1_000 + TTL_S)} and put["Item"]["verify"] == {
        "BOOL": False
    }
    store.update("c" * 32, status="done", result='{"a": 1}')
    update = client.calls[1][1]
    assert update["UpdateExpression"] == "SET #status = :status, #result = :result"
    assert update["ConditionExpression"] == "attribute_exists(id)"
    job = store.get("c" * 32)
    assert (job.status, job.result, job.verify, job.created) == ("done", '{"a": 1}', False, 1000.0)
    assert client.calls[2][1]["ConsistentRead"] is True and store.get("d" * 32) is None


# --------------------------------------------------------------------------- the front
class Recorder:
    def __init__(self, fail=None):
        self.jobs, self.fail = [], fail

    def __call__(self, job):
        if self.fail:
            raise self.fail
        self.jobs.append(job)


def front(token=TOKEN, dispatch=None, now=lambda: 1000.0, **kw):
    store, dispatch = MemoryJobStore(), dispatch or Recorder()
    app = create_front_app(store, dispatch, token, now=now, **kw)
    return TestClient(app), store, dispatch


def test_the_front_refuses_to_start_without_a_token():
    with pytest.raises(RuntimeError, match="fail closed"):
        create_front_app(MemoryJobStore(), Recorder(), None)
    assert create_front_app(MemoryJobStore(), Recorder(), None, require_auth=False)


def test_a_question_is_accepted_with_202_stored_and_handed_to_the_worker():
    client, store, dispatch = front()
    out = client.post(
        "/ask", json={"question": "¿Cuántas facturas?", "verify": False}, headers=AUTH
    )
    assert out.status_code == 202
    body = out.json()
    assert (
        body["status"] == "pending"
        and body["poll"] == f"/ask/{body['id']}"
        and len(body["id"]) == 32
    )
    job = store.get(body["id"])
    assert (job.question, job.profile, job.verify, job.created) == (
        "¿Cuántas facturas?",
        "bedrock-claude",
        False,
        1000.0,
    )
    assert dispatch.jobs == [job]


def test_every_route_but_health_needs_the_bearer_token():
    client, store, dispatch = front()
    assert client.get("/health").json() == {"status": "ok"}
    for headers in ({}, {"Authorization": "Bearer nope"}, {"Authorization": f"Basic {TOKEN}"}):
        assert client.post("/ask", json={"question": "hola!"}, headers=headers).status_code == 401
        assert client.get("/ask/" + "a" * 32, headers=headers).status_code == 401
    assert store.jobs == {} and dispatch.jobs == []


def test_input_is_validated_before_anything_is_stored():
    client, store, _ = front()
    for body in ({"question": "a"}, {"question": "x" * 2001}, {"question": "hola!", "profile": "A;b"},
                 {"question": "hola!", "profile": "x" * 41}):  # fmt: skip
        assert client.post("/ask", json=body, headers=AUTH).status_code == 422
    assert store.jobs == {}


def test_a_worker_that_does_not_start_leaves_a_failed_job_not_a_pending_one():
    client, store, _ = front(dispatch=Recorder(fail=RuntimeError("throttled")))
    out = client.post("/ask", json={"question": "hola!"}, headers=AUTH)
    assert out.status_code == 502
    (job,) = store.jobs.values()
    assert job.status == "failed" and "throttled" in job.error


def test_a_job_is_polled_until_done_and_then_shows_the_result_without_events():
    client, store, _ = front()
    job_id = client.post("/ask", json={"question": "hola!"}, headers=AUTH).json()["id"]
    assert client.get(f"/ask/{job_id}", headers=AUTH).json()["status"] == "pending"
    store.update(job_id, status="done", result=result().model_dump_json(exclude={"events"}))
    done = client.get(f"/ask/{job_id}", headers=AUTH).json()
    assert (
        done["status"] == "done"
        and done["result"]["answer"] == "a"
        and "events" not in done["result"]
    )


def test_unknown_and_malformed_ids_are_404():
    client, _, _ = front()
    assert client.get("/ask/" + "f" * 32, headers=AUTH).status_code == 404
    assert client.get("/ask/not-a-job-id", headers=AUTH).status_code == 404


def test_a_job_whose_worker_vanished_reports_timed_out_instead_of_pending_forever():
    job = Job("a" * 32, "q", "p", status="running", created=0.0)
    assert public_view(job, now=DEADLINE_S - 1, deadline_s=DEADLINE_S)["status"] == "running"
    assert public_view(job, now=DEADLINE_S + 1, deadline_s=DEADLINE_S)["status"] == "timed_out"
    done = Job("a" * 32, "q", "p", status="done", created=0.0, result="{}")
    assert public_view(done, now=10**9, deadline_s=DEADLINE_S)["status"] == "done"


def test_the_dispatcher_starts_the_worker_asynchronously_with_the_job_and_nothing_else():
    class FakeLambda:
        def invoke(self, **kw):
            self.sent = kw

    fake = FakeLambda()
    LambdaDispatcher("worker-fn", fake)(Job("a" * 32, "pregunta", "bedrock-claude", verify=False))
    assert fake.sent["FunctionName"] == "worker-fn" and fake.sent["InvocationType"] == "Event"
    assert json.loads(fake.sent["Payload"]) == {
        "id": "a" * 32, "question": "pregunta", "profile": "bedrock-claude", "verify": False,
    }  # fmt: skip


# --------------------------------------------------------------------------- the worker
@pytest.mark.anyio
async def test_the_worker_runs_the_agent_stores_the_result_and_emits_one_metrics_record():
    store, seen, emitted = MemoryJobStore(), [], []
    store.create(Job("a" * 32, "q", "bedrock-claude"))

    async def runner(question, profile, verify):
        seen.append((question, profile, verify))
        return result()

    status = await worker.process(
        {"id": "a" * 32, "question": "¿Cuántas?", "profile": "bedrock-claude", "verify": False},
        store, runner, lambda r, **kw: emitted.append((r, kw)), lambda profile: (1.10, 5.50),
    )  # fmt: skip
    assert status == "done" and seen == [("¿Cuántas?", "bedrock-claude", False)]
    job = store.get("a" * 32)
    assert (
        job.status == "done"
        and json.loads(job.result)["answer"] == "a"
        and "events" not in json.loads(job.result)
    )
    assert len(emitted) == 1 and emitted[0][1] == {"price_in": 1.10, "price_out": 5.50}


@pytest.mark.anyio
async def test_a_worker_failure_is_stored_on_the_job_and_nothing_is_emitted():
    store, emitted = MemoryJobStore(), []
    store.create(Job("a" * 32, "q", "bedrock-claude"))

    async def runner(*args):
        raise RuntimeError("model access denied")

    status = await worker.process({"id": "a" * 32, "question": "q", "profile": "bedrock-claude"},
                                  store, runner, lambda *a, **k: emitted.append(a))  # fmt: skip
    job = store.get("a" * 32)
    assert status == "failed" and job.status == "failed" and "model access denied" in job.error
    assert emitted == []


def test_the_price_comes_from_the_profile_in_the_config(monkeypatch):
    monkeypatch.setenv("BEDROCK_CHAT_MODEL", "us.anthropic.claude-haiku-4-5-20251001-v1:0")
    assert worker.prices("bedrock-claude") == (1.10, 5.50)


# --------------------------------------------------------------------------- secrets
def test_a_dsn_percent_encodes_the_password_and_requires_tls():
    dsn = secrets.build_dsn(
        {"username": "lb_reader", "password": "p@ss/w:rd#"}, "db.example", "5432", "legacy"
    )
    assert dsn == "postgresql://lb_reader:p%40ss%2Fw%3Ard%23@db.example:5432/legacy?sslmode=require"


class FakeSecrets:
    def __init__(self, values):
        self.values, self.asked = values, []

    def get_secret_value(self, SecretId):
        self.asked.append(SecretId)
        return {"SecretString": self.values[SecretId]}


def test_dsns_come_from_the_secrets_of_the_roles_that_do_not_have_one_yet():
    env = {"LB_DB_HOST": "db.example", "LB_READER_SECRET": "r", "LB_PROPOSER_SECRET": "p",
           "LB_PROPOSER_DSN": "postgresql://already-set"}  # fmt: skip
    client = FakeSecrets({"r": json.dumps({"username": "lb_reader", "password": "x"})})
    assert secrets.load_runtime_env(env, client) == ["reader"]
    assert env["LB_READER_DSN"].startswith("postgresql://lb_reader:x@db.example:5432/legacy")
    assert env["LB_PROPOSER_DSN"] == "postgresql://already-set" and client.asked == ["r"]


def test_a_missing_host_is_an_error_and_no_secrets_means_nothing_to_do():
    assert secrets.load_runtime_env({}, FakeSecrets({})) == []
    with pytest.raises(RuntimeError, match="LB_DB_HOST"):
        secrets.load_runtime_env({"LB_READER_SECRET": "r"}, FakeSecrets({}))


def test_the_token_comes_from_the_environment_or_from_its_secret():
    assert secrets.read_token({"LB_ASK_TOKEN": "direct"}) == "direct"
    assert secrets.read_token({}) is None
    assert secrets.read_token({"LB_ASK_TOKEN_SECRET": "t"}, FakeSecrets({"t": " tok\n"})) == "tok"


# --------------------------------------------------------------------------- metrics (EMF)
def test_the_record_declares_every_metric_by_profile_and_only_the_question_count_by_status():
    record = emf.emf_record(result(), price_in=1.10, price_out=5.50, now_ms=123)
    everything, by_status = record["_aws"]["CloudWatchMetrics"]
    assert everything["Namespace"] == by_status["Namespace"] == "LegacyBridge"
    assert everything["Dimensions"] == [["Profile"]]
    assert by_status["Dimensions"] == [["Profile", "Status"]]
    names = [m["Name"] for m in everything["Metrics"]]
    assert record["_aws"]["Timestamp"] == 123 and all(n in record for n in names)
    assert {m["Name"]: m["Unit"] for m in everything["Metrics"]}["LatencyMs"] == "Milliseconds"
    assert [m["Name"] for m in by_status["Metrics"]] == ["Questions"]
    assert (record["Profile"], record["Status"]) == ("bedrock-claude", "answered")


def test_the_number_of_series_a_profile_creates_is_what_the_dashboard_needs_and_no_more():
    """Each series is billed by the month; repeating every metric per status was 55 and not 15."""
    assert emf.series_per_profile(statuses=4) == len(emf.UNITS) + 4 == 15


def test_the_cost_is_tokens_times_the_price_per_million_and_zero_when_unpriced():
    record = emf.emf_record(result(), price_in=1.10, price_out=5.50)
    assert record["CostUsd"] == pytest.approx((2000 * 1.10 + 500 * 5.50) / 1_000_000)
    assert emf.emf_record(result())["CostUsd"] == 0


def test_guardrail_interventions_are_counted_and_blocks_are_told_apart():
    plain = emf.metrics_for(result())
    assert (plain["GuardrailIntervened"], plain["GuardrailBlocked"], plain["Answered"]) == (0, 0, 1)
    masked = emf.metrics_for(result(guardrail=GuardrailRecord(action="masked")))
    assert (masked["GuardrailIntervened"], masked["GuardrailBlocked"]) == (1, 0)
    blocked = emf.metrics_for(
        result(status="abstained", guardrail=GuardrailRecord(action="blocked_input"))
    )
    assert (blocked["GuardrailIntervened"], blocked["GuardrailBlocked"], blocked["Answered"]) == (
        1,
        1,
        0,
    )


def test_the_record_carries_no_question_and_no_answer():
    text = json.dumps(emf.emf_record(result(question="¿RFC de Patricia?", answer="PERA800101ABC")))
    assert "Patricia" not in text and "PERA800101ABC" not in text


def test_emit_prints_one_json_line(capsys):
    emf.emit(result())
    line = capsys.readouterr().out.strip()
    assert "\n" not in line and json.loads(line)["Questions"] == 1


# --------------------------------------------------------------------------- the Lambda wrapper
def http_event(method, path, body=None, token=TOKEN):
    """What API Gateway (HTTP API, payload 2.0) sends to the function."""
    headers = {"content-type": "application/json"}
    if token:
        headers["authorization"] = f"Bearer {token}"
    return {
        "version": "2.0", "routeKey": f"{method} {path}", "rawPath": path, "rawQueryString": "",
        "headers": headers, "isBase64Encoded": False,
        "body": json.dumps(body) if body is not None else None,
        "requestContext": {
            "http": {"method": method, "path": path, "protocol": "HTTP/1.1", "sourceIp": "203.0.113.9", "userAgent": "curl"},
            "stage": "$default", "requestId": "r1", "domainName": "x.execute-api.us-east-1.amazonaws.com",
        },
    }  # fmt: skip


def test_an_api_gateway_event_reaches_the_app_and_the_response_has_the_shape_it_expects():
    from legacybridge.aws.front import make_handler

    store, dispatch = MemoryJobStore(), Recorder()
    call = make_handler(create_front_app(store, dispatch, TOKEN, now=lambda: 1000.0))
    out = call(http_event("POST", "/ask", {"question": "¿Cuántas facturas?"}), None)
    assert out["statusCode"] == 202 and out["headers"]["content-type"] == "application/json"
    body = json.loads(out["body"])
    assert body["status"] == "pending" and len(dispatch.jobs) == 1
    polled = call(http_event("GET", f"/ask/{body['id']}"), None)
    assert polled["statusCode"] == 200 and json.loads(polled["body"])["status"] == "pending"
    assert call(http_event("GET", "/ask/" + body["id"], token=None), None)["statusCode"] == 401
    assert call(http_event("GET", "/health", token=None), None)["statusCode"] == 200


def test_the_lambda_entry_point_builds_the_app_from_the_environment_once(monkeypatch):
    from legacybridge.aws import front

    built = []
    monkeypatch.setattr(front, "_handler", None)
    monkeypatch.setattr(front, "app_from_env", lambda: built.append(1) or create_front_app(
        MemoryJobStore(), Recorder(), TOKEN))  # fmt: skip
    assert front.handler(http_event("GET", "/health", token=None))["statusCode"] == 200
    assert front.handler(http_event("GET", "/health", token=None))["statusCode"] == 200
    assert built == [1]


def test_a_guardrail_that_failed_is_an_error_not_an_intervention():
    failed = emf.metrics_for(result(status="abstained", guardrail=GuardrailRecord(action="error")))
    assert (
        failed["GuardrailIntervened"],
        failed["GuardrailBlocked"],
        failed["GuardrailErrors"],
    ) == (0, 0, 1)
    masked = emf.metrics_for(result(guardrail=GuardrailRecord(action="masked")))
    assert masked["GuardrailErrors"] == 0 and emf.metrics_for(result())["GuardrailErrors"] == 0
