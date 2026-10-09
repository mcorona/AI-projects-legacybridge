"""CDK entry point (ADR-014). Run from this directory: ``npx aws-cdk@2.1145.0 synth``."""

import os
from pathlib import Path

import aws_cdk as cdk
from legacybridge_infra.stack import LegacyBridgeStack, Settings

ROOT = Path(__file__).resolve().parents[1]

app = cdk.App()
LegacyBridgeStack(
    app,
    "LegacyBridge",
    repo_root=ROOT,
    settings=Settings.from_context(app.node),
    env=cdk.Environment(
        account=os.environ.get("CDK_DEFAULT_ACCOUNT"),
        region=os.environ.get("CDK_DEFAULT_REGION") or "us-east-1",
    ),
)
app.synth()
