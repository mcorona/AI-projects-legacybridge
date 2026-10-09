"""What the stack must never become (ADR-014). All of this is checked on the synthesized template,
so no AWS credentials and no Docker are needed; the first deploy proves the rest."""

import json

import pytest
from conftest import of_type
from legacybridge_infra.stack import Settings, model_resources

MODEL = "us.anthropic.claude-haiku-4-5-20251001-v1:0"


def functions(resources):
    """Application functions by logical-id prefix (not the CDK helper that empties buckets)."""
    out = {}
    for key, value in of_type(resources, "AWS::Lambda::Function").items():
        for name in ("Worker", "DbInit", "Front"):
            if key.startswith(name):
                out[name] = value
    return out


def statements(resources):
    for policy in of_type(resources, "AWS::IAM::Policy").values():
        yield from policy["Properties"]["PolicyDocument"]["Statement"]


def as_list(x):
    return x if isinstance(x, list) else [x]


# --------------------------------------------------------------------------- the database
def test_the_database_is_small_private_encrypted_and_disposable(resources):
    (db,) = of_type(resources, "AWS::RDS::DBInstance").values()
    p = db["Properties"]
    assert p["DBInstanceClass"] == "db.t4g.micro" and p["Engine"] == "postgres"
    assert p["PubliclyAccessible"] is False and p["StorageEncrypted"] is True
    assert (
        p["MultiAZ"] is False
        and p["BackupRetentionPeriod"] == 0
        and p["DeletionProtection"] is False
    )
    assert db["DeletionPolicy"] == "Delete"


def test_only_the_worker_and_the_initializer_can_reach_postgres(template, resources):
    sg_ingress = [
        r["Properties"] for r in of_type(resources, "AWS::EC2::SecurityGroupIngress").values()
    ]
    assert len(sg_ingress) == 2 and all(i["FromPort"] == 5432 == i["ToPort"] for i in sg_ingress)
    assert all("SourceSecurityGroupId" in i and "CidrIp" not in i for i in sg_ingress)


def test_nothing_in_the_stack_accepts_traffic_from_the_internet(resources):
    for sg in of_type(resources, "AWS::EC2::SecurityGroup").values():
        for rule in sg["Properties"].get("SecurityGroupIngress", []):
            assert rule.get("CidrIp") != "0.0.0.0/0", sg["Properties"]["GroupDescription"]


def test_there_is_no_nat_gateway_and_no_internet_gateway(resources):
    types = {r["Type"] for r in resources.values()}
    assert "AWS::EC2::NatGateway" not in types and "AWS::EC2::InternetGateway" not in types
    assert "AWS::EC2::EIP" not in types


def test_the_worker_reaches_aws_through_three_interface_endpoints_and_one_gateway_endpoint(
    resources,
):
    endpoints = [e["Properties"] for e in of_type(resources, "AWS::EC2::VPCEndpoint").values()]
    kinds = sorted(e.get("VpcEndpointType", "Gateway") for e in endpoints)
    assert kinds == ["Gateway", "Interface", "Interface", "Interface"]
    for e in endpoints:
        if e.get("VpcEndpointType") == "Interface":
            assert e["PrivateDnsEnabled"] is True and len(e["SubnetIds"]) == 1, "billed per hour"


# --------------------------------------------------------------------------- the functions
def test_the_front_has_no_vpc_no_database_and_no_model(resources):
    front = functions(resources)["Front"]["Properties"]
    assert "VpcConfig" not in front
    env = front["Environment"]["Variables"]
    assert set(env) == {
        "LB_JOBS_TABLE",
        "LB_WORKER_FUNCTION",
        "LB_ASK_TOKEN_SECRET",
        "LB_DEFAULT_PROFILE",
    }
    assert "LB_DB_HOST" not in env and "BEDROCK_CHAT_MODEL" not in env


def test_only_the_worker_has_the_model_and_only_the_initializer_has_the_admin_secret(resources):
    fns = functions(resources)
    assert "BEDROCK_CHAT_MODEL" in fns["Worker"]["Properties"]["Environment"]["Variables"]
    for name, fn in fns.items():
        env = fn["Properties"]["Environment"]["Variables"]
        assert ("LB_ADMIN_SECRET" in env) == (name == "DbInit"), name
        assert ("BEDROCK_CHAT_MODEL" in env) == (name == "Worker"), name
        assert "LB_WRITER_SECRET" not in env or name == "DbInit", "no function applies writes"


