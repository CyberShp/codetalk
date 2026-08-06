"""TDD contract for F014 Task 6 review records and patch decisions."""

from __future__ import annotations

import hashlib
import importlib
from dataclasses import dataclass, replace
from pathlib import Path
from types import ModuleType

import pytest


def _review_module() -> ModuleType:
    try:
        return importlib.import_module("app.services.skill_review")
    except ModuleNotFoundError as exc:
        if exc.name == "app.services.skill_review":
            pytest.fail("RED: app.services.skill_review has not been implemented")
        raise


def _digest(value: str) -> str:
    return f"sha256:{hashlib.sha256(value.encode('utf-8')).hexdigest()}"


@dataclass(frozen=True)
class _Build:
    build_id: str
    draft_id: str
    status: str
    content_digest: str
    unpacked_root: Path
    file_digest_map_path: Path | None = None


class _Store:
    def __init__(self, build: _Build) -> None:
        self.build = build
        self.recorded_reviews: list[object] = []
        self.recorded_decisions: list[object] = []

    def get_build(self, build_id: str) -> _Build:
        assert build_id == self.build.build_id
        return self.build

    def record_review(self, review: object) -> None:
        self.recorded_reviews.append(review)

    def record_patch_decision(self, decision: object) -> None:
        self.recorded_decisions.append(decision)


def _service(tmp_path: Path) -> tuple[ModuleType, object, _Store, Path]:
    source = tmp_path / "candidate-source"
    source.mkdir()
    (source / "guide.md").write_text("The deployer must retain audit logs.\n", encoding="utf-8")
    (source / "notes.md").write_text("Normal review notes.\n", encoding="utf-8")
    store = _Store(
        _Build(
            build_id="build_123",
            draft_id="draft_123",
            status="built",
            content_digest=_digest("candidate"),
            unpacked_root=source,
        )
    )
    module = _review_module()
    return module, module.SkillReviewService(store), store, source


def _provenance(module: ModuleType):
    return module.ReviewProvenance(
        purpose="release review",
        session_id="review-session-123",
        provider="deepseek",
        requested_model="deepseek-v4-flash",
        effective_model="deepseek-v4-flash",
        response_model="deepseek-v4-flash",
        declared_context_window_tokens=200000,
        requested_max_output_tokens=4096,
    )


def test_full_review_detects_seeded_semantic_contradiction_deterministically(tmp_path: Path) -> None:
    module, service, store, source = _service(tmp_path)
    target = source / "conflict.md"
    target.write_text("The deployer must not retain audit logs.\n", encoding="utf-8")
    before = {path.relative_to(source): path.read_bytes() for path in source.rglob("*") if path.is_file()}

    first = service.review_build("build_123", scope="full", provenance=_provenance(module))
    second = service.review_build("build_123", scope="full", provenance=_provenance(module))

    assert first.content_digest == store.build.content_digest
    assert first.scope == "full"
    assert first.decision == "changes_requested"
    assert first.findings == second.findings
    assert len(first.findings) == 1
    finding = first.findings[0]
    assert finding.code == "semantic_contradiction"
    assert {location.path for location in finding.locations} == {"conflict.md", "guide.md"}
    assert first.proposed_patches == ()
    assert {path.relative_to(source): path.read_bytes() for path in source.rglob("*") if path.is_file()} == before
    assert len(store.recorded_reviews) == 2


def test_incremental_review_limits_findings_to_changed_paths_but_full_review_scans_all(tmp_path: Path) -> None:
    module, service, _, source = _service(tmp_path)
    (source / "conflict.md").write_text("The deployer must not retain audit logs.\n", encoding="utf-8")

    incremental = service.review_build(
        "build_123", scope="incremental", changed_paths=["notes.md"], provenance=_provenance(module)
    )
    full = service.review_build("build_123", scope="full", provenance=_provenance(module))

    assert incremental.reviewed_paths == ("notes.md",)
    assert incremental.findings == ()
    assert incremental.decision == "approved"
    assert set(full.reviewed_paths) == {"conflict.md", "guide.md", "notes.md"}
    assert full.decision == "changes_requested"


