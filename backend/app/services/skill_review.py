"""Read-only Skill candidate reviews and explicit patch proposal decisions."""

from __future__ import annotations

import hashlib
import inspect
import json
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Literal

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

from app.services.skill_package_validator import SCHEMA_DIR


ReviewScope = Literal["full", "incremental"]
PatchDecision = Literal["apply", "reject"]

_ASSERTION = re.compile(r"\bmust(?P<negation>\s+not)?\s+(?P<subject>[^\n.!?]+)", re.IGNORECASE)


class SkillReviewError(ValueError):
    """Raised when review input cannot be safely inspected or recorded."""


@dataclass(frozen=True)
class ReviewProvenance:
    """Non-secret provider and session metadata required by skill-review-v1."""

    purpose: str
    session_id: str
    provider: str
    requested_model: str
    effective_model: str
    response_model: str
    declared_context_window_tokens: int
    requested_max_output_tokens: int

    def to_dict(self) -> dict[str, str | int]:
        return {
            "purpose": self.purpose,
            "session_id": self.session_id,
            "provider": self.provider,
            "requested_model": self.requested_model,
            "effective_model": self.effective_model,
            "response_model": self.response_model,
            "declared_context_window_tokens": self.declared_context_window_tokens,
            "requested_max_output_tokens": self.requested_max_output_tokens,
        }


@dataclass(frozen=True)
class ReviewLocation:
    path: str
    field: str = "content"


@dataclass(frozen=True)
class ReviewFinding:
    finding_id: str
    code: str
    severity: str
    summary: str
    locations: tuple[ReviewLocation, ...]
    reason: str
    impact: str
    recommendation: str
    disposition: str = "open"

    def to_dict(self) -> dict[str, Any]:
        return {
            "finding_id": self.finding_id,
            "severity": self.severity,
            "summary": self.summary,
            "locations": [{"path": item.path, "field": item.field} for item in self.locations],
            "reason": self.reason,
            "impact": self.impact,
            "recommendation": self.recommendation,
            "disposition": self.disposition,
        }


@dataclass(frozen=True)
class PatchProposal:
    patch_id: str
    finding_ids: tuple[str, ...]
    summary: str
    target_path: str
    base_digest: str
    unified_diff: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "patch_id": self.patch_id,
            "finding_ids": list(self.finding_ids),
            "summary": self.summary,
            "target_path": self.target_path,
            "base_digest": self.base_digest,
            "unified_diff": self.unified_diff,
        }


@dataclass(frozen=True)
class SkillReview:
    review_id: str
    build_id: str
    draft_id: str
    skill_id: str
    content_digest: str
    scope: ReviewScope
    reviewed_paths: tuple[str, ...]
    reviewed_file_digests: tuple[dict[str, str], ...]
    provenance: ReviewProvenance
    reviewed_at: str
    decision: str
    findings: tuple[ReviewFinding, ...]
    proposed_patches: tuple[PatchProposal, ...]
    review_evidence_digest: str

    def to_document(self) -> dict[str, Any]:
        evidence = {
            **self.provenance.to_dict(),
            "review_kind": self.scope,
            "reviewed_paths": list(self.reviewed_paths),
            "reviewed_file_digests": list(self.reviewed_file_digests),
            "reviewed_at": self.reviewed_at,
            "decision": self.decision,
            "findings": [item.to_dict() for item in self.findings],
            "proposed_patches": [item.to_dict() for item in self.proposed_patches],
        }
        return {
            "schema_version": "skill-review-v1",
            "review_id": self.review_id,
            "skill_id": self.skill_id,
            "content_digest": self.content_digest,
            "review_evidence_digest": self.review_evidence_digest,
            "review_evidence": evidence,
        }


@dataclass(frozen=True)
class PatchProposalDecision:
    review_id: str
    patch_id: str
    decision: PatchDecision
    proposal_state: str
    actor: str
    decided_at: str


