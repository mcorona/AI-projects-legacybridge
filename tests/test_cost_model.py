"""The cost model (Phase 8): its inputs are the real stack and the stored run, not typed figures."""

import re
from pathlib import Path

import pytest

from scripts import cost_model as cm

ROOT = Path(__file__).resolve().parents[1]
STACK = (ROOT / "infra" / "legacybridge_infra" / "stack.py").read_text(encoding="utf-8")
PRICES = cm.load_prices()


# --------------------------------------------------------------------------- pinned to the stack
def test_the_assumed_worker_memory_is_the_stacks():
    assert re.search(rf"memory_size={int(cm.WORKER_GB * 1024)},", STACK)


def test_the_assumed_reserved_concurrency_is_the_stacks_default():
    assert f"reserved_concurrency: int | None = {cm.RESERVED_CONCURRENCY}" in STACK


def test_the_assumed_endpoints_are_the_ones_the_stack_creates_without_xray():
    block = STACK.split("services = {")[1].split("}")[0]
    assert block.count("InterfaceVpcEndpointAwsService.") == cm.INTERFACE_ENDPOINTS


def test_the_assumed_secrets_are_the_ones_the_stack_creates():
    roles = re.search(r'for role in \("reader", "writer", "proposer"\)', STACK)
    assert roles and "AskToken" in STACK and "from_generated_secret" in STACK
    assert cm.SECRETS == 3 + 1 + 1  # three database roles, the API token and the admin secret


def test_the_assumed_database_is_the_stacks():
    assert "BURSTABLE4_GRAVITON, ec2.InstanceSize.MICRO" in STACK or "InstanceSize.MICRO" in STACK
    assert f"allocated_storage={cm.RDS_STORAGE_GB}" in STACK


# --------------------------------------------------------------------------- the snapshot
def test_the_price_snapshot_is_dated_and_has_every_price_the_model_reads():
    assert PRICES["retrieved"] == "2026-10-09" and PRICES["region"] == "us-east-1"
    needed = {
        "lambda_gb_second_arm", "lambda_request", "apigw_http_request", "dynamodb_write_request_unit",
        "dynamodb_read_request_unit", "secret_month", "cloudwatch_metric_month",
        "cloudwatch_logs_ingest_gb", "rds_t4g_micro_postgres_single_az_hour", "rds_gp3_gb_month",
        "vpc_interface_endpoint_hour", "vpc_interface_endpoint_gb",
        "guardrail_content_policy_1k_text_units", "guardrail_topic_policy_1k_text_units",
        "guardrail_sensitive_information_1k_text_units",
    }  # fmt: skip
    assert needed <= set(PRICES["items"])


# --------------------------------------------------------------------------- the model
@pytest.fixture(scope="module")
def data():
    return cm.measured()


def test_the_measured_inputs_come_from_all_five_profiles_of_the_stored_run(data):
    assert sorted(data) == sorted(cm.PROFILES) and all(m["n"] == 80 for m in data.values())
    assert data["bedrock-claude"]["model_usd"] == pytest.approx(0.0261, abs=0.0001)
    assert data["bedrock-claude-opus"]["model_usd"] == pytest.approx(0.1299, abs=0.0001)
    assert data["local-qwen"]["model_usd"] == 0


def test_a_question_costs_the_model_plus_plumbing_and_the_parts_add_up(data):
    parts = cm.variable_per_question(data["bedrock-claude"], PRICES)
    assert parts["total"] == pytest.approx(sum(v for k, v in parts.items() if k != "total"))
    assert parts["model"] > 10 * (parts["total"] - parts["model"]) / 2, "the model is most of it"
    assert parts["guardrail"] == pytest.approx(cm.GUARDRAIL_CALLS * 0.40 / 1000)


def test_the_fixed_bill_is_the_sum_of_its_items_and_the_hour_leaves_the_metrics_out():
    fixed = cm.fixed_monthly(PRICES)
    assert sum(fixed.values()) == pytest.approx(11.68 + 2.30 + 21.90 + 2.00 + 4.50, abs=0.01)
    assert cm.per_hour_deployed(PRICES) == pytest.approx(0.052, abs=0.001)


def test_the_metric_series_are_the_ones_the_emf_records_create():
    from legacybridge.observability import emf

    assert cm.metric_series() == emf.series_per_profile(4) == 15


def test_a_local_model_has_no_marginal_cost_and_a_bedrock_one_pays_the_fixed_bill(data):
    var = cm.variable_per_question(data["bedrock-claude"], PRICES)
    assert cm.monthly("local-qwen", 10_000, None, 99.0) == 0
    assert cm.monthly("bedrock-claude", 0, var, 42.0) == 42.0
    assert cm.monthly("bedrock-claude", 100, var, 42.0) == pytest.approx(
        42.0 + 100 * 30 * var["total"]
    )


def test_capacity_is_concurrency_over_latency_and_the_need_grows_with_volume(data):
    m = data["bedrock-claude"]
    assert cm.capacity("bedrock-claude", m) == pytest.approx(2 * 86400 / m["mean_seconds"])
    assert cm.capacity("local-qwen", data["local-qwen"]) == pytest.approx(
        86400 / data["local-qwen"]["mean_seconds"]
    )
    assert cm.needed_concurrency(10_000, m) == pytest.approx(10 * cm.needed_concurrency(1_000, m))


# --------------------------------------------------------------------------- the document
def test_the_committed_document_is_what_the_inputs_produce():
    assert cm.OUT.read_text(encoding="utf-8") == cm.render()


def test_the_document_says_what_was_assumed_and_what_it_leaves_out():
    text = cm.render()
    for heading in ("## Per question", "## Per month", "## Capacity", "## What stands out",
                    "## Assumptions", "## Not included"):  # fmt: skip
        assert heading in text
    assert "Metrics were a cost bug" in text and "the stack costs about **$0.052 an hour**" in text
    assert "provisional" in text, "the golden set is a draft and the document says so"


def test_the_check_mode_fails_when_the_document_drifts(tmp_path, monkeypatch, capsys):
    stale = tmp_path / "cost-model.md"
    stale.write_text("old")
    monkeypatch.setattr(cm, "OUT", stale)
    assert cm.main(["--check"]) == 1 and "out of date" in capsys.readouterr().err
    stale.write_text(cm.render())
    assert cm.main(["--check"]) == 0
