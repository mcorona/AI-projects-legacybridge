"""The cost model (Phase 8): what a question and a month cost, local against Bedrock.

    uv run python -m scripts.cost_model --write     # rewrite docs/cost-model.md
    uv run python -m scripts.cost_model --check     # exit 1 when docs/cost-model.md is out of date
    uv run python -m scripts.cost_model             # print it

Nothing is typed in the document. Tokens and latency per question are measured (``evals/archive``);
model prices come from ``config/models.yaml`` and the other prices from the dated snapshot
``docs/cost-model.prices.json``; the shape of the stack (memory, reserved concurrency, endpoints)
is pinned to ``infra/`` by a test. What is *assumed* (polling interval, size of a result, calls to
the guardrail) is listed in the document under "Assumptions".
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any, get_args

from evals import report
from evals.metrics import latency, usage_cost
from legacybridge.agent.schemas import Status
from legacybridge.observability.emf import UNITS as UNITS_ALL
from legacybridge.observability.emf import series_per_profile

ROOT = Path(__file__).resolve().parents[1]
PRICES = ROOT / "docs" / "cost-model.prices.json"
OUT = ROOT / "docs" / "cost-model.md"
ARCHIVE = ROOT / "evals" / "archive"
RUN_ID = "20261009"

VOLUMES = (100, 1_000, 10_000)  # questions a day
DAYS = 30  # days of questions in a month
HOURS_PER_MONTH = 730  # AWS's convention for what runs all the time
PROFILES = [
    "local-qwen",
    "omniroute-free",
    "bedrock-claude",
    "bedrock-claude-sonnet",
    "bedrock-claude-opus",
]
AWS_PROFILES = [p for p in PROFILES if p.startswith("bedrock")]

# The stack, as deployed (pinned to infra/legacybridge_infra/stack.py by tests/test_docs.py).
WORKER_GB = 2.0  # memory_size=2048
RESERVED_CONCURRENCY = 2
INTERFACE_ENDPOINTS = 3  # Bedrock runtime, Bedrock agent runtime, Secrets Manager (+1 for X-Ray)
SECRETS = 5  # admin, reader, writer, proposer, API token
RDS_STORAGE_GB = 20
FRONT_GB, FRONT_SECONDS = 0.25, 0.1  # one small invocation per request

# Assumptions that cannot be measured from the stored results.
ORDINALS = {1: "largest", 2: "second", 3: "third", 4: "fourth"}
TIGHT = 0.8  # above this share of its concurrency a worker has no room for a burst
POLL_SECONDS = 3.0  # a client asks for the job every 3 s
DYNAMO_WRITE_UNITS = 22  # create (1) + status (1) + a ~20 KB result (20)
DYNAMO_READ_UNITS_PER_POLL = 5  # a consistent read of the ~20 KB result
GUARDRAIL_CALLS = 4  # the question, and the answer plus its warnings and assumptions
LOG_KB_PER_QUESTION = 4  # the EMF record and the function's own lines
BYTES_PER_TOKEN = 4  # what crosses a VPC endpoint, roughly


def load_prices(path: Path = PRICES) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def first_tier(item: dict[str, Any]) -> float:
    return item["tiers"][0][2]


def usd(x: float, digits: int = 2) -> str:
    return f"${x:,.{digits}f}"


# ============================================================================ measured inputs
def measured(archive: Path = ARCHIVE, run_id: str = RUN_ID) -> dict[str, dict[str, Any]]:
    """Tokens, calls and latency per question for each profile, from the stored run."""
    run = report.load_run(run_id, archive / run_id)
    out = {}
    for profile in PROFILES:
        records = run.records(profile)
        price_in, price_out = report.prices(profile)
        usage = usage_cost(records, price_in, price_out)
        out[profile] = {
            "input_tokens": usage["input_tokens"],
            "output_tokens": usage["output_tokens"],
            "llm_calls": usage["llm_calls"],
            "model_usd": usage["usd_per_question"],
            "mean_seconds": latency(records)["mean"],
            "p50_seconds": latency(records)["p50"],
            "n": usage["n"],
        }
    return out


# ============================================================================ the model
def variable_per_question(m: dict[str, Any], prices: dict[str, Any]) -> dict[str, float]:
    """Everything that scales with questions, in USD per question, on the AWS stack."""
    items = prices["items"]
    seconds = m["mean_seconds"]
    polls = math.ceil(seconds / POLL_SECONDS)
    requests = 1 + polls  # the POST and each poll
    guardrail_unit = (
        items["guardrail_content_policy_1k_text_units"]["usd"]
        + items["guardrail_topic_policy_1k_text_units"]["usd"]
        + items["guardrail_sensitive_information_1k_text_units"]["usd"]
    ) / 1000
    gb = (m["input_tokens"] + m["output_tokens"]) * BYTES_PER_TOKEN / 1e9
    parts = {
        "model": m["model_usd"],
        "worker": seconds * WORKER_GB * first_tier(items["lambda_gb_second_arm"])
        + items["lambda_request"]["usd"],
        "front": requests
        * (
            FRONT_GB * FRONT_SECONDS * first_tier(items["lambda_gb_second_arm"])
            + items["lambda_request"]["usd"]
            + first_tier(items["apigw_http_request"])
        ),
        "dynamodb": DYNAMO_WRITE_UNITS * items["dynamodb_write_request_unit"]["usd"]
        + polls * DYNAMO_READ_UNITS_PER_POLL * items["dynamodb_read_request_unit"]["usd"],
        "guardrail": GUARDRAIL_CALLS * guardrail_unit,
        "logs": LOG_KB_PER_QUESTION / 1e6 * items["cloudwatch_logs_ingest_gb"]["usd"],
        "endpoint data": gb * items["vpc_interface_endpoint_gb"]["usd"],
    }
    parts["total"] = sum(parts.values())
    return parts


def metric_series() -> int:
    """Custom metrics one profile creates (``observability/emf.py`` is the one definition)."""
    return series_per_profile(len(get_args(Status)))


def fixed_monthly(prices: dict[str, Any]) -> dict[str, float]:
    items = prices["items"]
    series = metric_series()
    return {
        "RDS PostgreSQL db.t4g.micro (24/7)": HOURS_PER_MONTH
        * items["rds_t4g_micro_postgres_single_az_hour"]["usd"],
        f"RDS storage, {RDS_STORAGE_GB} GB gp3": RDS_STORAGE_GB * items["rds_gp3_gb_month"]["usd"],
        f"{INTERFACE_ENDPOINTS} interface endpoints (24/7)": INTERFACE_ENDPOINTS
        * HOURS_PER_MONTH
        * items["vpc_interface_endpoint_hour"]["usd"],
        f"{SECRETS} secrets": SECRETS * items["secret_month"]["usd"],
        f"CloudWatch custom metrics ({series} series)": series
        * first_tier(items["cloudwatch_metric_month"]),
    }


def per_hour_deployed(prices: dict[str, Any]) -> float:
    """What the stack costs per hour while it exists, without the metrics (they bill by month)."""
    fixed = fixed_monthly(prices)
    return sum(v for k, v in fixed.items() if "metrics" not in k) / HOURS_PER_MONTH


def monthly(profile: str, volume: int, v: dict[str, Any], fixed: float) -> float | None:
    if profile in ("local-qwen", "omniroute-free"):
        return 0.0
    return fixed + volume * DAYS * v["total"]


def capacity(profile: str, m: dict[str, Any]) -> float:
    """Questions a day that the concurrency can carry if it were busy all day."""
    concurrency = 1 if profile in ("local-qwen", "omniroute-free") else RESERVED_CONCURRENCY
    return concurrency * 86400 / m["mean_seconds"]


def needed_concurrency(volume: int, m: dict[str, Any]) -> float:
    return volume * m["mean_seconds"] / 86400


# ============================================================================ the document
def table(head: list[str], rows: list[list[str]]) -> list[str]:
    return report.table(head, rows)


def render(archive: Path = ARCHIVE, prices_path: Path = PRICES) -> str:
    prices = load_prices(prices_path)
    data = measured(archive)
    var = {p: variable_per_question(data[p], prices) for p in AWS_PROFILES}
    fixed = fixed_monthly(prices)
    fixed_total = sum(fixed.values())
    series = metric_series()
    metric_price = first_tier(prices["items"]["cloudwatch_metric_month"])
    metrics_usd = series * metric_price
    first_design = len(UNITS_ALL) * (1 + len(get_args(Status)))
    hour = per_hour_deployed(prices)
    xray = prices["items"]["vpc_interface_endpoint_hour"]["usd"] * HOURS_PER_MONTH
    n = data["bedrock-claude"]["n"]

    L = [
        "# Cost model",
        "",
        "> Generated by `scripts/cost_model.py` from the stored run (`evals/archive/"
        f"{RUN_ID}/`), the model prices in `config/models.yaml` and the AWS prices in "
        f"`docs/cost-model.prices.json` (read from the AWS Price List on {prices['retrieved']}). "
        "Do not edit by hand: change the inputs and rebuild it.",
        "",
        f"A question costs the model tokens plus a few cents of plumbing, and a month costs a "
        f"fixed bill for the stack plus the questions. Per-question figures are the mean of the "
        f"{n} golden cases of the run, a mix of 55 answerable questions, 15 ambiguous or out of "
        "scope and 10 write requests; a different mix costs differently.",
        "",
        "## Per question",
        "",
    ]
    rows = []
    for p in PROFILES:
        m = data[p]
        rows.append(
            [
                f"`{p}`",
                f"{m['input_tokens']:,.0f} / {m['output_tokens']:,.0f}",
                f"{m['llm_calls']:.1f}",
                f"{m['mean_seconds']:.1f} s",
                usd(var[p]["model"], 4) if p in var else "free",
                usd(var[p]["total"] - var[p]["model"], 4) if p in var else "n/a",
                usd(var[p]["total"], 4) if p in var else "free",
            ]
        )
    L += table(
        ["Profile", "Tokens in / out", "Model calls", "Mean latency", "Model", "Plumbing", "Total"],
        rows,
    )
    L += [
        "",
        "*Plumbing* is the worker and front Lambdas, API Gateway, DynamoDB, the guardrail, logs and "
        "endpoint traffic. It is the same order for every model, so it matters only for the cheap "
        "ones. A local profile has no marginal cost here; the machine and its electricity are "
        "not valued. `omniroute-free` is a rate-limited free tier, not a production option.",
        "",
        "Plumbing for `bedrock-claude`, in USD per question:",
        "",
    ]
    L += table(
        ["Part", "USD"],
        [[k, usd(v, 5)] for k, v in var["bedrock-claude"].items() if k not in ("model", "total")],
    )
    L += [
        "",
        "## Per month",
        "",
        f"A month is {DAYS} days of questions and the stack running {HOURS_PER_MONTH} hours.",
        "",
    ]
    head = ["Profile"] + [f"{v:,} / day" for v in VOLUMES]
    rows = [
        [f"`{p}`"]
        + [usd(monthly(p, v, var.get(p), fixed_total)) if p in var else "$0" for v in VOLUMES]
        for p in PROFILES
    ]
    L += table(head, rows)
    L += ["", "The fixed bill is always on, whatever the volume:", ""]
    L += table(
        ["Item", "USD / month"],
        [[k, usd(v)] for k, v in fixed.items()]
        + [["**Total, always on**", f"**{usd(fixed_total)}**"]],
    )
    L += [
        "",
        f"While a demo exists the stack costs about **{usd(hour, 3)} an hour** (without the metrics, "
        f"which bill by month), and nothing once destroyed. Turning on X-Ray adds one endpoint, "
        f"{usd(xray)} a month.",
        "",
        "## Capacity",
        "",
        f"The worker carries as many questions as its concurrency allows: {RESERVED_CONCURRENCY} "
        "reserved in the stack, one for a local model.",
        "",
    ]
    rows = []
    for p in PROFILES:
        m = data[p]
        cells = [f"`{p}`", f"{capacity(p, m):,.0f}"]
        for v in VOLUMES:
            need = needed_concurrency(v, m)
            limit = 1 if p in ("local-qwen", "omniroute-free") else RESERVED_CONCURRENCY
            mark = " **over**" if need > limit else " (tight)" if need > TIGHT * limit else ""
            cells.append(f"{need:.2f}{mark}")
        rows.append(cells)
    L += table(
        ["Profile", "Questions a day it can carry"]
        + [f"Average concurrency at {v:,} / day" for v in VOLUMES],
        rows,
    )
    L += ["", "## What stands out", ""]
    haiku, opus = data["bedrock-claude"], data["bedrock-claude-opus"]
    vol_lo, vol_hi = VOLUMES[0], VOLUMES[2]
    total_lo = monthly("bedrock-claude", vol_lo, var["bedrock-claude"], fixed_total)
    total_hi = monthly("bedrock-claude", vol_hi, var["bedrock-claude"], fixed_total)
    largest = max(fixed, key=fixed.get)
    others = [v for k, v in fixed.items() if "metrics" not in k]
    rank = 1 + sum(v > first_design * metric_price for v in others)  # where the first design ranked
    L += [
        f"- **Fixed or variable depends on volume.** For `bedrock-claude` the always-on stack is "
        f"{fixed_total / total_lo:.0%} of the {usd(total_lo)} a month at {vol_lo} questions a day "
        f"({usd(total_lo / (vol_lo * DAYS), 3)} a question all in, against "
        f"{usd(var['bedrock-claude']['total'], 3)} of variable cost) and "
        f"{fixed_total / total_hi:.1%} at {vol_hi:,} a day, where the model is the cost.",
        f"- **The largest fixed item is {largest} ({usd(fixed[largest])} a month).**"
        + (
            " They let a function in the VPC reach Bedrock and Secrets Manager without a NAT gateway "
            "(ADR-014)."
            if "endpoints" in largest
            else ""
        ),
        f"- **Metrics were a cost bug, found by this model and fixed.** The first design repeated "
        f"every metric for each of the {len(get_args(Status))} values of `Status`: {first_design} "
        f"series a profile, {usd(first_design * metric_price)} a month, which would have been the "
        f"{ORDINALS.get(rank, f'{rank}th')} largest fixed item, for data the dashboard never read. Now each metric is by "
        f"profile and only `Questions` is by status: {series} series, {usd(metrics_usd)} a month. "
        "The stack that was deployed for the measurements (33 series) still had the first "
        "design; it was destroyed before the fix.",
        f"- **Capacity.** With {RESERVED_CONCURRENCY} workers `bedrock-claude` carries "
        f"{capacity('bedrock-claude', haiku):,.0f} questions a day and `bedrock-claude-opus` "
        f"{capacity('bedrock-claude-opus', opus):,.0f}; at {vol_hi:,} a day the reserved "
        "concurrency has to go up (`-c reserved_concurrency=N`), which costs nothing by itself on "
        "Lambda. A single local model carries "
        f"{capacity('local-qwen', data['local-qwen']):,.0f} a day.",
        "",
        "## Assumptions",
        "",
        f"- The mean latency of the run is the time a worker is busy; a client polls every "
        f"{POLL_SECONDS:g} s. The latencies were measured on the author's laptop, so the Bedrock "
        "ones include the network between the laptop and the region; a worker inside the region "
        "would not pay that leg, which was not measured over the full set "
        "(`docs/measurement-environment.md`).",
        f"- A result is about 20 KB in DynamoDB: {DYNAMO_WRITE_UNITS} write units and "
        f"{DYNAMO_READ_UNITS_PER_POLL} read units a poll.",
        f"- The guardrail is called {GUARDRAIL_CALLS} times a question (the question; the answer and "
        "its warnings and assumptions), one text unit each, with the content, topic and "
        "sensitive-information policies.",
        f"- {LOG_KB_PER_QUESTION} KB of logs a question; {BYTES_PER_TOKEN} bytes a token through the endpoints.",
        "- Lambda, API Gateway and CloudWatch stay in their first price tier at these volumes.",
        "",
        "## Not included",
        "",
        "Data transfer out, KMS, a support plan, taxes, Transaction Search ingestion (nothing "
        "without traffic; spans bill as logs with it), the AWS free tier, Bedrock quotas and "
        "throttling, and any saving from prompt caching or batch inference, neither of which was "
        "measured. Each profile ran once, so the *answers* these costs buy carry no measure of "
        "run-to-run noise even if the costs are exact.",
        "",
    ]
    return "\n".join(L)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--write", action="store_true")
    mode.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    text = render()
    if args.write:
        OUT.write_text(text, encoding="utf-8")
        print(f"wrote {OUT}")
        return 0
    if args.check:
        if not OUT.exists() or OUT.read_text(encoding="utf-8") != text:
            print(
                "docs/cost-model.md is out of date: run `uv run python -m scripts.cost_model --write`",
                file=sys.stderr,
            )
            return 1
        print("docs/cost-model.md is up to date")
        return 0
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