class SkillReviewService:
    """Creates review records without changing a Draft or candidate artifact.

    ``record_review`` and ``record_patch_decision`` are intentionally small
    store hooks. Task 6's store integration can make them durable without
    changing this read-only review behavior.
    """

    def __init__(self, store: Any) -> None:
        self.store = store
        self._reviews: dict[str, SkillReview] = {}
        self._decisions: list[PatchProposalDecision] = []

    def record_review(self, *, build_id: str, review_evidence: dict[str, Any]) -> Any:
        build = self.store.get_build(str(build_id))
        if getattr(build, "status", None) != "built":
            raise SkillReviewError("only built candidates can be reviewed")
        _assert_build_digest_matches_current_candidate(build, Path(build.unpacked_root))
        draft = _get_draft_if_available(self.store, str(build.draft_id))
        skill_id = str(getattr(build, "skill_id", "") or getattr(draft, "skill_id", "") or "skill.review-candidate")
        evidence = json.loads(json.dumps(review_evidence, ensure_ascii=False))
        reviewed_paths = _attested_reviewed_paths(build, evidence)
        evidence["reviewed_paths"] = list(reviewed_paths)
        evidence["reviewed_file_digests"] = _attested_reviewed_file_digests(build, reviewed_paths, evidence)
        review_id = f"review_{uuid.uuid4().hex}"
        review_record = {
            "schema_version": "skill-review-v1",
            "review_id": review_id,
            "skill_id": skill_id,
            "content_digest": str(build.content_digest),
            "review_evidence_digest": _json_digest(evidence),
            "review_evidence": evidence,
        }
        _validate_review_record(review_record)
        _validate_review_decision(evidence)
        stored = _call_store_review_hook(self.store, build, review_record)
        return stored if stored is not None else review_record

    def review_build(
        self,
        build_id: str,
        *,
        scope: ReviewScope | str,
        provenance: ReviewProvenance,
        changed_paths: Iterable[str] | None = None,
        proposed_patches: Iterable[PatchProposal] = (),
    ) -> SkillReview:
        build = self.store.get_build(str(build_id))
        if getattr(build, "status", None) != "built":
            raise SkillReviewError("only built candidates can be reviewed")
        root = Path(build.unpacked_root)
        _assert_build_digest_matches_current_candidate(build, root)
        selected_paths = _reviewed_paths(root, scope=scope, changed_paths=changed_paths)
        reviewed_file_digests = tuple(_file_digest_entries(root, selected_paths))
        findings = _semantic_contradictions(root, selected_paths)
        if reviewed_file_digests != tuple(_file_digest_entries(root, selected_paths)):
            raise SkillReviewError("candidate changed during review")
        patches = tuple(proposed_patches)
        _validate_patches(root, patches, findings)
        normalized_scope: ReviewScope = scope  # type: ignore[assignment]
        reviewed_at = _now()
        decision = "changes_requested" if findings else "approved"
        draft = _get_draft_if_available(self.store, str(build.draft_id))
        evidence = {
            **provenance.to_dict(),
            "review_kind": normalized_scope,
            "reviewed_paths": list(selected_paths),
            "reviewed_file_digests": list(reviewed_file_digests),
            "reviewed_at": reviewed_at,
            "decision": decision,
            "findings": [item.to_dict() for item in findings],
            "proposed_patches": [item.to_dict() for item in patches],
        }
        review = SkillReview(
            review_id=f"review_{uuid.uuid4().hex}",
            build_id=str(build.build_id),
            draft_id=str(build.draft_id),
            skill_id=str(getattr(build, "skill_id", "") or getattr(draft, "skill_id", "") or "skill.review-candidate"),
            content_digest=str(build.content_digest),
            scope=normalized_scope,
            reviewed_paths=selected_paths,
            reviewed_file_digests=reviewed_file_digests,
            provenance=provenance,
            reviewed_at=reviewed_at,
            decision=decision,
            findings=findings,
            proposed_patches=patches,
            review_evidence_digest=_json_digest(evidence),
        )
        self._reviews[review.review_id] = review
        _validate_review_record(review.to_document())
        _call_store_review_hook(self.store, build, review.to_document(), fallback=review)
        return review

    def decide_patch(
        self,
        review_id: str,
        patch_id: str,
        *,
        decision: PatchDecision | str,
        actor: str,
    ) -> PatchProposalDecision:
        review = self._reviews.get(str(review_id))
        patch_ids = _patch_ids_for_review(self.store, review_id) if review is None else {
            patch.patch_id for patch in review.proposed_patches
        }
        if decision not in {"apply", "reject"}:
            raise SkillReviewError("patch decision must be 'apply' or 'reject'")
        if str(patch_id) not in patch_ids:
            raise KeyError(patch_id)
        clean_actor = str(actor).strip()
        if not clean_actor:
            raise SkillReviewError("patch decision actor is required")
        record = PatchProposalDecision(
            review_id=str(review_id),
            patch_id=str(patch_id),
            decision=decision,
            proposal_state="apply_recorded" if decision == "apply" else "rejected",
            actor=clean_actor,
            decided_at=_now(),
        )
        self._decisions.append(record)
        _call_store_hook(self.store, "record_patch_decision", record)
        return record

    def get_review(self, review_id: str) -> SkillReview:
        try:
            return self._reviews[str(review_id)]
        except KeyError:
            raise KeyError(review_id) from None


