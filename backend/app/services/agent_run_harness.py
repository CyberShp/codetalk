"""Agent run and artifact validation harness for CodeTalk workflows."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


@dataclass(frozen=True)
class AgentRunRecord:
    run_id: str
    provider: str
    command: list[str]
    cwd: str
    artifact_dir: str
    mcp_profile: str = ""
    status: str = "created"
    created_at: str = field(default_factory=_now)


@dataclass(frozen=True)
class ArtifactValidationResult:
    status: str
    provenance_status: str
    accepted_artifacts: list[str] = field(default_factory=list)
    rejected_artifacts: list[dict[str, str]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class AgentRunHarness:
    """Writes the reproducible envelope around an external Agent CLI run."""

    def __init__(self, artifact_dir: str | Path) -> None:
        self.artifact_dir = Path(artifact_dir)
        self.artifact_dir.mkdir(parents=True, exist_ok=True)

    def create_run(
        self,
        *,
        provider: str,
        command: list[str],
        cwd: str,
        workflow_snapshot: dict[str, Any],
        task_bundle: dict[str, Any],
        mcp_profile: str = "",
        run_id: str | None = None,
    ) -> AgentRunRecord:
        run = AgentRunRecord(
            run_id=run_id or _new_id("agent_run"),
            provider=provider,
            command=[str(part) for part in command],
            cwd=cwd,
            artifact_dir=str(self.artifact_dir),
            mcp_profile=mcp_profile,
        )
        self._write_json("agent_run.json", asdict(run))
        self._write_json("task_bundle.json", task_bundle)
        self._write_json("workflow_snapshot.json", workflow_snapshot)
        return run

    def record_raw_output(self, run_id: str, *, stdout: str, stderr: str = "") -> None:
        payload = "\n".join(part for part in [stdout, stderr] if part)
        self._write_text("raw_output.txt", _redact(payload))
        self._write_json(
            "runtime_events.jsonl",
            {
                "event": "raw_output_recorded",
                "run_id": run_id,
                "created_at": _now(),
            },
            append_jsonl=True,
        )

    def _write_json(self, filename: str, payload: Any, *, append_jsonl: bool = False) -> None:
        path = self.artifact_dir / filename
        if append_jsonl:
            with path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
            return
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")

    def _write_text(self, filename: str, content: str) -> None:
        (self.artifact_dir / filename).write_text(content, encoding="utf-8")


class ArtifactValidationHarness:
    """Validates Agent-produced artifacts before they become evidence."""

    def __init__(self, artifact_dir: str | Path) -> None:
        self.artifact_dir = Path(artifact_dir)

    def validate_mr_artifacts(self, *, required_artifacts: list[str]) -> ArtifactValidationResult:
        accepted: list[str] = []
        rejected: list[dict[str, str]] = []
        warnings: list[str] = []

        for artifact in required_artifacts:
            path = self.artifact_dir / artifact
            if not path.exists():
                rejected.append({"artifact": artifact, "reason": "missing_required_artifact"})
            else:
                accepted.append(artifact)
        if rejected:
            return ArtifactValidationResult(
                status="invalid",
                provenance_status="unverified_agent_claim",
                accepted_artifacts=accepted,
                rejected_artifacts=rejected,
            )

        snapshot = self._read_json("mr_snapshot.json")
        diff_text = (self.artifact_dir / "diff.patch").read_text(encoding="utf-8")
        changed_files = self._read_json("changed_files.json")
        if not isinstance(snapshot, dict):
            rejected.append({"artifact": "mr_snapshot.json", "reason": "invalid_json_object"})
        if not isinstance(changed_files, list):
            rejected.append({"artifact": "changed_files.json", "reason": "invalid_json_array"})

        for field_name in (
            "source", "mcp_profile", "mr_url", "project", "mr_id", "title",
            "source_branch", "target_branch", "base_commit", "head_commit",
            "diff_sha256", "changed_files_count",
        ):
            if isinstance(snapshot, dict) and snapshot.get(field_name) in {None, ""}:
                rejected.append({"artifact": "mr_snapshot.json", "reason": f"missing_{field_name}"})

        if isinstance(snapshot, dict):
            actual_sha = hashlib.sha256(diff_text.encode("utf-8")).hexdigest()
            if snapshot.get("diff_sha256") != actual_sha:
                rejected.append({"artifact": "diff.patch", "reason": "diff_sha256_mismatch"})

        if isinstance(changed_files, list):
            diff_paths = _paths_from_unified_diff(diff_text)
            for item in changed_files:
                item_path = str((item or {}).get("path") or "").replace("\\", "/")
                if item_path and item_path not in diff_paths:
                    warnings.append(f"changed file not present in diff: {item_path}")

        return ArtifactValidationResult(
            status="invalid" if rejected else "ok",
            provenance_status="agent_mcp_provenance" if not rejected else "unverified_agent_claim",
            accepted_artifacts=accepted,
            rejected_artifacts=rejected,
            warnings=warnings,
        )

    def _read_json(self, filename: str) -> Any:
        try:
            return json.loads((self.artifact_dir / filename).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None


def _paths_from_unified_diff(diff_text: str) -> set[str]:
    paths: set[str] = set()
    for line in diff_text.splitlines():
        if line.startswith("diff --git "):
            parts = line.split()
            for candidate in parts[-2:]:
                cleaned = re.sub(r"^[ab]/", "", candidate).replace("\\", "/")
                if cleaned:
                    paths.add(cleaned)
        elif line.startswith(("--- a/", "+++ b/")):
            paths.add(line[6:].replace("\\", "/"))
    return paths


_SECRET_RE = re.compile(
    r"(?i)\b(api[-_]?key|token|access[-_]?token|secret|password)\s*=\s*[^\s]+"
)
_BEARER_RE = re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]{8,}")


def _redact(text: str) -> str:
    value = _SECRET_RE.sub(lambda m: f"{m.group(1)}=<redacted>", text or "")
    return _BEARER_RE.sub(r"\1<redacted>", value)
