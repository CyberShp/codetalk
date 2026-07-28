"""Shared declaration and path checks for read-only validators."""

from __future__ import annotations

import os
import stat
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Iterable

from .contracts import DeclaredOutput, ValidationIssue, ValidationResult


def normalize_declared_outputs(
    declared_outputs: Iterable[object],
    *,
    validator_id: str,
    node_id: str = "",
) -> tuple[dict[str, DeclaredOutput] | None, ValidationResult | None]:
    normalized: dict[str, DeclaredOutput] = {}
    for index, raw in enumerate(declared_outputs):
        if not isinstance(raw, dict):
            return None, ValidationResult.failed(
                validator_id=validator_id,
                code="invalid_declared_output",
                message="工作流声明输出必须是结构化对象。",
                node_id=node_id,
                details={"index": index},
            )
        output_id = str(raw.get("output_id") or raw.get("id") or "").strip()
        artifact = str(raw.get("artifact") or raw.get("path") or "").strip()
        if not output_id or not artifact:
            return None, ValidationResult.failed(
                validator_id=validator_id,
                code="invalid_declared_output",
                message="工作流声明输出缺少 output_id 或 artifact。",
                node_id=node_id,
                output_id=output_id,
                details={"index": index},
            )
        if output_id in normalized:
            return None, ValidationResult.failed(
                validator_id=validator_id,
                code="duplicate_declared_output",
                message="工作流声明输出 ID 重复。",
                node_id=node_id,
                output_id=output_id,
            )
        normalized[output_id] = DeclaredOutput(
            output_id=output_id,
            artifact=artifact,
            required=bool(raw.get("required", False)),
            schema=raw.get("schema"),
        )
    return normalized, None


def validate_required_output_subset(
    *,
    validator_id: str,
    required_output_ids: Iterable[str],
    declared_outputs: Iterable[object],
    node_id: str = "",
) -> ValidationResult:
    declarations, error = normalize_declared_outputs(
        declared_outputs,
        validator_id=validator_id,
        node_id=node_id,
    )
    if error is not None:
        return error
    assert declarations is not None
    required = tuple(dict.fromkeys(str(item).strip() for item in required_output_ids if str(item).strip()))
    undeclared = sorted(item for item in required if item not in declarations)
    if undeclared:
        return ValidationResult.failed(
            validator_id=validator_id,
            code="undeclared_required_output",
            message="Validator 要求的输出未在工作流中声明。",
            node_id=node_id,
            output_id=undeclared[0],
            details={"undeclared_output_ids": undeclared},
        )
    return ValidationResult.passed(
        validator_id=validator_id,
        validated_output_ids=required,
    )


def selected_declarations(
    *,
    validator_id: str,
    declared_outputs: Iterable[object],
    required_output_ids: Iterable[str],
    node_id: str = "",
) -> tuple[tuple[DeclaredOutput, ...] | None, ValidationResult | None]:
    raw_outputs = tuple(declared_outputs)
    required = tuple(dict.fromkeys(str(item).strip() for item in required_output_ids if str(item).strip()))
    subset = validate_required_output_subset(
        validator_id=validator_id,
        required_output_ids=required,
        declared_outputs=raw_outputs,
        node_id=node_id,
    )
    if subset.status == "failed":
        return None, subset
    declarations, error = normalize_declared_outputs(
        raw_outputs,
        validator_id=validator_id,
        node_id=node_id,
    )
    if error is not None:
        return None, error
    assert declarations is not None
    return tuple(declarations[output_id] for output_id in required), None


def inspect_regular_file(
    *,
    root: Path,
    relative_path: str,
    output_id: str,
    code_prefix: str = "artifact",
    node_id: str = "",
) -> tuple[Path | None, ValidationIssue | None]:
    root_path = Path(root)
    try:
        root_mode = os.lstat(root_path).st_mode
        if stat.S_ISLNK(root_mode):
            return None, ValidationIssue(
                code=f"{code_prefix}_root_symlink_rejected",
                message="校验根目录不能是符号链接。",
                node_id=node_id,
                output_id=output_id,
                path=str(root_path),
            )
        if not stat.S_ISDIR(root_mode):
            return None, ValidationIssue(
                code=f"{code_prefix}_root_not_directory",
                message="校验根路径不是目录。",
                node_id=node_id,
                output_id=output_id,
                path=str(root_path),
            )
    except (FileNotFoundError, OSError):
        return None, ValidationIssue(
            code=f"{code_prefix}_root_missing",
            message="校验根目录不存在或不可访问。",
            node_id=node_id,
            output_id=output_id,
            path=str(root_path),
        )
    try:
        root_resolved = root_path.resolve(strict=True)
    except (FileNotFoundError, OSError):
        return None, ValidationIssue(
            code=f"{code_prefix}_root_missing",
            message="校验根目录不存在或不可访问。",
            node_id=node_id,
            output_id=output_id,
            path=str(root_path),
        )

    raw = str(relative_path or "").replace("\\", "/")
    pure = PurePosixPath(raw)
    if (
        not raw
        or pure.is_absolute()
        or bool(PureWindowsPath(raw).drive)
        or ".." in pure.parts
    ):
        return None, ValidationIssue(
            code=f"{code_prefix}_path_escape",
            message="文件路径越过了允许的根目录。",
            node_id=node_id,
            output_id=output_id,
            path=raw,
        )

    candidate = root_path.joinpath(*pure.parts)
    current = root_path
    try:
        for part in pure.parts:
            current = current / part
            mode = os.lstat(current).st_mode
            if stat.S_ISLNK(mode):
                return None, ValidationIssue(
                    code=f"{code_prefix}_symlink_rejected",
                    message="校验文件及其父目录不能是符号链接。",
                    node_id=node_id,
                    output_id=output_id,
                    path=raw,
                )
    except FileNotFoundError:
        return None, ValidationIssue(
            code=f"{code_prefix}_missing" if code_prefix == "artifact" else f"{code_prefix}_file_missing",
            message="校验文件不存在。",
            node_id=node_id,
            output_id=output_id,
            path=raw,
        )
    except OSError as exc:
        return None, ValidationIssue(
            code=f"{code_prefix}_unreadable",
            message="校验文件不可访问。",
            node_id=node_id,
            output_id=output_id,
            path=raw,
            details={"error_type": type(exc).__name__},
        )

    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(root_resolved)
    except (ValueError, FileNotFoundError, OSError):
        return None, ValidationIssue(
            code=f"{code_prefix}_path_escape",
            message="文件路径越过了允许的根目录。",
            node_id=node_id,
            output_id=output_id,
            path=raw,
        )
    try:
        mode = os.lstat(candidate).st_mode
    except OSError:
        return None, ValidationIssue(
            code=f"{code_prefix}_unreadable",
            message="校验文件不可访问。",
            node_id=node_id,
            output_id=output_id,
            path=raw,
        )
    if not stat.S_ISREG(mode):
        return None, ValidationIssue(
            code=f"{code_prefix}_not_regular_file",
            message="校验目标不是普通文件。",
            node_id=node_id,
            output_id=output_id,
            path=raw,
        )
    return candidate, None


def result_from_issues(
    *,
    validator_id: str,
    issues: list[ValidationIssue],
    validated_output_ids: tuple[str, ...],
    details: dict[str, Any] | None = None,
) -> ValidationResult:
    if issues:
        return ValidationResult(
            validator_id=validator_id,
            status="failed",
            issues=tuple(issues),
            validated_output_ids=validated_output_ids,
            details=dict(details or {}),
        )
    return ValidationResult.passed(
        validator_id=validator_id,
        validated_output_ids=validated_output_ids,
        details=details,
    )
