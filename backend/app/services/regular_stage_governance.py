from __future__ import annotations

import hashlib
import json
import shutil
import threading
import uuid
from contextlib import suppress
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from app.config import settings


_STREAMING_MARKDOWN_STAGES = {"business_flow", "test_strategy", "test_design"}
_CACHE_LOCKS_GUARD = threading.Lock()
_CACHE_LOCKS: dict[str, threading.Lock] = {}


def _cache_lock(cache_key: str) -> threading.Lock:
    with _CACHE_LOCKS_GUARD:
        return _CACHE_LOCKS.setdefault(cache_key, threading.Lock())


@dataclass(frozen=True)
class StageExecutionPolicy:
    model: str
    max_tokens: int
    provider_timeout_seconds: float
    total_timeout_seconds: float
    repair_timeout_seconds: float
    max_full_attempts: int = 1
    allow_format_repair: bool = True
    allow_degraded_output: bool = False
    streaming: bool = False
    repair_max_tokens: int = 500

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def stage_execution_policy(
    *,
    stage: dict[str, Any],
    global_max_tokens: int,
    overrides: dict[str, Any] | None = None,
) -> StageExecutionPolicy:
    stage_id = str(stage.get("id") or "stage").split("__", 1)[0]
    configured_tokens = max(256, int(stage.get("max_tokens") or global_max_tokens))
    explicit_streaming = bool(stage.get("streaming"))
    base = {
        "model": "",
        "max_tokens": min(global_max_tokens, configured_tokens),
        "provider_timeout_seconds": float(settings.regular_stage_provider_timeout_seconds),
        "total_timeout_seconds": float(settings.regular_stage_total_timeout_seconds),
        "repair_timeout_seconds": float(settings.regular_stage_repair_timeout_seconds),
        "max_full_attempts": 1,
        "allow_format_repair": True,
        "allow_degraded_output": stage_id in _STREAMING_MARKDOWN_STAGES or explicit_streaming,
        "streaming": stage_id in _STREAMING_MARKDOWN_STAGES or explicit_streaming,
        "repair_max_tokens": int(settings.regular_stage_repair_max_tokens),
    }
    if stage_id == "business_flow":
        base.update(
            {
                "max_tokens": min(
                    global_max_tokens,
                    int(settings.business_flow_max_tokens),
                    configured_tokens,
                ),
                "provider_timeout_seconds": float(
                    settings.business_flow_provider_timeout_seconds
                ),
                "total_timeout_seconds": float(
                    settings.business_flow_total_timeout_seconds
                ),
                "repair_timeout_seconds": float(
                    settings.business_flow_repair_timeout_seconds
                ),
                "streaming": bool(settings.business_flow_streaming),
                "allow_degraded_output": True,
            }
        )
    for key, value in (overrides or {}).items():
        if key in base and value is not None:
            base[key] = value
    base["max_full_attempts"] = 1
    base["provider_timeout_seconds"] = min(
        360.0, max(0.001, float(base["provider_timeout_seconds"]))
    )
    base["total_timeout_seconds"] = min(
        360.0, max(0.001, float(base["total_timeout_seconds"]))
    )
    base["provider_timeout_seconds"] = min(
        base["provider_timeout_seconds"], base["total_timeout_seconds"]
    )
    base["repair_timeout_seconds"] = min(
        60.0,
        max(0.001, float(base["repair_timeout_seconds"])),
        base["total_timeout_seconds"],
    )
    base["max_tokens"] = max(128, int(base["max_tokens"]))
    base["repair_max_tokens"] = min(600, max(128, int(base["repair_max_tokens"])))
    return StageExecutionPolicy(**base)


def regular_stage_cache_key(
    *,
    stage: dict[str, Any],
    plan: dict[str, Any],
    prompt: str,
    policy: StageExecutionPolicy,
    source_fingerprint: str,
    flow_fingerprint: str,
) -> str:
    payload = {
        "cache_version": settings.regular_stage_cache_version,
        "stage_id": str(stage.get("id") or ""),
        "artifact": str(stage.get("artifact") or ""),
        "repo_revision": str(plan.get("repo_revision") or ""),
        "analysis_target": str(plan.get("original_user_request") or plan.get("target") or ""),
        "source_fingerprint": source_fingerprint,
        "flow_fingerprint": flow_fingerprint,
        "workflow_version": str(plan.get("workflow_version") or ""),
        "model": policy.model,
        "prompt_sha256": _sha256_text(prompt),
        "schema": (stage.get("output_contract") or {}).get("schema")
        if isinstance(stage.get("output_contract"), dict)
        else None,
        "policy": policy.as_dict(),
    }
    return _sha256_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    )


