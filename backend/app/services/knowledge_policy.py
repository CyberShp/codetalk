"""Authority and CodeHub boundaries for historical knowledge.

The functions are intentionally framework-free so the later workflow/API layer
cannot accidentally broaden the CodeHub or evidence authority contract.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse


class CodeHubBoundaryError(ValueError):
    """Raised when returned CodeHub material exceeds the explicit MR request."""


@dataclass(frozen=True)
class AuthorityDecision:
    status: str
    missing_evidence: tuple[str, ...] = ()


def canonical_repository_identity(value: str) -> str:
    """Normalize an HTTPS/SSH remote into a credential-free host/path identity."""
    raw = str(value or "").strip()
    if not raw:
        return ""
    if "://" not in raw and "@" not in raw and raw.count("/") >= 1:
        raw = f"https://{raw}"
    if "://" not in raw and "@" in raw and ":" in raw:
        raw = f"ssh://{raw.replace(':', '/', 1)}"
    parsed = urlparse(raw)
    host = (parsed.hostname or "").lower()
    path = parsed.path.strip("/")
    if not host or not path:
        return ""
    if path.endswith(".git"):
        path = path[:-4]
    return f"{host}/{path}"


def resolve_knowledge_scope(
    *,
    workspace_remotes: list[str] | tuple[str, ...],
    mr_project_identity: str,
) -> tuple[str, str, str]:
    """Return (scope, canonical workspace identity, reason) without fuzzy mapping."""
    expected = canonical_repository_identity(mr_project_identity)
    candidates = [canonical_repository_identity(remote) for remote in workspace_remotes]
    candidates = [candidate for candidate in candidates if candidate]
    if not expected:
        return "personal_global", "", "missing_mr_project_identity"
    if expected in candidates:
        return "project", expected, "exact_remote_match"
    return "personal_global", "", "project_identity_mismatch"


def build_codehub_request(mr_url: str | None) -> dict[str, object] | None:
    """Create an explicit, read-only MR request; absent MR means no request."""
    normalized = str(mr_url or "").strip()
    if not normalized:
        return None
    return {
        "mr_url": normalized,
        "allowed_operations": ["read"],
        "max_reference_hops": 1,
        "search_enabled": False,
    }


def validate_codehub_response(
    request: dict[str, object] | None,
    source_manifest: list[dict[str, object]],
    returned_sources: list[dict[str, object]],
) -> bool:
    """Validate returned sources against an explicit MR and one-hop manifest."""
    if request is None:
        if source_manifest or returned_sources:
            raise CodeHubBoundaryError("no supplied MR permits no CodeHub sources")
        return True
    supplied_mr = str(request.get("mr_url") or "")
    if not supplied_mr:
        raise CodeHubBoundaryError("no supplied MR permits no CodeHub sources")
    if not source_manifest:
        raise CodeHubBoundaryError("source manifest is required")
    roots = [source for source in source_manifest if int(source.get("hop") or 0) == 0]
    if len(roots) != 1 or str(roots[0].get("source_url") or "") != supplied_mr:
        raise CodeHubBoundaryError("source manifest must contain exactly one supplied MR root")
    for source in source_manifest:
        _validate_codehub_source(source, supplied_mr)
    declared = {_manifest_key(source) for source in source_manifest}
    for source in returned_sources:
        _validate_codehub_source(source, supplied_mr)
        if _manifest_key(source) not in declared:
            raise CodeHubBoundaryError("returned source is unmanifested")
    return True


def _validate_codehub_source(source: dict[str, object], supplied_mr: str) -> None:
        operation = str(source.get("operation") or "read").lower()
        if operation != "read":
            raise CodeHubBoundaryError("CodeHub search and non-read operations are prohibited")
        hop = int(source.get("hop") or 0)
        if hop > 1:
            raise CodeHubBoundaryError("CodeHub traversal stops after one hop")
        source_url = str(source.get("source_url") or "")
        parent_url = str(source.get("parent_url") or "")
        if hop == 0 and source_url != supplied_mr:
            raise CodeHubBoundaryError("root source must be the supplied MR")
        if hop == 1 and parent_url != supplied_mr:
            raise CodeHubBoundaryError("direct reference must have the supplied MR as parent")


def authority_transition(
    requested_status: str,
    *,
    historical_hits: list[str] | tuple[str, ...] = (),
    current_evidence: list[str] | tuple[str, ...] = (),
    disconfirming_checks: list[dict[str, object]] | tuple[dict[str, object], ...] = (),
    current_disproof_evidence: list[str] | tuple[str, ...] = (),
) -> AuthorityDecision:
    """Keep history as a lead and require current evidence before conclusions."""
    requested = str(requested_status)
    if requested == "ruled_out":
        if current_disproof_evidence:
            return AuthorityDecision("ruled_out")
        return _best_supported_authority(current_evidence)
    if requested == "investigation_lead":
        return AuthorityDecision("investigation_lead")
    if requested == "candidate_finding":
        if current_evidence:
            return AuthorityDecision("candidate_finding")
        return AuthorityDecision("investigation_lead", ("current_evidence",))
    if requested == "confirmed_finding":
        missing: list[str] = []
        if not current_evidence:
            missing.append("current_evidence")
        if not _has_completed_disconfirming_checks(disconfirming_checks):
            missing.append("disconfirming_checks")
        if not missing:
            return AuthorityDecision("confirmed_finding")
        return AuthorityDecision("candidate_finding" if current_evidence else "investigation_lead", tuple(missing))
    raise ValueError(f"unsupported authority status: {requested}")


def _best_supported_authority(current_evidence: list[str] | tuple[str, ...]) -> AuthorityDecision:
    return AuthorityDecision("candidate_finding" if current_evidence else "investigation_lead", ("current_disproof_evidence",))


def _has_completed_disconfirming_checks(checks: list[dict[str, object]] | tuple[dict[str, object], ...]) -> bool:
    return bool(checks) and all(
        isinstance(check, dict)
        and bool(str(check.get("check") or "").strip())
        and str(check.get("status") or "") == "completed"
        and bool(str(check.get("result") or "").strip())
        for check in checks
    )


def _manifest_key(source: dict[str, object]) -> tuple[str, str, int, str]:
    return (
        str(source.get("source_url") or ""),
        str(source.get("parent_url") or ""),
        int(source.get("hop") or 0),
        str(source.get("operation") or "read").lower(),
    )
