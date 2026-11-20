"""The allowlist is the control that keeps this a scanner and not a weapon."""

from __future__ import annotations

import pytest

from app.policy import (
    PolicyViolation,
    ScanPolicy,
    host_matches_scope,
    is_domain,
    normalise_target,
)


@pytest.mark.parametrize(
    "host,scope,expected",
    [
        ("example.com", "example.com", True),
        ("api.example.com", "example.com", True),
        ("deep.sub.example.com", "example.com", True),
        ("notexample.com", "example.com", False),
        ("example.com.evil.net", "example.com", False),
        ("evil.net", "example.com", False),
        ("api.example.com", "*.example.com", True),
        ("example.com", "*.example.com", True),
        ("93.184.216.34", "93.184.216.0/24", True),
        ("93.184.217.34", "93.184.216.0/24", False),
        ("10.0.0.7", "10.0.0.7", True),
        ("10.0.0.8", "10.0.0.7", False),
        ("EXAMPLE.COM", "example.com", True),
    ],
)
def test_host_matches_scope(host, scope, expected):
    assert host_matches_scope(host, scope) is expected


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("https://example.com/path?q=1", "example.com"),
        ("http://Example.COM:8080/x", "example.com"),
        ("example.com", "example.com"),
        ("  EXAMPLE.com  ", "example.com"),
    ],
)
def test_normalise_target(raw, expected):
    assert normalise_target(raw) == expected


def test_normalise_rejects_garbage():
    with pytest.raises(PolicyViolation):
        normalise_target("://nope")


def test_default_policy_denies_everything_when_no_scopes():
    """Fail closed: an unconfigured server must not scan anything."""
    policy = ScanPolicy(scopes=[])
    assert policy.is_authorized("example.com") is False
    with pytest.raises(PolicyViolation) as excinfo:
        policy.assert_authorized("example.com")
    assert "no scopes configured" in str(excinfo.value)


def test_policy_denies_unlisted_target():
    policy = ScanPolicy(scopes=["example.com"])
    assert policy.is_authorized("https://acme.test") is False
    with pytest.raises(PolicyViolation):
        policy.assert_authorized("acme.test")


def test_filter_seeds_splits_allowed_and_rejected():
    policy = ScanPolicy(scopes=["example.com"])
    allowed, rejected = policy.filter_seeds(
        ["example.com", "https://api.example.com/v1", "evil.net", "cdn.example.com"]
    )
    assert set(allowed) == {"example.com", "https://api.example.com/v1", "cdn.example.com"}
    assert rejected == ["evil.net"]


def test_active_modules_require_server_side_switch():
    locked = ScanPolicy(scopes=["example.com"], allow_active_modules=False)
    with pytest.raises(PolicyViolation):
        locked.assert_active_allowed()
    ScanPolicy(scopes=["example.com"], allow_active_modules=True).assert_active_allowed()


def test_allow_any_scope_is_explicit_escape_hatch():
    policy = ScanPolicy(scopes=[], allow_any_scope=True)
    assert policy.is_authorized("anything.example.org") is True


@pytest.mark.parametrize(
    "value,expected",
    [("example.com", True), ("sub.example.com", True), ("93.184.216.34", False), ("nope", False)],
)
def test_is_domain(value, expected):
    assert is_domain(value) is expected