def restore_regular_stage_cache(
    *,
    cache_root: Path | None,
    cache_key: str,
    artifact: str,
    output_path: Path,
) -> dict[str, Any] | None:
    if cache_root is None:
        return None
    entry = cache_root / cache_key
    metadata_path = entry / "cache_metadata.json"
    cached_artifact = entry / Path(artifact).name
    if not metadata_path.is_file() or not cached_artifact.is_file():
        return None
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if metadata.get("version") != settings.regular_stage_cache_version:
        return None
    if metadata.get("cache_key") != cache_key:
        return None
    if metadata.get("artifact_sha256") != _sha256_path(cached_artifact):
        return None
    result = metadata.get("stage_result")
    normalized = dict(result) if isinstance(result, dict) else {}
    if (
        str(normalized.get("status") or "") != "partial"
        and metadata.get("quality_status") != "verified"
    ):
        return None
    output_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(cached_artifact, output_path)
    return normalized


def store_regular_stage_cache(
    *,
    cache_root: Path | None,
    cache_key: str,
    artifact: str,
    output_path: Path,
    stage_result: dict[str, Any],
    quality_status: str = "candidate",
) -> None:
    if cache_root is None or not output_path.is_file():
        return
    cache_root.mkdir(parents=True, exist_ok=True)
    with _cache_lock(cache_key):
        entry = cache_root / cache_key
        suffix = uuid.uuid4().hex
        temporary = cache_root / f".{cache_key}.{suffix}.tmp"
        stale = cache_root / f".{cache_key}.{suffix}.stale"
        temporary.mkdir(parents=True, exist_ok=False)
        try:
            cached_artifact = temporary / Path(artifact).name
            shutil.copy2(output_path, cached_artifact)
            metadata = {
                "version": settings.regular_stage_cache_version,
                "cache_key": cache_key,
                "artifact": artifact,
                "artifact_sha256": _sha256_path(cached_artifact),
                "quality_status": quality_status,
                "stage_result": stage_result,
            }
            (temporary / "cache_metadata.json").write_text(
                json.dumps(metadata, ensure_ascii=False, indent=2, sort_keys=True),
                encoding="utf-8",
            )
            if entry.exists():
                entry.rename(stale)
            temporary.rename(entry)
        finally:
            shutil.rmtree(temporary, ignore_errors=True)
            shutil.rmtree(stale, ignore_errors=True)


def promote_regular_stage_caches(
    *,
    cache_root: Path | None,
    artifact_roots: list[Path],
    blocked_artifacts: set[str],
) -> list[str]:
    if cache_root is None:
        return []
    promoted: list[str] = []
    blocked = {Path(value).name for value in blocked_artifacts}
    for artifact_root in artifact_roots:
        if not artifact_root.is_dir():
            continue
        for result_path in artifact_root.rglob("stage_result.json"):
            try:
                result = json.loads(result_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            artifact = str(result.get("artifact") or "")
            cache_key = str(result.get("cache_key") or "")
            if (
                not artifact
                or not cache_key
                or Path(artifact).name in blocked
                or str(result.get("status") or "") != "completed"
            ):
                continue
            metadata_path = cache_root / cache_key / "cache_metadata.json"
            with _cache_lock(cache_key):
                try:
                    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                if metadata.get("cache_key") != cache_key:
                    continue
                run_artifact = artifact_root / artifact
                cached_artifact = metadata_path.parent / Path(
                    str(metadata.get("artifact") or artifact)
                ).name
                if not run_artifact.is_file() or not cached_artifact.is_file():
                    continue
                run_sha256 = _sha256_path(run_artifact)
                if (
                    metadata.get("artifact_sha256") != run_sha256
                    or _sha256_path(cached_artifact) != run_sha256
                ):
                    continue
                metadata["quality_status"] = "verified"
                temporary = metadata_path.with_name(
                    f".{metadata_path.name}.{uuid.uuid4().hex}.tmp"
                )
                temporary.write_text(
                    json.dumps(metadata, ensure_ascii=False, indent=2, sort_keys=True),
                    encoding="utf-8",
                )
                temporary.replace(metadata_path)
            promoted.append(Path(artifact).name)
    return sorted(set(promoted))


def cache_root(path: Path | None) -> Path | None:
    if path is None or not settings.regular_stage_cache_enabled:
        return None
    return path


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(128 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
