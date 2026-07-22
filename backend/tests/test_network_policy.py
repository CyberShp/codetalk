import socket

import pytest


def test_intranet_policy_allows_loopback_private_and_explicit_internal_hosts():
    from app.services.network_policy import IntranetNetworkPolicy

    policy = IntranetNetworkPolicy(
        policy_id="corp-approved-v1",
        allowed_hosts={"models.corp.example"},
        resolver=lambda host, _port: {
            "models.corp.example": ["10.42.7.18"],
        }.get(host, []),
    )

    assert policy.evaluate_url("http://127.0.0.1:7100/api/repos").allowed
    assert policy.evaluate_url("http://[::1]:3004/health").allowed
    assert policy.evaluate_url("https://models.corp.example/v1/chat").allowed


def test_intranet_policy_rejects_public_url_before_a_connection_is_attempted():
    from app.services.network_policy import IntranetNetworkPolicy, NetworkEgressBlocked

    resolved = False

    def resolver(_host: str, _port: int):
        nonlocal resolved
        resolved = True
        return ["93.184.216.34"]

    policy = IntranetNetworkPolicy(policy_id="corp-approved-v1", resolver=resolver)
    decision = policy.evaluate_url("https://example.com/secret")

    assert decision.allowed is False
    assert decision.reason == "host_not_allowlisted"
    assert resolved is False
    with pytest.raises(NetworkEgressBlocked, match="公网出口已被内网策略拒绝"):
        policy.require_url("https://example.com/secret")


def test_allowlisted_hostname_is_rejected_when_dns_resolves_to_a_public_address():
    from app.services.network_policy import IntranetNetworkPolicy

    policy = IntranetNetworkPolicy(
        policy_id="corp-approved-v1",
        allowed_hosts={"models.corp.example"},
        resolver=lambda _host, _port: ["8.8.8.8"],
    )

    decision = policy.evaluate_url("https://models.corp.example/v1")

    assert decision.allowed is False
    assert decision.reason == "resolved_public_address"


def test_policy_snapshot_is_json_safe_and_does_not_include_runtime_resolver():
    from app.services.network_policy import IntranetNetworkPolicy

    policy = IntranetNetworkPolicy(
        policy_id="corp-approved-v1",
        allowed_hosts={"models.corp.example"},
        allowed_cidrs={"10.42.0.0/16"},
        resolver=lambda _host, _port: ["10.42.7.18"],
    )

    assert policy.snapshot() == {
        "network_mode": "intranet_deny_public",
        "allowed_endpoint_policy_id": "corp-approved-v1",
        "allowed_hosts": ["models.corp.example"],
        "allowed_cidrs": ["10.42.0.0/16"],
        "telemetry": "disabled",
        "remote_tracing": "disabled",
        "hosted_mcp": "forbidden",
        "external_model_api": "forbidden",
    }