def test_provenance_has_only_non_secret_contract_fields_and_patch_decisions_do_not_mutate_draft(tmp_path: Path) -> None:
    module, service, store, source = _service(tmp_path)
    (source / "conflict.md").write_text("The deployer must not retain audit logs.\n", encoding="utf-8")
    finding_id = service.review_build("build_123", scope="full", provenance=_provenance(module)).findings[0].finding_id
    original = (source / "guide.md").read_bytes()
    proposal = module.PatchProposal(
        patch_id="patch.audit-logs",
        finding_ids=(finding_id,),
        summary="Clarify the audit log rule",
        target_path="guide.md",
        base_digest=_digest(original.decode("utf-8")),
        unified_diff=(
            "--- a/guide.md\n"
            "+++ b/guide.md\n"
            "@@ -1 +1 @@\n"
            "-The deployer must retain audit logs.\n"
            "+The deployer must retain audit logs and explain retention boundaries.\n"
        ),
    )

    review = service.review_build(
        "build_123", scope="full", provenance=_provenance(module), proposed_patches=[proposal]
    )
    applied = service.decide_patch(review.review_id, proposal.patch_id, decision="apply", actor="human.reviewer")
    rejected = service.decide_patch(review.review_id, proposal.patch_id, decision="reject", actor="human.reviewer")

    assert review.proposed_patches == (proposal,)
    assert review.provenance.to_dict() == {
        "purpose": "release review",
        "session_id": "review-session-123",
        "provider": "deepseek",
        "requested_model": "deepseek-v4-flash",
        "effective_model": "deepseek-v4-flash",
        "response_model": "deepseek-v4-flash",
        "declared_context_window_tokens": 200000,
        "requested_max_output_tokens": 4096,
    }
    assert applied.decision == "apply"
    assert applied.proposal_state == "apply_recorded"
    assert rejected.decision == "reject"
    assert rejected.proposal_state == "rejected"
    assert (source / "guide.md").read_bytes() == original
    assert store.recorded_decisions == [applied, rejected]


def test_patch_proposal_rejects_stale_base_unknown_finding_and_wrong_diff_target(tmp_path: Path) -> None:
    module, service, _, source = _service(tmp_path)
    (source / "conflict.md").write_text("The deployer must not retain audit logs.\n", encoding="utf-8")
    finding_id = service.review_build("build_123", scope="full", provenance=_provenance(module)).findings[0].finding_id
    valid = {
        "patch_id": "patch.audit-logs",
        "finding_ids": (finding_id,),
        "summary": "Clarify the audit log rule",
        "target_path": "guide.md",
        "base_digest": _digest((source / "guide.md").read_text(encoding="utf-8")),
        "unified_diff": "--- a/guide.md\n+++ b/guide.md\n@@ -1 +1 @@\n-old\n+new\n",
    }

    with pytest.raises(module.SkillReviewError, match="base_digest"):
        service.review_build(
            "build_123",
            scope="full",
            provenance=_provenance(module),
            proposed_patches=[module.PatchProposal(**{**valid, "base_digest": _digest("stale")})],
        )
    with pytest.raises(module.SkillReviewError, match="finding"):
        service.review_build(
            "build_123",
            scope="full",
            provenance=_provenance(module),
            proposed_patches=[module.PatchProposal(**{**valid, "finding_ids": ("finding.unknown",)})],
        )
    with pytest.raises(module.SkillReviewError, match="diff"):
        service.review_build(
            "build_123",
            scope="full",
            provenance=_provenance(module),
            proposed_patches=[
                module.PatchProposal(**{**valid, "unified_diff": "--- a/other.md\n+++ b/other.md\n@@ -1 +1 @@\n-a\n+b\n"})
            ],
        )
    with pytest.raises(module.SkillReviewError, match="diff"):
        service.review_build(
            "build_123",
            scope="full",
            provenance=_provenance(module),
            proposed_patches=[
                module.PatchProposal(
                    **{
                        **valid,
                        "unified_diff": (
                            "--- a/guide.md\n+++ b/guide.md\n@@ -1 +1 @@\n-old\n+new\n"
                            "--- a/notes.md\n+++ b/notes.md\n@@ -1 +1 @@\n-old\n+new\n"
                        ),
                    }
                )
            ],
        )
    with pytest.raises(module.SkillReviewError, match="diff"):
        service.review_build(
            "build_123",
            scope="full",
            provenance=_provenance(module),
            proposed_patches=[
                module.PatchProposal(
                    **{
                        **valid,
                        "unified_diff": (
                            "--- a/guide.md\n+++ b/guide.md\n@@ -1 +1 @@\n-old\n+new\n"
                            "--- notes.md\n+++ notes.md\n@@ -1 +1 @@\n-old\n+new\n"
                        ),
                    }
                )
            ],
        )


