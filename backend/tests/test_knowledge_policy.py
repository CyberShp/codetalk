from __future__ import annotations

import pytest

from app.services.knowledge_policy import (
    CodeHubBoundaryError,
    authority_transition,
    build_codehub_request,
    canonical_repository_identity,
    resolve_knowledge_scope,
    validate_codehub_response,
)


def test_repository_identity_matches_complete_host_and_project_and_falls_back_global():
    identity = canonical_repository_identity("ssh://git@CodeHub.EXAMPLE/storage/array.git")

    assert identity == "codehub.example/storage/array"
    assert canonical_repository_identity("https://CodeHub.EXAMPLE/Storage/Array.git") == "codehub.example/Storage/Array"
    assert resolve_knowledge_scope(
        workspace_remotes=["https://codehub.example/storage/array.git"],
        mr_project_identity="codehub.example/storage/array",
    ) == ("project", "codehub.example/storage/array", "exact_remote_match")
    assert resolve_knowledge_scope(
        workspace_remotes=["https://codehub.example/storage/array.git"],
        mr_project_identity="codehub.example/storage/other",
    ) == ("personal_global", "", "project_identity_mismatch")


def test_codehub_validates_manifest_and_returned_sources_against_explicit_single_hop_mr():
    assert build_codehub_request(None) is None
    request = build_codehub_request("https://codehub.example/storage/array/-/merge_requests/42")
    manifest = [
        {"source_url": request["mr_url"], "parent_url": "", "hop": 0, "operation": "read"},
        {"source_url": "https://codehub.example/storage/array/-/issues/17", "parent_url": request["mr_url"], "hop": 1, "operation": "read"},
    ]

    assert validate_codehub_response(request, manifest, list(manifest)) is True
    with pytest.raises(CodeHubBoundaryError, match="exactly one supplied MR root"):
        validate_codehub_response(request, [manifest[1]], [])
    with pytest.raises(CodeHubBoundaryError, match="search"):
        validate_codehub_response(request, [{**manifest[0], "operation": "search"}], [])
    with pytest.raises(CodeHubBoundaryError, match="one hop"):
        validate_codehub_response(request, [manifest[0], {**manifest[1], "hop": 2}], [])
    with pytest.raises(CodeHubBoundaryError, match="direct reference"):
        validate_codehub_response(request, [{**manifest[0]}, {**manifest[1], "parent_url": "https://codehub.example/wrong"}], [])
    with pytest.raises(CodeHubBoundaryError, match="search"):
        validate_codehub_response(request, manifest, [{**manifest[0], "operation": "search"}])
    with pytest.raises(CodeHubBoundaryError, match="one hop"):
        validate_codehub_response(request, manifest, [{**manifest[1], "hop": 2}])
    with pytest.raises(CodeHubBoundaryError, match="unmanifested"):
        validate_codehub_response(request, manifest, [{"source_url": "https://codehub.example/other/1", "parent_url": request["mr_url"], "hop": 1, "operation": "read"}])
    with pytest.raises(CodeHubBoundaryError, match="no supplied MR"):
        validate_codehub_response(None, [], [{"source_url": "https://codehub.example/search", "parent_url": "", "hop": 0, "operation": "read"}])
    with pytest.raises(CodeHubBoundaryError, match="no supplied MR"):
        validate_codehub_response(None, manifest, [])


def test_authority_transition_requires_current_and_disconfirming_evidence():
    assert authority_transition("investigation_lead", historical_hits=["pat_1"]).status == "investigation_lead"
    assert authority_transition("candidate_finding", historical_hits=["pat_1"]).status == "investigation_lead"
    assert authority_transition("candidate_finding", current_evidence=["ev_1"]).status == "candidate_finding"
    assert authority_transition("confirmed_finding", current_evidence=["ev_1"]).status == "candidate_finding"
    assert authority_transition(
        "confirmed_finding", current_evidence=["ev_1"], disconfirming_checks=[{"status": "running", "result": "pending"}]
    ).status == "candidate_finding"
    assert authority_transition(
        "confirmed_finding", current_evidence=["ev_1"], disconfirming_checks=[{}]
    ).status == "candidate_finding"
    assert authority_transition(
        "confirmed_finding",
        current_evidence=["ev_1"],
        disconfirming_checks=[{"status": "completed", "result": "not_found"}],
    ).status == "candidate_finding"
    assert authority_transition(
        "confirmed_finding", current_evidence=["ev_1"], disconfirming_checks=[{"check": "cross-file unlock", "status": "completed", "result": "not_found"}]
    ).status == "confirmed_finding"
    assert authority_transition("ruled_out", historical_hits=["pat_1"]).status == "investigation_lead"
    assert authority_transition("ruled_out", current_evidence=["ev_1"], current_disproof_evidence=["ev_2"]).status == "ruled_out"