def _reviewed_paths(root: Path, *, scope: ReviewScope | str, changed_paths: Iterable[str] | None) -> tuple[str, ...]:
    if not root.is_dir() or root.is_symlink():
        raise SkillReviewError("candidate source must be a real directory")
    if scope == "full":
        return tuple(path.relative_to(root).as_posix() for path in _source_files(root))
    if scope != "incremental":
        raise SkillReviewError("review scope must be 'full' or 'incremental'")
    paths = tuple(sorted({_safe_relative_path(path) for path in changed_paths or ()}))
    if not paths:
        raise SkillReviewError("incremental review requires changed_paths")
    for relative in paths:
        target = root / relative
        if not target.is_file() or target.is_symlink():
            raise SkillReviewError(f"changed path is not a candidate file: {relative}")
    return paths


def _attested_reviewed_paths(build: Any, evidence: dict[str, Any]) -> tuple[str, ...]:
    root = Path(build.unpacked_root)
    scope = evidence.get("review_kind")
    provided = tuple(str(path) for path in evidence.get("reviewed_paths") or ())
    if scope == "full":
        current = tuple(path.relative_to(root).as_posix() for path in _source_files(root))
        if not current:
            raise SkillReviewError("full review has no candidate files")
        if provided in {(), ("__FULL_CANDIDATE__",)}:
            return current
        if tuple(sorted(provided)) != current:
            raise SkillReviewError("full review coverage does not match candidate files")
        return current
    if scope == "incremental":
        return _reviewed_paths(root, scope="incremental", changed_paths=provided)
    raise SkillReviewError("review scope must be 'full' or 'incremental'")


def _attested_reviewed_file_digests(build: Any, reviewed_paths: tuple[str, ...], evidence: dict[str, Any]) -> list[dict[str, str]]:
    root = Path(build.unpacked_root)
    expected = _file_digest_entries(root, reviewed_paths)
    provided = evidence.get("reviewed_file_digests")
    if provided is None:
        raise SkillReviewError("review evidence must include reviewed_file_digests")
    normalized = sorted(
        [{"path": str(item["path"]), "digest": str(item["digest"])} for item in provided],
        key=lambda item: item["path"],
    )
    if normalized != expected:
        raise SkillReviewError("reviewed_file_digests do not match candidate files")
    return expected


def _file_digest_entries(root: Path, paths: Iterable[str]) -> list[dict[str, str]]:
    return [
        {"path": relative, "digest": _sha256_path(root / relative)}
        for relative in sorted(str(path) for path in paths)
    ]