def test_no_function_environment_holds_a_secret_value(resources):
    for fn in functions(resources).values():
        for key, value in fn["Properties"]["Environment"]["Variables"].items():
            assert "password" not in key.lower() and "PASSWORD" not in json.dumps(value), key


def test_the_worker_runs_in_the_vpc_with_limited_concurrency_and_without_retries(
    template, resources
):
    worker = functions(resources)["Worker"]["Properties"]
    assert worker["Architectures"] == ["arm64"] and worker["Timeout"] == 300
    assert worker["ReservedConcurrentExecutions"] == 2 and "VpcConfig" in worker
    template.has_resource_properties(
        "AWS::Lambda::EventInvokeConfig", {"MaximumRetryAttempts": 0}
    )  # a retry would pay for the same question twice


def test_the_functions_use_one_image_with_three_handlers(resources):
    handlers = {
        name: fn["Properties"]["ImageConfig"]["Command"][0]
        for name, fn in functions(resources).items()
    }
    assert handlers == {
        "Worker": "legacybridge.aws.worker.handler",
        "DbInit": "legacybridge.aws.db_init.handler",
        "Front": "legacybridge.aws.front.handler",
    }


# --------------------------------------------------------------------------- permissions
def test_no_permission_is_a_wildcard_in_action_or_in_resource(resources):
    for s in statements(resources):
        actions = as_list(s["Action"])
        assert all(a not in ("*", "*:*") and not a.endswith(":*") for a in actions), actions
        # A resource of "*" is allowed only where the service has no resource-level control.
        if s["Resource"] == "*":
            assert set(actions) <= set(), (
                f"{actions} on every resource (the X-Ray case is tested apart)"
            )


def test_the_model_is_invoked_only_on_the_inference_profile_and_its_foundation_models(resources):
    invoke = [
        s for s in statements(resources)
        if "bedrock:InvokeModel" in as_list(s["Action"]) and "anthropic" in json.dumps(s["Resource"])
    ]  # fmt: skip
    assert len(invoke) == 1, (
        "one statement for the chat model (the knowledge base role has its own)"
    )
    arns = as_list(invoke[0]["Resource"])
    assert any("inference-profile/" + MODEL in json.dumps(a) for a in arns)
    assert sum("foundation-model/anthropic.claude-haiku-4-5" in json.dumps(a) for a in arns) == 3


def test_the_front_cannot_call_bedrock_or_read_the_database_secrets(resources):
    for key, policy in of_type(resources, "AWS::IAM::Policy").items():
        if not key.startswith("Front"):
            continue
        actions = {
            a
            for s in policy["Properties"]["PolicyDocument"]["Statement"]
            for a in as_list(s["Action"])
        }
        assert not any(a.startswith("bedrock:") for a in actions)
        assert "lambda:InvokeFunction" in actions and "dynamodb:PutItem" in actions


# --------------------------------------------------------------------------- the API
def test_only_three_routes_exist_and_there_is_no_catch_all(resources):
    routes = {
        r["Properties"]["RouteKey"] for r in of_type(resources, "AWS::ApiGatewayV2::Route").values()
    }
    assert routes == {"POST /ask", "GET /ask/{id}", "GET /health"}


def test_the_api_is_throttled(template):
    template.has_resource_properties(
        "AWS::ApiGatewayV2::Stage",
        {"DefaultRouteSettings": {"ThrottlingRateLimit": 2, "ThrottlingBurstLimit": 5}},
    )


