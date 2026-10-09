import sys
from pathlib import Path

import aws_cdk as cdk
import pytest
from aws_cdk.assertions import Template

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from legacybridge_infra.stack import LegacyBridgeStack  # noqa: E402


@pytest.fixture(scope="session")
def template(tmp_path_factory) -> Template:
    """The stack as CloudFormation. The image asset is a one-line Dockerfile: synthesis stages the
    directory but never builds it, so the tests need neither Docker nor AWS credentials."""
    assets = tmp_path_factory.mktemp("assets")
    (assets / "Dockerfile").write_text("FROM scratch\n")
    app = cdk.App()
    stack = LegacyBridgeStack(
        app,
        "Test",
        repo_root=assets,
        env=cdk.Environment(account="111111111111", region="us-east-1"),
    )
    return Template.from_stack(stack)


@pytest.fixture(scope="session")
def resources(template) -> dict:
    return template.to_json()["Resources"]


def of_type(resources: dict, type_: str) -> dict:
    return {k: v for k, v in resources.items() if v["Type"] == type_}
