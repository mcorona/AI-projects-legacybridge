"""Tracing to AWS X-Ray without a collector (ADR-014): the ids, the SigV4 signature, the switch."""

import time

import pytest
import requests
from botocore.credentials import Credentials

from legacybridge.aws.xray import SignedRequests, signed_session, xray_endpoint
from legacybridge.observability import tracing
from legacybridge.observability.tracing import XRayIdGenerator, build_provider, trace_backend

CREDS = Credentials("AKIDEXAMPLE", "secret-key", "session-token")


def prepared(body=b"payload", url="https://xray.us-east-1.amazonaws.com/v1/traces"):
    request = requests.Request(
        "POST", url, data=body, headers={"Content-Type": "application/x-protobuf"}
    )
    return request.prepare()


# --------------------------------------------------------------------------- ids
def test_a_trace_id_starts_with_the_current_time_so_that_xray_accepts_it():
    generator = XRayIdGenerator()
    before = int(time.time())
    trace_id = generator.generate_trace_id()
    after = int(time.time())
    assert before <= trace_id >> 96 <= after and trace_id.bit_length() <= 128
    assert generator.generate_trace_id() != trace_id, "the rest is random"


def test_span_ids_are_never_zero_which_opentelemetry_treats_as_invalid():
    generator = XRayIdGenerator()
    ids = {generator.generate_span_id() for _ in range(200)}
    assert 0 not in ids and len(ids) > 190 and all(i.bit_length() <= 64 for i in ids)


# --------------------------------------------------------------------------- signing
def test_a_request_is_signed_for_the_xray_service_in_the_region_with_the_session_token():
    request = SignedRequests(CREDS, "us-east-1")(prepared())
    auth = request.headers["Authorization"]
    assert auth.startswith("AWS4-HMAC-SHA256 Credential=AKIDEXAMPLE/")
    assert "/us-east-1/xray/aws4_request" in auth and "Signature=" in auth
    assert (
        request.headers["X-Amz-Security-Token"] == "session-token"
        and "X-Amz-Date" in request.headers
    )
    assert request.headers["Content-Type"] == "application/x-protobuf"


def test_the_signature_covers_the_body():
    a = SignedRequests(CREDS, "us-east-1")(prepared(b"one"))
    b = SignedRequests(CREDS, "us-east-1")(prepared(b"two"))
    sig = lambda r: r.headers["Authorization"].split("Signature=")[1]  # noqa: E731
    assert sig(a) != sig(b)


def test_credentials_that_refresh_are_asked_every_time():
    class Rotating:
        calls = 0

        def get_frozen_credentials(self):
            self.calls += 1
            return CREDS.get_frozen_credentials()

    rotating = Rotating()
    auth = SignedRequests(rotating, "us-east-1")
    auth(prepared())
    auth(prepared())
    assert rotating.calls == 2


def test_a_session_signs_with_the_credentials_it_was_given_and_refuses_to_work_without_any():
    session = signed_session("us-east-1", credentials=CREDS)
    assert isinstance(session.auth, SignedRequests)
    assert xray_endpoint("us-west-2") == "https://xray.us-west-2.amazonaws.com/v1/traces"
    import boto3

    class NoCreds:
        def __init__(self, **kw): ...
        def get_credentials(self):
            return None

    original = boto3.Session
    boto3.Session = NoCreds
    try:
        with pytest.raises(RuntimeError, match="no AWS credentials"):
            signed_session("us-east-1")
    finally:
        boto3.Session = original


# --------------------------------------------------------------------------- the switch
def test_the_backend_is_otlp_unless_xray_is_asked_for(monkeypatch):
    monkeypatch.delenv("LB_TRACE_BACKEND", raising=False)
    assert trace_backend() == "otlp"
    monkeypatch.setenv("LB_TRACE_BACKEND", " XRay ")
    assert trace_backend() == "xray"
    monkeypatch.setenv("LB_TRACE_BACKEND", "jaeger")
    assert trace_backend() == "otlp"


def test_the_default_provider_keeps_random_ids_and_a_collector_endpoint(monkeypatch):
    monkeypatch.delenv("LB_TRACE_BACKEND", raising=False)
    provider = build_provider("t")
    assert not isinstance(provider.id_generator, XRayIdGenerator)
    exporter = provider._active_span_processor._span_processors[0].span_exporter
    assert exporter._endpoint.endswith(":4318/v1/traces")


def test_the_xray_provider_uses_xray_ids_the_regional_endpoint_and_a_signed_session(monkeypatch):
    monkeypatch.setenv("LB_TRACE_BACKEND", "xray")
    monkeypatch.setenv("AWS_REGION", "us-west-2")
    monkeypatch.setattr(
        "legacybridge.aws.xray.signed_session", lambda region, profile=None: requests.Session()
    )
    provider = build_provider("t")
    assert isinstance(provider.id_generator, XRayIdGenerator)
    exporter = provider._active_span_processor._span_processors[0].span_exporter
    assert exporter._endpoint == "https://xray.us-west-2.amazonaws.com/v1/traces"
    assert tracing.XRAY_EXPORT_TIMEOUT_S > tracing.EXPORT_TIMEOUT_S