def _assert_build_digest_matches_current_candidate(build: Any, root: Path) -> None:
    file_digest_map = {
        "schema_version": "skill-file-digests-v1",
        "files": {entry["path"]: entry["digest"] for entry in _file_digest_entries(root, [path.relative_to(root).as_posix() for path in _source_files(root)])},
    }
    digest_map_path = getattr(build, "file_digest_map_path", None)
    if digest_map_path is not None and Path(digest_map_path).exists():
        recorded = json.loads(Path(digest_map_path).read_text(encoding="utf-8"))
        if recorded != file_digest_map:
            raise SkillReviewError("candidate_digest_mismatch")


def _semantic_contradictions(root: Path, reviewed_paths: Iterable[str]) -> tuple[ReviewFinding, ...]:
    assertions: dict[str, dict[bool, list[ReviewLocation]]] = {}
    for relative in reviewed_paths:
        path = root / relative
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for match in _ASSERTION.finditer(text):
            proposition = _normalize_proposition(match.group("subject"))
            if proposition:
                assertions.setdefault(proposition, {False: [], True: []})[bool(match.group("negation"))].append(
                    ReviewLocation(path=relative)
                )
    findings: list[ReviewFinding] = []
    for proposition, polarities in sorted(assertions.items()):
        if not polarities[False] or not polarities[True]:
            continue
        locations = tuple(sorted({*polarities[False], *polarities[True]}, key=lambda item: (item.path, item.field)))
        identity = hashlib.sha256((proposition + "\0" + "\0".join(item.path for item in locations)).encode("utf-8")).hexdigest()[:16]
        findings.append(
            ReviewFinding(
                finding_id=f"finding.semantic-contradiction-{identity}",
                code="semantic_contradiction",
                severity="p1",
                summary=f"Conflicting required behavior: {proposition}",
                locations=locations,
                reason="The reviewed candidate contains both a required and prohibited form of the same behavior.",
                impact="An agent cannot satisfy both instructions deterministically.",
                recommendation="Choose one requirement and remove or qualify the conflicting assertion.",
            )
        )
    return tuple(findings)


def _source_files(root: Path) -> list[Path]:
    files = [path for path in root.rglob("*") if path.is_file() and not path.is_symlink()]
    return sorted(files, key=lambda path: path.relative_to(root).as_posix())


def _safe_relative_path(value: str) -> str:
    path = Path(str(value))
    if not value or path.is_absolute() or ".." in path.parts or str(path) in {".", ""}:
        raise SkillReviewError("unsafe changed path")
    return path.as_posix()


def _normalize_proposition(value: str) -> str:
    return " ".join(value.lower().strip().split())


def _validate_patches(root: Path, patches: tuple[PatchProposal, ...], findings: tuple[ReviewFinding, ...]) -> None:
    seen: set[str] = set()
    finding_ids = {finding.finding_id for finding in findings}
    for patch in patches:
        if patch.patch_id in seen:
            raise SkillReviewError(f"duplicate patch proposal: {patch.patch_id}")
        seen.add(patch.patch_id)
        relative = _safe_relative_path(patch.target_path)
        target = root / relative
        if not target.is_file():
            raise SkillReviewError(f"patch target is not a candidate file: {relative}")
        if patch.base_digest != _sha256_path(target):
            raise SkillReviewError(f"patch base_digest does not match candidate file: {relative}")
        unknown = set(patch.finding_ids) - finding_ids
        if unknown:
            raise SkillReviewError(f"patch references unknown finding: {sorted(unknown)[0]}")
        if _diff_targets(patch.unified_diff) != {relative}:
            raise SkillReviewError(f"patch unified diff does not target declared file: {relative}")


def _patch_ids_for_review(store: Any, review_id: str) -> set[str]:
    method = getattr(store, "get_review", None)
    if not callable(method):
        raise KeyError(review_id)
    review = method(str(review_id))
    document = json.loads(Path(review.record_path).read_text(encoding="utf-8"))
    patches = document.get("review_evidence", {}).get("proposed_patches", [])
    return {str(patch["patch_id"]) for patch in patches}


