"""Send traces straight to the AWS X-Ray OTLP endpoint, with no collector (ADR-014).

The endpoint (``https://xray.<region>.amazonaws.com/v1/traces``) wants every request signed with
SigV4, which the stock OTLP exporter does not do. This signs each request with botocore from a
``requests`` session that the exporter accepts. The account must have Transaction Search turned on
(spans are then stored in CloudWatch Logs); without it the endpoint answers HTTP 400.

Nothing here runs unless ``LB_TRACE_BACKEND=xray``.
"""

from __future__ import annotations

from typing import Any

import boto3
import requests
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest


def xray_endpoint(region: str) -> str:
    return f"https://xray.{region}.amazonaws.com/v1/traces"


class SignedRequests(requests.auth.AuthBase):
    """Adds a SigV4 ``Authorization`` header to every request the exporter prepares."""

    def __init__(self, credentials: Any, region: str, service: str = "xray"):
        self._credentials, self._region, self._service = credentials, region, service

    def __call__(self, request: requests.PreparedRequest) -> requests.PreparedRequest:
        frozen = self._credentials.get_frozen_credentials()  # refreshes a role's keys when due
        to_sign = AWSRequest(
            method=request.method,
            url=request.url,
            data=request.body,
            headers={"Content-Type": request.headers.get("Content-Type", "application/x-protobuf")},
        )
        SigV4Auth(frozen, self._service, self._region).add_auth(to_sign)
        request.headers.update(dict(to_sign.headers.items()))
        return request


def signed_session(
    region: str, *, profile: str | None = None, credentials: Any = None
) -> requests.Session:
    """A session whose requests are signed with the function's role (or ``profile`` on a laptop)."""
    credentials = (
        credentials or boto3.Session(profile_name=profile, region_name=region).get_credentials()
    )
    if credentials is None:
        raise RuntimeError("no AWS credentials to sign the trace export with")
    session = requests.Session()
    session.auth = SignedRequests(credentials, region)
    return session
