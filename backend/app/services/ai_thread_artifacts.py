from __future__ import annotations

import hashlib
import json
import mimetypes
import zipfile
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any

from app.services.external_agent_discovery import redact_agent_diagnostic_text


_TEXT_SUFFIXES = {
    ".csv",
    ".diff",
    ".html",
    ".json",
    ".jsonl",
    ".log",
    ".md",
    ".ndjson",
    ".patch",
    ".py",
    ".sh",
    ".txt",
    ".xml",
    ".yaml",
    ".yml",
}


class ArtifactContractError(ValueError):
    def __init__(self, message: str, *, manifest: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.manifest = manifest or {
            "version": "ai-thread-artifact-manifest-v1",
            "status": "rejected",
            "artifacts": [],
        }


def resolve_ai_thread_artifact(root: Path, relative_path: str) -> Path:
    value = str(relative_path or "").strip().replace("\\", "/")
    if not value or value.startswith("/") or "\x00" in value:
        raise ArtifactContractError("交付文件路径无效")
    try:
        root_resolved = root.resolve()
        resolved = (root / value).resolve()
    except OSError as exc:
        raise ArtifactContractError(f"交付文件路径无法解析：{exc}") from exc
    if resolved == root_resolved or root_resolved not in resolved.parents:
        raise ArtifactContractError("交付文件路径越过了本次运行目录")
    return resolved


def materialize_ai_thread_manifest(
    root: Path,
    *,
    run_id: str,
    declared_artifacts: list[dict[str, Any]],
    producer: str,
) -> dict[str, Any]:
    root.mkdir(parents=True, exist_ok=True)
    artifacts: list[dict[str, Any]] = []
    rejection_messages: list[str] = []
    seen: set[str] = set()
    for raw_contract in declared_artifacts:
        contract = raw_contract if isinstance(raw_contract, dict) else {}
        relative_path = str(
            contract.get("artifact") or contract.get("path") or contract.get("filename") or ""
        ).strip()
        if not relative_path or relative_path in seen:
            rejection_messages.append("交付文件声明缺少唯一 artifact 路径")
            continue
        seen.add(relative_path)
        required = bool(contract.get("required", True))
        try:
            path = resolve_ai_thread_artifact(root, relative_path)
        except ArtifactContractError as exc:
            artifacts.append(
                _rejected_entry(
                    relative_path,
                    producer=producer,
                    required=required,
                    status="rejected",
                    reason=str(exc),
                )
            )
            rejection_messages.append(f"{relative_path}: {exc}")
            continue
        if not path.exists() or not path.is_file():
            status = "missing" if required else "optional_missing"
            artifacts.append(
                _rejected_entry(
                    relative_path,
                    producer=producer,
                    required=required,
                    status=status,
                    reason="必需交付文件未生成" if required else "可选交付文件未生成",
                )
            )
            if required:
                rejection_messages.append(f"{relative_path}: 必需交付文件未生成")
            continue
        try:
            data = path.read_bytes()
        except OSError as exc:
            artifacts.append(
                _rejected_entry(
                    relative_path,
                    producer=producer,
                    required=required,
                    status="rejected",
                    reason=f"读取失败：{exc}",
                )
            )
            rejection_messages.append(f"{relative_path}: 读取失败")
            continue
        if not data:
            artifacts.append(
                _rejected_entry(
                    relative_path,
                    producer=producer,
                    required=required,
                    status="rejected",
                    reason="交付文件为空",
                )
            )
            rejection_messages.append(f"{relative_path}: 交付文件为空")
            continue
        if path.suffix.lower() in _TEXT_SUFFIXES:
            text = data.decode("utf-8", errors="replace")
            redacted = redact_agent_diagnostic_text(text)
            data = redacted.encode("utf-8")
            if data != path.read_bytes():
                path.write_bytes(data)
        artifact_type = str(contract.get("type") or "").strip().lower()
        schema = contract.get("schema") if isinstance(contract.get("schema"), dict) else None
        schema_status = "not_declared"
        validation_status = "accepted"
        reason = ""
        if artifact_type == "json" or path.suffix.lower() == ".json":
            try:
                payload = json.loads(data.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                validation_status = "rejected"
                schema_status = "rejected"
                reason = f"JSON 无法解析：{exc}"
            else:
                if schema is not None:
                    errors = _validate_schema(payload, schema)
                    schema_status = "accepted" if not errors else "rejected"
                    if errors:
                        validation_status = "rejected"
                        reason = "；".join(errors[:5])
                else:
                    schema_status = "not_declared"
        item = {
            "relative_path": relative_path,
            "filename": Path(relative_path).name,
            "media_type": _media_type(path),
            "audience": "deliverable",
            "producer": producer,
            "required": required,
            "size_bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            "schema_status": schema_status,
            "validation_status": validation_status,
        }
        if reason:
            item["reason"] = reason
            rejection_messages.append(f"{relative_path}: {reason}")
        artifacts.append(item)
    status = "rejected" if rejection_messages else "accepted"
    manifest = {
        "version": "ai-thread-artifact-manifest-v1",
        "run_id": run_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "artifact_count": len(artifacts),
        "accepted_count": sum(
            1 for item in artifacts if item.get("validation_status") == "accepted"
        ),
        "rejected_count": sum(
            1
            for item in artifacts
            if item.get("validation_status") in {"rejected", "missing"}
        ),
        "artifacts": artifacts,
    }
    (root / "artifact_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    if rejection_messages:
        raise ArtifactContractError("；".join(rejection_messages), manifest=manifest)
    return manifest


def build_ai_thread_delivery_zip(root: Path, manifest: dict[str, Any]) -> bytes:
    if manifest.get("status") != "accepted":
        raise ArtifactContractError("交付清单未通过验收，不能下载 ZIP", manifest=manifest)
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "artifact_manifest.json",
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True),
        )
        for item in manifest.get("artifacts") or []:
            if not isinstance(item, dict) or item.get("validation_status") != "accepted":
                continue
            relative_path = str(item.get("relative_path") or "")
            path = resolve_ai_thread_artifact(root, relative_path)
            data = path.read_bytes()
            if path.suffix.lower() in _TEXT_SUFFIXES:
                data = redact_agent_diagnostic_text(
                    data.decode("utf-8", errors="replace")
                ).encode("utf-8")
            archive.writestr(relative_path, data)
    return buffer.getvalue()


def _rejected_entry(
    relative_path: str,
    *,
    producer: str,
    required: bool,
    status: str,
    reason: str,
) -> dict[str, Any]:
    return {
        "relative_path": relative_path,
        "filename": Path(relative_path).name,
        "media_type": _media_type(Path(relative_path)),
        "audience": "deliverable",
        "producer": producer,
        "required": required,
        "size_bytes": 0,
        "sha256": "",
        "schema_status": "rejected" if relative_path.endswith(".json") else "not_declared",
        "validation_status": status,
        "reason": reason,
    }


def _media_type(path: Path) -> str:
    explicit = {
        ".json": "application/json",
        ".md": "text/markdown",
        ".py": "text/x-python",
        ".yaml": "application/yaml",
        ".yml": "application/yaml",
    }.get(path.suffix.lower())
    return explicit or mimetypes.guess_type(path.name)[0] or "application/octet-stream"


def _validate_schema(value: Any, schema: dict[str, Any], *, path: str = "$") -> list[str]:
    errors: list[str] = []
    expected = schema.get("type")
    type_checks = {
        "array": lambda item: isinstance(item, list),
        "object": lambda item: isinstance(item, dict),
        "string": lambda item: isinstance(item, str),
        "integer": lambda item: isinstance(item, int) and not isinstance(item, bool),
        "number": lambda item: isinstance(item, (int, float)) and not isinstance(item, bool),
        "boolean": lambda item: isinstance(item, bool),
        "null": lambda item: item is None,
    }
    if isinstance(expected, str) and expected in type_checks and not type_checks[expected](value):
        return [f"{path} 应为 {expected}"]
    if "enum" in schema and isinstance(schema["enum"], list) and value not in schema["enum"]:
        errors.append(f"{path} 不在允许值中")
    if isinstance(value, str) and isinstance(schema.get("minLength"), int):
        if len(value) < schema["minLength"]:
            errors.append(f"{path} 长度小于 {schema['minLength']}")
    if isinstance(value, list):
        if isinstance(schema.get("minItems"), int) and len(value) < schema["minItems"]:
            errors.append(f"{path} 项目数小于 {schema['minItems']}")
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(value):
                errors.extend(_validate_schema(item, item_schema, path=f"{path}[{index}]"))
    if isinstance(value, dict):
        required = schema.get("required") if isinstance(schema.get("required"), list) else []
        for key in required:
            if str(key) not in value:
                errors.append(f"{path}.{key} 为必填字段")
        properties = schema.get("properties")
        if isinstance(properties, dict):
            for key, child_schema in properties.items():
                if key in value and isinstance(child_schema, dict):
                    errors.extend(_validate_schema(value[key], child_schema, path=f"{path}.{key}"))
    return errors