def test_incremental_review_requires_a_safe_nonempty_changed_path_set(tmp_path: Path) -> None:
    module, service, _, _ = _service(tmp_path)

    with pytest.raises(module.SkillReviewError, match="changed_paths"):
        service.review_build("build_123", scope="incremental", provenance=_provenance(module))
    with pytest.raises(module.SkillReviewError, match="unsafe"):
        service.review_build(
            "build_123", scope="incremental", changed_paths=["../guide.md"], provenance=_provenance(module)
        )


def test_review_build_rejects_candidate_files_changed_after_build_digest_map(tmp_path: Path) -> None:
    module, service, _, source = _service(tmp_path)
    digest_map = source.parent / "file-digests.json"
    digest_map.write_text(
        '{"schema_version":"skill-file-digests-v1","files":{"guide.md":"' + _digest((source / "guide.md").read_text(encoding="utf-8")) + '","notes.md":"' + _digest((source / "notes.md").read_text(encoding="utf-8")) + '"}}\n',
        encoding="utf-8",
    )
    service.store.build = replace(service.store.build, file_digest_map_path=digest_map)
    (source / "guide.md").write_text("The deployer must not retain audit logs.\n", encoding="utf-8")

    with pytest.raises(module.SkillReviewError, match="candidate_digest_mismatch"):
        service.review_build("build_123", scope="full", provenance=_provenance(module))


def test_record_review_rejects_schema_invalid_or_secret_extra_evidence(tmp_path: Path) -> None:
    module, service, store, source = _service(tmp_path)
    evidence = {
        **_provenance(module).to_dict(),
        "review_kind": "full",
        "reviewed_paths": ["__FULL_CANDIDATE__"],
        "reviewed_file_digests": [
            {"path": "guide.md", "digest": _digest((source / "guide.md").read_text(encoding="utf-8"))},
            {"path": "notes.md", "digest": _digest((source / "notes.md").read_text(encoding="utf-8"))},
        ],
        "reviewed_at": "2026-08-05T00:00:00Z",
        "decision": "approved",
        "findings": [],
        "proposed_patches": [],
        "api_key": "sk-must-not-be-persisted",
    }

    with pytest.raises(module.SkillReviewError, match="schema"):
        service.record_review(build_id="build_123", review_evidence=evidence)

    assert store.recorded_reviews == []


def test_record_review_rejects_approved_evidence_with_open_findings(tmp_path: Path) -> None:
    module, service, store, source = _service(tmp_path)
    evidence = {
        **_provenance(module).to_dict(),
        "review_kind": "full",
        "reviewed_paths": ["__FULL_CANDIDATE__"],
        "reviewed_file_digests": [
            {"path": "guide.md", "digest": _digest((source / "guide.md").read_text(encoding="utf-8"))},
            {"path": "notes.md", "digest": _digest((source / "notes.md").read_text(encoding="utf-8"))},
        ],
        "reviewed_at": "2026-08-05T00:00:00Z",
        "decision": "approved",
        "findings": [
            {
                "finding_id": "finding.open-p1",
                "severity": "p1",
                "summary": "Open release blocker",
                "locations": [{"path": "guide.md", "field": "content"}],
                "reason": "The candidate still has an unresolved blocker.",
                "impact": "Publication would hide unresolved review risk.",
                "recommendation": "Resolve or explicitly acknowledge the finding.",
                "disposition": "open",
            }
        ],
        "proposed_patches": [],
    }

    with pytest.raises(module.SkillReviewError, match="approved reviews cannot contain findings"):
        service.record_review(build_id="build_123", review_evidence=evidence)

    assert store.recorded_reviews == []