# --------------------------------------------------------------------------- guardrail and knowledge base
def test_the_guardrail_blocks_attacks_in_and_masks_pii_out_and_does_not_forbid_writes(resources):
    (g,) = of_type(resources, "AWS::Bedrock::Guardrail").values()
    p = g["Properties"]
    filters = {f["Type"]: f for f in p["ContentPolicyConfig"]["FiltersConfig"]}
    assert (
        filters["PROMPT_ATTACK"]["InputStrength"] == "HIGH"
        and filters["PROMPT_ATTACK"]["OutputStrength"] == "NONE"
    )
    topics = [t["Name"] for t in p["TopicPolicyConfig"]["TopicsConfig"]]
    assert topics == ["credenciales"], "a write request is a legitimate flow, not a denied topic"
    sensitive = p["SensitiveInformationPolicyConfig"]
    assert {e["Type"] for e in sensitive["PiiEntitiesConfig"]} == {"EMAIL", "PHONE"}
    (rfc,) = sensitive["RegexesConfig"]
    assert (
        rfc["Name"] == "RFC_FISICA"
        and rfc["OutputAction"] == "ANONYMIZE"
        and rfc["InputEnabled"] is False
    )
    assert all(e["InputEnabled"] is False for e in sensitive["PiiEntitiesConfig"]), (
        "a write request carries an e-mail"
    )


def test_the_worker_gets_a_pinned_guardrail_version_not_the_draft(resources):
    env = functions(resources)["Worker"]["Properties"]["Environment"]["Variables"]
    assert "GuardrailVersion" in json.dumps(
        env["LB_GUARDRAIL_VERSION"]
    ) or "PinnedGuardrail" in json.dumps(env["LB_GUARDRAIL_VERSION"])


def test_the_knowledge_base_is_on_s3_vectors_never_on_opensearch(resources):
    (kb,) = of_type(resources, "AWS::Bedrock::KnowledgeBase").values()
    storage = kb["Properties"]["StorageConfiguration"]
    assert storage["Type"] == "S3_VECTORS"
    assert not any("OpenSearch" in r["Type"] or "AOSS" in r["Type"] for r in resources.values())
    embedding = kb["Properties"]["KnowledgeBaseConfiguration"]["VectorKnowledgeBaseConfiguration"]
    assert (
        embedding["EmbeddingModelConfiguration"]["BedrockEmbeddingModelConfiguration"]["Dimensions"]
        == 1024
    )
    (index,) = of_type(resources, "AWS::S3Vectors::Index").values()
    assert index["Properties"]["Dimension"] == 1024
    assert set(index["Properties"]["MetadataConfiguration"]["NonFilterableMetadataKeys"]) >= {
        "AMAZON_BEDROCK_TEXT"
    }


def test_the_documents_bucket_is_private_and_tls_only(resources):
    (bucket,) = of_type(resources, "AWS::S3::Bucket").values()
    assert all(bucket["Properties"]["PublicAccessBlockConfiguration"].values())
    (policy,) = of_type(resources, "AWS::S3::BucketPolicy").values()
    assert any(s.get("Condition", {}).get("Bool", {}).get("aws:SecureTransport") == "false"
               for s in policy["Properties"]["PolicyDocument"]["Statement"])  # fmt: skip


# --------------------------------------------------------------------------- destroy leaves nothing
@pytest.mark.parametrize(
    "type_",
    ["AWS::RDS::DBInstance", "AWS::DynamoDB::Table", "AWS::S3::Bucket", "AWS::Logs::LogGroup",
     "AWS::SecretsManager::Secret", "AWS::S3Vectors::VectorBucket", "AWS::S3Vectors::Index"],
)  # fmt: skip
def test_every_resource_that_keeps_data_is_deleted_by_destroy(resources, type_):
    found = of_type(resources, type_)
    assert found, type_
    for key, resource in found.items():
        assert resource.get("DeletionPolicy") == "Delete", key


def test_logs_are_kept_one_week(resources):
    for group in of_type(resources, "AWS::Logs::LogGroup").values():
        assert group["Properties"]["RetentionInDays"] == 7


def test_the_jobs_table_is_on_demand_and_expires_its_items(resources):
    (table,) = of_type(resources, "AWS::DynamoDB::Table").values()
    p = table["Properties"]
    assert p["BillingMode"] == "PAY_PER_REQUEST" and p["TimeToLiveSpecification"] == {
        "AttributeName": "ttl",
        "Enabled": True,
    }