def _diff_targets(unified_diff: str) -> set[str]:
    targets: set[str] = set()
    old_paths: list[str] = []
    new_paths: list[str] = []
    for line in unified_diff.splitlines():
        if line.startswith("--- a/"):
            old_paths.append(line[len("--- a/") :].strip())
        elif line.startswith("+++ b/"):
            new_paths.append(line[len("+++ b/") :].strip())
        elif line.startswith("--- ") and not line.startswith("--- /dev/null"):
            old_paths.append(line[len("--- ") :].strip())
        elif line.startswith("+++ ") and not line.startswith("+++ /dev/null"):
            new_paths.append(line[len("+++ ") :].strip())
    if len(old_paths) != len(new_paths):
        return set()
    for old, new in zip(old_paths, new_paths):
        if old != new:
            return set()
        targets.add(old)
    return targets


def _call_store_review_hook(store: Any, build: Any, review_record: dict[str, Any], fallback: Any | None = None) -> Any:
    method = getattr(store, "record_review", None)
    if not callable(method):
        return None
    try:
        signature = inspect.signature(method)
    except (TypeError, ValueError):
        return method(fallback if fallback is not None else review_record)
    if any(parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in signature.parameters.values()):
        return method(
            build_id=str(build.build_id),
            review_record=review_record,
            record_path=_review_record_path(build, str(review_record["review_id"])),
        )
    parameter_names = set(signature.parameters)
    if {"build_id", "review_record", "record_path"} <= parameter_names:
        return method(
            build_id=str(build.build_id),
            review_record=review_record,
            record_path=_review_record_path(build, str(review_record["review_id"])),
        )
    return method(fallback if fallback is not None else review_record)


def _call_store_hook(store: Any, name: str, value: Any) -> None:
    method = getattr(store, name, None)
    if callable(method):
        method(value)


def _get_draft_if_available(store: Any, draft_id: str) -> Any | None:
    method = getattr(store, "get_draft", None)
    return method(draft_id) if callable(method) else None


def _schema_document(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _review_schema_validator() -> Draft202012Validator:
    resources = [Resource.from_contents(_schema_document(path)) for path in SCHEMA_DIR.glob("*.schema.json")]
    registry = Registry().with_resources((resource.id(), resource) for resource in resources)
    return Draft202012Validator(
        _schema_document(SCHEMA_DIR / "skill-review-v1.schema.json"),
        registry=registry,
        format_checker=FormatChecker(),
    )


def _validate_review_record(record: dict[str, Any]) -> None:
    errors = sorted(_review_schema_validator().iter_errors(record), key=lambda error: list(error.absolute_path))
    if errors:
        path = ".".join(str(segment) for segment in errors[0].absolute_path) or "$"
        raise SkillReviewError(f"review schema validation failed at {path}: {errors[0].message}")


def _validate_review_decision(evidence: dict[str, Any]) -> None:
    decision = str(evidence.get("decision") or "")
    findings = evidence.get("findings") if isinstance(evidence.get("findings"), list) else []
    if decision == "approved" and findings:
        raise SkillReviewError("approved reviews cannot contain findings")
    if decision == "acknowledged":
        for finding in findings:
            disposition = str(finding.get("disposition") or "") if isinstance(finding, dict) else ""
            if disposition not in {"acknowledged", "resolved"}:
                raise SkillReviewError("acknowledged reviews require resolved or acknowledged findings")


def _sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _review_record_path(build: Any, review_id: str) -> Path:
    build_root = getattr(build, "build_root", None)
    if build_root is not None:
        root = Path(build_root)
    else:
        root = Path(build.manifest_path).parent
    return root / "reviews" / review_id / "skill-review-v1.json"


def _json_digest(payload: dict[str, Any]) -> str:
    data = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return f"sha256:{hashlib.sha256(data).hexdigest()}"
