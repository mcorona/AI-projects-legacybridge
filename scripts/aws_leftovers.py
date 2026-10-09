"""What is still in the account after ``cdk destroy`` (ADR-014).

    uv run python -m scripts.aws_leftovers          # exit 1 when anything of the project remains

Destroying is only worth trusting if it is checked. Everything the stack creates carries the tag
``project=legacybridge-v2``; this asks the tagging API for it, then looks directly for the few
things that cost money by the hour and may not show there (the stack itself, RDS instances, NAT
gateways, interface endpoints). The CDK bootstrap stack and its bucket and repository are not part
of the project: they are listed apart, so you can see them and decide.
"""

from __future__ import annotations

import os
import sys
from typing import Any

import boto3

TAG = {"Key": "project", "Values": ["legacybridge-v2"]}
STACK = "LegacyBridge"
BOOTSTRAP_STACK = "CDKToolkit"
HOURLY = {"AWS::EC2::NatGateway", "AWS::EC2::VPCEndpoint", "AWS::RDS::DBInstance"}


def tagged_arns(tagging: Any) -> list[str]:
    arns, token = [], ""
    while True:
        page = tagging.get_resources(TagFilters=[TAG], PaginationToken=token)
        arns += [r["ResourceARN"] for r in page.get("ResourceTagMappingList", [])]
        token = page.get("PaginationToken", "")
        if not token:
            return arns


def stack_status(cloudformation: Any, name: str) -> str | None:
    try:
        stacks = cloudformation.describe_stacks(StackName=name)["Stacks"]
    except Exception as error:  # a missing stack is an error to boto3 and the good answer here
        if "does not exist" in str(error):
            return None
        raise
    return stacks[0]["StackStatus"] if stacks else None


def find(clients: dict[str, Any]) -> dict[str, list[str]]:
    leftovers: dict[str, list[str]] = {}
    if status := stack_status(clients["cloudformation"], STACK):
        leftovers["stack"] = [f"{STACK}: {status}"]
    if arns := tagged_arns(clients["tagging"]):
        leftovers["tagged resources"] = arns
    ec2 = clients["ec2"]
    nat = [
        n["NatGatewayId"]
        for n in ec2.describe_nat_gateways(
            Filters=[
                {"Name": "tag:project", "Values": TAG["Values"]},
                {"Name": "state", "Values": ["available", "pending"]},
            ]
        )["NatGateways"]
    ]
    if nat:
        leftovers["NAT gateways (billed hourly)"] = nat
    endpoints = [
        e["VpcEndpointId"]
        for e in ec2.describe_vpc_endpoints(
            Filters=[{"Name": "tag:project", "Values": TAG["Values"]}]
        )["VpcEndpoints"]
    ]
    if endpoints:
        leftovers["VPC endpoints (billed hourly)"] = endpoints
    return leftovers


def bootstrap_note(cloudformation: Any) -> str | None:
    status = stack_status(cloudformation, BOOTSTRAP_STACK)
    return (
        f"{BOOTSTRAP_STACK} ({status}) is the CDK bootstrap, shared and not part of the project"
        if status
        else None
    )


def main() -> int:
    session = boto3.Session(profile_name=os.environ.get("AWS_PROFILE") or None)
    clients = {
        "cloudformation": session.client("cloudformation"),
        "tagging": session.client("resourcegroupstaggingapi"),
        "ec2": session.client("ec2"),
    }
    leftovers = find(clients)
    for kind, items in leftovers.items():
        print(f"LEFT: {kind}")
        for item in items:
            print("  -", item)
    if note := bootstrap_note(clients["cloudformation"]):
        print("note:", note)
    if not leftovers:
        print("nothing of legacybridge-v2 is left")
    return 1 if leftovers else 0


if __name__ == "__main__":
    sys.exit(main())