def test_everything_that_can_be_tagged_carries_the_project_tag(resources):
    tagged = [
        of_type(resources, t)
        for t in ("AWS::DynamoDB::Table", "AWS::RDS::DBInstance", "AWS::EC2::VPC")
    ]
    tagged.append({k: v for k, v in of_type(resources, "AWS::Lambda::Function").items()
                   if not k.startswith("CustomS3AutoDelete")})  # fmt: skip
    for group in tagged:
        assert group
        for key, resource in group.items():
            assert {"Key": "project", "Value": "legacybridge-v2"} in resource["Properties"][
                "Tags"
            ], key


# --------------------------------------------------------------------------- the helpers
def test_a_us_inference_profile_is_allowed_on_the_profile_and_each_destination_region():
    arns = model_resources(MODEL, "us-east-1", "111111111111")
    assert arns[0] == f"arn:aws:bedrock:us-east-1:111111111111:inference-profile/{MODEL}"
    assert [a.split(":")[3] for a in arns[1:]] == ["us-east-1", "us-east-2", "us-west-2"]
    assert all(
        a.endswith("foundation-model/anthropic.claude-haiku-4-5-20251001-v1:0") for a in arns[1:]
    )


def test_a_plain_model_id_needs_only_the_model_in_this_region():
    assert model_resources("anthropic.claude-3-haiku-20240307-v1:0", "us-east-1", "1") == [
        "arn:aws:bedrock:us-east-1::foundation-model/anthropic.claude-3-haiku-20240307-v1:0"
    ]


@pytest.mark.parametrize(
    "model", ["global.anthropic.claude-haiku-4-5-20251001-v1:0", "eu.anthropic.x"]
)
def test_other_geographies_are_refused_instead_of_given_a_wrong_policy(model):
    with pytest.raises(ValueError, match="only the us."):
        model_resources(model, "us-east-1", "1")


def test_a_reserved_concurrency_of_zero_means_the_account_default():
    class Node:
        def __init__(self, ctx):
            self.ctx = ctx

        def try_get_context(self, key):
            return self.ctx.get(key)

    assert Settings.from_context(Node({"reserved_concurrency": 0})).reserved_concurrency is None
    assert Settings.from_context(Node({})).reserved_concurrency == 2
    assert Settings.from_context(Node({"model_id": "m"})).model_id == "m"


# --------------------------------------------------------------------------- traces to X-Ray (off by default)
def test_traces_to_xray_are_off_by_default_and_cost_nothing(resources):
    env = functions(resources)["Worker"]["Properties"]["Environment"]["Variables"]
    assert "LB_TRACING" not in env and "LB_TRACE_BACKEND" not in env
    assert not any(
        a.startswith("xray:") for s in statements(resources) for a in as_list(s["Action"])
    )


def test_turning_xray_on_adds_one_endpoint_the_two_write_actions_and_the_switches(traced_resources):
    endpoints = of_type(traced_resources, "AWS::EC2::VPCEndpoint")
    assert sorted(e["Properties"].get("VpcEndpointType", "Gateway") for e in endpoints.values()) == [
        "Gateway", "Interface", "Interface", "Interface", "Interface"]  # fmt: skip
    env = functions(traced_resources)["Worker"]["Properties"]["Environment"]["Variables"]
    assert (env["LB_TRACING"], env["LB_TRACE_BACKEND"]) == ("1", "xray")
    wildcard = [s for s in statements(traced_resources) if s["Resource"] == "*"]
    assert [sorted(as_list(s["Action"])) for s in wildcard] == [
        ["xray:PutTelemetryRecords", "xray:PutTraceSegments"]
    ]


def test_applying_the_guardrail_is_allowed_on_the_guardrail_and_its_cross_region_profile_only(
    resources,
):
    """The first deploy was refused for lack of the profile: STANDARD routes through it."""
    apply = [s for s in statements(resources) if "bedrock:ApplyGuardrail" in as_list(s["Action"])]
    assert len(apply) == 1
    arns = [json.dumps(r) for r in as_list(apply[0]["Resource"])]
    assert len(arns) == 2 and any("guardrail-profile/us.guardrail.v1:0" in a for a in arns)
    assert any("Guardrail" in a and "guardrail-profile" not in a for a in arns)
    assert not any("*" in a for a in arns)
