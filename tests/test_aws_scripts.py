"""The scripts that fill a fresh stack and check that destroy left nothing (ADR-014)."""

import io
import json

import pytest

from scripts import aws_leftovers, aws_post_deploy


# --------------------------------------------------------------------------- post deploy
class FakeLambda:
    def __init__(self, body, error=None):
        self.body, self.error, self.calls = body, error, []

    def invoke(self, **kw):
        self.calls.append(kw)
        out = {"Payload": io.BytesIO(json.dumps(self.body).encode())}
        if self.error:
            out["FunctionError"] = "Unhandled"
        return out


def test_the_database_initializer_is_invoked_and_its_answer_returned():
    lam = FakeLambda({"status": "initialized"})
    assert aws_post_deploy.initialize_database(lam, "fn") == {"status": "initialized"}
    assert lam.calls == [{"FunctionName": "fn", "Payload": b"{}"}]


def test_a_failing_initializer_stops_the_deploy_with_its_message():
    with pytest.raises(RuntimeError, match="boom"):
        aws_post_deploy.initialize_database(FakeLambda({"errorMessage": "boom"}, error=True), "fn")


def test_documents_go_under_docs_with_their_bucket_names(tmp_path):
    a, b = tmp_path / "a.md", tmp_path / "b.sql"
    a.write_text("# a")
    b.write_text("CREATE TABLE t();")

    class S3:
        def __init__(self):
            self.puts = []

        def put_object(self, **kw):
            self.puts.append(kw)

    s3 = S3()
    names = aws_post_deploy.upload_documents(s3, "bucket", [(a, "a.md"), (b, "b.txt")])
    assert names == ["a.md", "b.txt"]
    assert [(p["Bucket"], p["Key"], p["Body"]) for p in s3.puts] == [
        ("bucket", "docs/a.md", b"# a"), ("bucket", "docs/b.txt", b"CREATE TABLE t();"),
    ]  # fmt: skip


def test_the_committed_documents_and_the_schema_exist():
    for source, _ in aws_post_deploy.DOCUMENTS:
        assert source.exists(), source


class FakeAgent:
    def __init__(self, statuses):
        self.statuses, self.polls = list(statuses), 0

    def start_ingestion_job(self, **kw):
        return {"ingestionJob": {"ingestionJobId": "job-1"}}

    def get_ingestion_job(self, **kw):
        self.polls += 1
        status = self.statuses.pop(0)
        return {
            "ingestionJob": {"status": status, "statistics": {"numberOfNewDocumentsIndexed": 3}}
        }


def test_ingestion_is_polled_until_it_finishes():
    agent, sleeps = FakeAgent(["STARTING", "IN_PROGRESS", "COMPLETE"]), []
    state = aws_post_deploy.ingest(agent, "kb", "ds", sleep=sleeps.append)
    assert state["status"] == "COMPLETE" and agent.polls == 3 and sleeps == [5, 5]


def test_an_ingestion_that_never_ends_times_out_instead_of_hanging():
    ticks = iter(range(0, 10_000, 400))
    with pytest.raises(TimeoutError, match="job-1"):
        aws_post_deploy.ingest(
            FakeAgent(["IN_PROGRESS"] * 50),
            "kb",
            "ds",
            sleep=lambda s: None,
            clock=lambda: next(ticks),
        )


def test_outputs_are_read_from_the_single_stack_in_the_file(tmp_path):
    path = tmp_path / "out.json"
    path.write_text(json.dumps({"LegacyBridge": {"ApiUrl": "https://x/"}}))
    assert aws_post_deploy.load_outputs(path) == {"ApiUrl": "https://x/"}


# --------------------------------------------------------------------------- leftovers
class Cfn:
    def __init__(self, stacks):
        self.stacks = stacks

    def describe_stacks(self, StackName):
        if StackName not in self.stacks:
            raise RuntimeError(f"Stack with id {StackName} does not exist")
        return {"Stacks": [{"StackStatus": self.stacks[StackName]}]}


class Tagging:
    def __init__(self, pages):
        self.pages = list(pages)

    def get_resources(self, TagFilters, PaginationToken=""):
        assert TagFilters == [aws_leftovers.TAG]
        return self.pages.pop(0)


class Ec2:
    def __init__(self, nat=(), endpoints=()):
        self.nat, self.endpoints = nat, endpoints

    def describe_nat_gateways(self, **kw):
        return {"NatGateways": [{"NatGatewayId": n} for n in self.nat]}

    def describe_vpc_endpoints(self, **kw):
        return {"VpcEndpoints": [{"VpcEndpointId": e} for e in self.endpoints]}


def clients(cfn=None, tagging=None, ec2=None):
    return {
        "cloudformation": Cfn(cfn or {}),
        "tagging": Tagging(tagging or [{}]),
        "ec2": ec2 or Ec2(),
    }


def test_a_clean_account_has_no_leftovers():
    assert aws_leftovers.find(clients()) == {}


def test_a_remaining_stack_tagged_resources_and_hourly_billed_things_are_all_reported():
    found = aws_leftovers.find(clients(
        cfn={"LegacyBridge": "DELETE_FAILED"},
        tagging=[{"ResourceTagMappingList": [{"ResourceARN": "arn:a"}], "PaginationToken": "p2"},
                 {"ResourceTagMappingList": [{"ResourceARN": "arn:b"}]}],
        ec2=Ec2(nat=["nat-1"], endpoints=["vpce-1"]),
    ))  # fmt: skip
    assert found["stack"] == ["LegacyBridge: DELETE_FAILED"]
    assert found["tagged resources"] == ["arn:a", "arn:b"], "every page is read"
    assert "NAT gateways (billed hourly)" in found and "VPC endpoints (billed hourly)" in found


def test_the_bootstrap_stack_is_noted_but_is_not_a_leftover():
    cfn = Cfn({"CDKToolkit": "CREATE_COMPLETE"})
    assert aws_leftovers.find({"cloudformation": cfn, "tagging": Tagging([{}]), "ec2": Ec2()}) == {}
    assert "CDKToolkit (CREATE_COMPLETE)" in aws_leftovers.bootstrap_note(cfn)
    assert aws_leftovers.bootstrap_note(Cfn({})) is None


def test_an_unexpected_cloudformation_error_is_not_mistaken_for_a_missing_stack():
    class Broken:
        def describe_stacks(self, StackName):
            raise RuntimeError("AccessDenied")

    with pytest.raises(RuntimeError, match="AccessDenied"):
        aws_leftovers.stack_status(Broken(), "LegacyBridge")
