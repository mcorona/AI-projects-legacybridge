"""Model profiles (ADR-003) and the synthetic-data guard (ADR-010)."""

import pytest

from legacybridge.llm.profiles import (
    ProfileError,
    assert_data_allowed,
    load_profile,
)

ENV = {
    "LMSTUDIO_BASE_URL": "http://localhost:1234/v1",
    "LMSTUDIO_MODEL": "qwen/qwen3.6-35b-a3b",
    "OMNIROUTE_BASE_URL": "http://localhost:20128/v1",
    "OMNIROUTE_API_KEY": "sk-real-looking",
    "OMNIROUTE_MODEL": "auto/best-free",
}


def test_local_qwen_resolves_its_environment():
    p = load_profile("local-qwen", env=ENV)
    assert (p.base_url, p.model, p.api_key) == (
        ENV["LMSTUDIO_BASE_URL"],
        ENV["LMSTUDIO_MODEL"],
        None,
    )
    assert p.max_concurrency == 1 and p.max_tokens >= 4096 and not p.synthetic_data_only


def test_omniroute_free_is_serial_retries_and_synthetic_only():
    p = load_profile("omniroute-free", env=ENV)
    assert (p.max_concurrency, p.retries, p.synthetic_data_only) == (1, 3, True)
    assert p.api_key == "sk-real-looking" and p.model == "auto/best-free"


def test_unknown_profile_lists_the_available_ones():
    with pytest.raises(ProfileError, match="local-qwen.*omniroute-free"):
        load_profile("nope", env=ENV)


def test_a_missing_variable_names_it():
    with pytest.raises(ProfileError, match="LMSTUDIO_MODEL"):
        load_profile("local-qwen", env={k: v for k, v in ENV.items() if k != "LMSTUDIO_MODEL"})


@pytest.mark.parametrize("placeholder", ["changeme", "CHANGEME", "", " "])
def test_a_placeholder_api_key_is_refused(placeholder):
    with pytest.raises(ProfileError, match="OMNIROUTE_API_KEY"):
        load_profile("omniroute-free", env={**ENV, "OMNIROUTE_API_KEY": placeholder})


# --------------------------------------------------------------------------- ADR-010
class FakeConn:
    def __init__(self, marker):
        self.marker = marker

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql):
        marker = self.marker

        class R:
            def fetchone(self):
                return (marker,)

        return R()


def test_a_local_profile_needs_no_marker():
    assert_data_allowed(load_profile("local-qwen", env=ENV), connect=lambda *a, **k: FakeConn(None))


def test_a_third_party_profile_refuses_a_database_that_is_not_marked_synthetic():
    p = load_profile("omniroute-free", env=ENV)
    for marker in (None, "false", ""):
        with pytest.raises(ProfileError, match="synthetic"):
            assert_data_allowed(p, connect=lambda *a, m=marker, **k: FakeConn(m))
    assert_data_allowed(p, connect=lambda *a, **k: FakeConn("true"))


def test_an_unreachable_database_counts_as_real_data():
    def broken(*a, **k):
        raise RuntimeError("no database")

    with pytest.raises(ProfileError, match="could not be checked"):
        assert_data_allowed(load_profile("omniroute-free", env=ENV), connect=broken)
