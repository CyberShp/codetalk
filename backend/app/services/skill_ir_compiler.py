"""Deterministic Skill IR compiler for F014 Task 4."""

from __future__ import annotations

import copy
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

from app.services.skill_package_paths import SkillPackagePathError, validate_member_name
from app.services.skill_package_validator import (
    SCHEMA_DIR,
    SkillPackageValidationError,
    SkillPackageValidationIssue,
    validate_skill_document,
    topological_order,
)


_FORMAL_OUTPUT_IDS = {
    "正式输出/开发给测试讲代码.md": "developer-test-code-explanation",
    "正式输出/流程分支状态资源与异常传播.md": "flow-branch-state-resource-exception",
    "正式输出/风险点与SFMEA.md": "risk-and-sfmea",
    "正式输出/黑盒测试场景.md": "black-box-scenarios",
    "正式输出/黑盒测试流程.md": "black-box-flows",
    "正式输出/黑盒测试用例.md": "black-box-cases",
    "正式输出/覆盖审计与分析限制.md": "coverage-audit-and-limitations",
    "正式输出/完整分析报告.md": "complete-analysis-report",
}
_MODULE_ANALYSIS_REQUIRED_ARTIFACT_COUNT = 37
_MODULE_ANALYSIS_STEP_IDS = ("01", "02", "03", "04", "05", "06", "07", "08", "09")
_MODULE_ANALYSIS_CORE_RULE_IDS = {"path-fidelity", "evidence-consumption", "narrative-first"}
_MODULE_ANALYSIS_REQUIRED_ARTIFACT_PATHS_BY_STEP = (
    ("活文档/01-范围与任务契约.md",),
    ("活文档/02-输入材料消费记录.md", "内部索引/运行计划.json", "内部索引/输入材料索引.json", "活文档/覆盖门禁/步骤02-覆盖门禁.md"),
    ("活文档/03-入口清单与说明.md", "活文档/04-流程清单与说明.md", "活文档/05-状态清单与说明.md", "活文档/06-资源清单与说明.md", "活文档/07-分析模型适用性.md", "活文档/覆盖门禁/步骤03-覆盖门禁.md"),
    ("活文档/08-分支处置与解释.md", "活文档/09-状态转换处置与解释.md", "活文档/10-资源生命周期处置与解释.md", "活文档/11-异常传播链与解释.md", "活文档/12-开发讲解覆盖台账.md", "活文档/覆盖门禁/步骤04-覆盖门禁.md"),
    ("活文档/13-场景候选池与推导说明.md", "活文档/14-风险点清单与因果说明.md", "活文档/覆盖门禁/步骤05-覆盖门禁.md"),
    ("活文档/15-SFMEA分析.md", "活文档/16-黑盒控制与观测映射.md", "活文档/17-测试设计依据.md", "活文档/覆盖门禁/步骤06-覆盖门禁.md"),
    ("活文档/18-测试追溯矩阵.md", "活文档/覆盖门禁/步骤07-覆盖门禁.md"),
    ("活文档/19-独立审查报告.md", "活文档/覆盖门禁/最终覆盖门禁.md", "内部索引/独立审查状态.json"),
    tuple(_FORMAL_OUTPUT_IDS.keys()),
)
_MODULE_ANALYSIS_REQUIRED_ARTIFACT_PATHS = {
    artifact_path
    for step_paths in _MODULE_ANALYSIS_REQUIRED_ARTIFACT_PATHS_BY_STEP
    for artifact_path in step_paths
}
_RUN_GUARD_LOG_PATH = "内部索引/运行状态.json"
_JUDGE_STATE_PATH = "内部索引/独立审查状态.json"


def _sha256_bytes(payload: bytes) -> str:
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def _sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def _canonical_json(document: dict[str, Any]) -> bytes:
    return json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _source_file_digests(source_root: Path, source_path: Path, document: dict[str, Any]) -> list[dict[str, str]]:
    return _all_source_file_digests(source_root)


def _with_content_digest(ir: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(ir)
    unsigned = copy.deepcopy(result)
    unsigned["content_digest"] = "sha256:" + "0" * 64
    result["content_digest"] = _sha256_bytes(_canonical_json(unsigned))
    return result


def compile_skill_ir(
    document: dict[str, Any],
    *,
    source_root: str | Path,
    source_path: str | Path,
) -> dict[str, Any]:
    """Compile a validated codetalk-skill-v1 document into deterministic IR."""

    root = Path(source_root)
    skill_path = Path(source_path)
    validation = validate_skill_document(document, source_root=root, source_path=skill_path)
    if not validation.ok:
        raise SkillPackageValidationError(validation.issues)
    source_document = _read_source_json(skill_path, "source_path")
    if _canonical_json(source_document) != _canonical_json(document):
        raise _issue("source_document_mismatch", "source_path", "compiled document differs from source file bytes")
    ir = copy.deepcopy(source_document)
    ir["schema_version"] = "skill-ir-v1"
    ir.pop("name", None)
    ir["content_digest"] = "sha256:" + "0" * 64
    ir["topological_order"] = topological_order(source_document)
    ir["source_file_digests"] = _source_file_digests(root, skill_path, source_document)
    _raise_ir_schema_errors(ir)
    return _with_content_digest(ir)


def _issue(code: str, path: str, message: str) -> SkillPackageValidationError:
    return SkillPackageValidationError((SkillPackageValidationIssue(code, path, message),))


def _slug(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return normalized or "item"


def _artifact_id_for_path(path: str) -> str:
    if path in _FORMAL_OUTPUT_IDS:
        return f"artifact.formal-{_FORMAL_OUTPUT_IDS[path]}"
    if path == _RUN_GUARD_LOG_PATH:
        return "artifact.internal-run-state"
    if path == _JUDGE_STATE_PATH:
        return "artifact.internal-judge-state"
    return f"artifact.required-{hashlib.sha256(path.encode('utf-8')).hexdigest()[:12]}"


def _delivery_id_for_path(path: str) -> str:
    return f"delivery.{_FORMAL_OUTPUT_IDS[path]}"


def _read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise _issue("invalid_json", path.name, str(exc)) from exc
    except UnicodeDecodeError as exc:
        raise _issue("invalid_json", path.name, str(exc)) from exc


def _read_source_json(path: Path, issue_path: str) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise _issue("invalid_json", issue_path, str(exc)) from exc
    except UnicodeDecodeError as exc:
        raise _issue("invalid_json", issue_path, str(exc)) from exc


def _all_source_file_digests(root: Path) -> list[dict[str, str]]:
    root_resolved = root.resolve(strict=True)
    paths: list[str] = []
    for path in root.rglob("*"):
        relative_path = path.relative_to(root).as_posix()
        if path.is_symlink():
            raise _issue("unsafe_path", "source_file_digests", f"source path uses a symlink: {relative_path!r}")
        if path.is_file():
            try:
                path.resolve(strict=True).relative_to(root_resolved)
            except ValueError:
                raise _issue("unsafe_path", "source_file_digests", f"source path escapes root: {relative_path!r}")
            paths.append(relative_path)
    paths.sort()
    for path in paths:
        try:
            validate_member_name(path)
        except SkillPackagePathError:
            raise _issue("unsafe_path", "source_file_digests", f"unsafe source file path {path!r}")
    return [{"path": path, "digest": _sha256_path(root / path)} for path in paths]


def _require_file(root: Path, relative_path: str) -> None:
    try:
        validate_member_name(relative_path)
    except SkillPackagePathError:
        raise _issue("unsafe_path", relative_path, relative_path)
    candidate = root / relative_path
    if candidate.is_symlink():
        raise _issue("unsafe_path", relative_path, relative_path)
    try:
        candidate.resolve(strict=True).relative_to(root.resolve(strict=True))
    except (FileNotFoundError, ValueError):
        if candidate.exists():
            raise _issue("unsafe_path", relative_path, relative_path)
    if not candidate.is_file():
        raise _issue("missing_source_file", relative_path, relative_path)


def _schema_validator(name: str) -> Draft202012Validator:
    resources = [Resource.from_contents(_read_json(path)) for path in SCHEMA_DIR.glob("*.schema.json")]
    registry = Registry().with_resources((resource.id(), resource) for resource in resources)
    schema = _read_json(SCHEMA_DIR / f"{name}.schema.json")
    return Draft202012Validator(schema, registry=registry, format_checker=FormatChecker())


def _raise_ir_schema_errors(ir: dict[str, Any]) -> None:
    priority = {
        "schema_version": 0,
        "skill_id": 1,
        "content_digest": 2,
        "required_agent_capabilities": 3,
        "inputs": 4,
        "steps": 5,
        "artifacts": 6,
        "deliveries": 7,
        "scripts": 8,
        "core_rules": 9,
        "judge": 10,
        "topological_order": 11,
        "source_file_digests": 12,
    }
    errors = sorted(
        _schema_validator("skill-ir-v1").iter_errors(ir),
        key=lambda error: (priority.get(list(error.absolute_path)[0], 99) if list(error.absolute_path) else 99, list(error.absolute_path)),
    )
    if errors:
        error = errors[0]
        path = ".".join(str(segment) for segment in error.absolute_path) or "$"
        raise _issue("schema_error", path, error.message)


def _add_source_path(paths: list[str], seen: set[str], path: str) -> None:
    if path not in seen:
        paths.append(path)
        seen.add(path)


def _manifest_issue(code: str, path: str, message: str) -> SkillPackageValidationError:
    return _issue(code, f"workflow-manifest.json.{path}", message)


def _validate_v24_manifest_shape(manifest: Any) -> dict[str, Any]:
    if not isinstance(manifest, dict):
        raise _manifest_issue("schema_error", "$", "manifest must be an object")
    if manifest.get("version") != "2.4":
        raise _issue("unsupported_manifest_version", "workflow-manifest.json", "expected Codetalks v2.4")
    for field in (
        "required_core_rules",
        "steps",
        "evidence_allowed_status",
        "coverage_allowed_outcomes",
        "flow_required_headings",
        "flow_key_narrative_headings",
    ):
        if field not in manifest:
            raise _manifest_issue("missing_manifest_field", field, field)
    if not isinstance(manifest["required_core_rules"], dict):
        raise _manifest_issue("schema_error", "required_core_rules", "required_core_rules must be an object")
    for rule_id, instruction_path in manifest["required_core_rules"].items():
        if not isinstance(rule_id, str) or not isinstance(instruction_path, str):
            raise _manifest_issue("schema_error", "required_core_rules", "core rule IDs and paths must be strings")
    if not isinstance(manifest["steps"], list) or not manifest["steps"]:
        raise _manifest_issue("schema_error", "steps", "steps must be a non-empty array")
    for field in (
        "evidence_allowed_status",
        "coverage_allowed_outcomes",
        "flow_required_headings",
        "flow_key_narrative_headings",
    ):
        if not isinstance(manifest[field], list) or not manifest[field] or not all(isinstance(item, str) for item in manifest[field]):
            raise _manifest_issue("schema_error", field, f"{field} must be a non-empty array of strings")
    for index, step in enumerate(manifest["steps"]):
        step_path = f"steps[{index}]"
        if not isinstance(step, dict):
            raise _manifest_issue("schema_error", step_path, "step must be an object")
        for field in ("id", "file", "required", "markdown_min_chars"):
            if field not in step:
                raise _manifest_issue("missing_manifest_field", f"{step_path}.{field}", field)
        if step.get("id") == "04":
            for field in ("requires_glob", "flow_narrative_validation"):
                if field not in step:
                    raise _manifest_issue("missing_manifest_field", f"{step_path}.{field}", field)
        if not isinstance(step["id"], str):
            raise _manifest_issue("schema_error", f"{step_path}.id", "id must be a string")
        if not isinstance(step["file"], str):
            raise _manifest_issue("schema_error", f"{step_path}.file", "file must be a string")
        if not isinstance(step["required"], list) or not all(isinstance(item, str) for item in step["required"]):
            raise _manifest_issue("schema_error", f"{step_path}.required", "required must be an array of strings")
        if not isinstance(step["markdown_min_chars"], int):
            raise _manifest_issue("schema_error", f"{step_path}.markdown_min_chars", "markdown_min_chars must be an integer")
        if step["id"] == "04":
            if "requires_glob" not in step:
                raise _manifest_issue("missing_manifest_field", f"{step_path}.requires_glob", "requires_glob")
            if "flow_narrative_validation" not in step:
                raise _manifest_issue("missing_manifest_field", f"{step_path}.flow_narrative_validation", "flow_narrative_validation")
            if not isinstance(step["requires_glob"], list) or not step["requires_glob"] or not all(isinstance(item, str) for item in step["requires_glob"]):
                raise _manifest_issue("schema_error", f"{step_path}.requires_glob", "requires_glob must be a non-empty array of strings")
            if step["flow_narrative_validation"] is not True:
                raise _manifest_issue("schema_error", f"{step_path}.flow_narrative_validation", "flow_narrative_validation must be true")
        elif "requires_glob" in step:
            if not isinstance(step["requires_glob"], list) or not step["requires_glob"] or not all(isinstance(item, str) for item in step["requires_glob"]):
                raise _manifest_issue("schema_error", f"{step_path}.requires_glob", "requires_glob must be a non-empty array of strings")
        if "flow_narrative_validation" in step and step["id"] != "04" and not isinstance(step["flow_narrative_validation"], bool):
            raise _manifest_issue("schema_error", f"{step_path}.flow_narrative_validation", "flow_narrative_validation must be a boolean")
    return manifest


def _validate_module_analysis_artifact_contract(manifest: dict[str, Any], *, include_step_set: bool = True) -> None:
    if include_step_set and tuple(step["id"] for step in manifest["steps"]) != _MODULE_ANALYSIS_STEP_IDS:
        raise _issue("codetalks_step_set_mismatch", "workflow-manifest.json.steps", "module-analysis must declare exactly steps 01-09")
    if include_step_set:
        for index, expected_paths in enumerate(_MODULE_ANALYSIS_REQUIRED_ARTIFACT_PATHS_BY_STEP):
            actual_paths = manifest["steps"][index]["required"]
            if set(actual_paths) != set(expected_paths) or len(actual_paths) != len(expected_paths):
                raise _issue(
                    "codetalks_required_artifact_step_mismatch",
                    f"workflow-manifest.json.steps[{index}].required",
                    "required artifact paths must stay attached to their Codetalks v2.4 step",
                )
    if set(manifest["required_core_rules"]) != _MODULE_ANALYSIS_CORE_RULE_IDS:
        raise _issue(
            "codetalks_core_rule_set_mismatch",
            "workflow-manifest.json.required_core_rules",
            "module-analysis must declare the three required core rules",
        )
    required_paths = [artifact_path for step in manifest["steps"] for artifact_path in step["required"]]
    formal_paths = {path for path in required_paths if path.startswith("正式输出/")}
    expected_formal_paths = set(_FORMAL_OUTPUT_IDS)
    if formal_paths != expected_formal_paths:
        raise _issue(
            "codetalks_formal_output_set_mismatch",
            "workflow-manifest.json.steps[8].required",
            "formal output paths do not match Codetalks v2.4 module-analysis contract",
        )
    if set(required_paths) != _MODULE_ANALYSIS_REQUIRED_ARTIFACT_PATHS:
        raise _issue(
            "codetalks_required_artifact_set_mismatch",
            "workflow-manifest.json.steps",
            "required artifact paths do not match Codetalks v2.4 module-analysis contract",
        )
    if len(required_paths) != _MODULE_ANALYSIS_REQUIRED_ARTIFACT_COUNT:
        raise _issue(
            "codetalks_required_artifact_count_mismatch",
            "workflow-manifest.json.steps",
            f"expected {_MODULE_ANALYSIS_REQUIRED_ARTIFACT_COUNT} required artifacts",
        )


def _codetalks_v24_document(root: Path, source_scenario_id: str) -> dict[str, Any]:
    workflow_path = f"workflows/{source_scenario_id}.md"
    _require_file(root, workflow_path)
    _require_file(root, "workflow-manifest.json")
    manifest = _validate_v24_manifest_shape(_read_json(root / "workflow-manifest.json"))
    if source_scenario_id == "module-analysis":
        _validate_module_analysis_artifact_contract(manifest, include_step_set=False)

    source_paths: list[str] = []
    source_path_seen: set[str] = set()
    for path in ("SKILL.md", "workflow-manifest.json", workflow_path, "scripts/run_guard.py", "checklists/judge-checklist.md"):
        _add_source_path(source_paths, source_path_seen, path)
    skill_id = "skill.codetalks-module-full-analysis" if source_scenario_id == "module-analysis" else f"skill.codetalks-{_slug(source_scenario_id)}"
    steps: list[dict[str, Any]] = []
    artifacts: list[dict[str, Any]] = []
    deliveries: list[dict[str, Any]] = []
    seen_artifacts: set[str] = set()

    for index, step in enumerate(manifest["steps"]):
        step_id = f"step.step-{step['id']}"
        _add_source_path(source_paths, source_path_seen, step["file"])
        required_artifact_ids: list[str] = []
        for artifact_path in step["required"]:
            artifact_id = _artifact_id_for_path(artifact_path)
            required_artifact_ids.append(artifact_id)
            if artifact_id not in seen_artifacts:
                artifacts.append({
                    "artifact_id": artifact_id,
                    "path": artifact_path,
                    "producer_step_id": step_id,
                    "required": True,
                    "visibility": "delivery" if artifact_path in _FORMAL_OUTPUT_IDS else "internal",
                })
                seen_artifacts.add(artifact_id)
            if artifact_path in _FORMAL_OUTPUT_IDS:
                deliveries.append({
                    "delivery_id": _delivery_id_for_path(artifact_path),
                    "label": Path(artifact_path).stem,
                    "artifact_ids": [artifact_id],
                })
        completion_gate: dict[str, Any] = {
            "required_artifact_ids": list(required_artifact_ids),
            "min_output_characters": step["markdown_min_chars"],
            "evidence_allowed_status": manifest["evidence_allowed_status"],
            "coverage_allowed_outcomes": manifest["coverage_allowed_outcomes"],
        }
        if "requires_glob" in step:
            completion_gate["requires_glob"] = step["requires_glob"]
        if "flow_narrative_validation" in step:
            completion_gate["flow_narrative_validation"] = step["flow_narrative_validation"]
            completion_gate["flow_required_headings"] = manifest["flow_required_headings"]
            completion_gate["flow_key_narrative_headings"] = manifest["flow_key_narrative_headings"]

        steps.append({
            "step_id": step_id,
            "title": f"{workflow_path}: Codetalks step {step['id']}",
            "instruction_path": step["file"],
            "script_id": "script.run_guard",
            "depends_on": [f"step.step-{manifest['steps'][index - 1]['id']}"] if index else [],
            "produces": list(required_artifact_ids),
            "completion_gate": completion_gate,
        })

    run_guard_log_id = _artifact_id_for_path(_RUN_GUARD_LOG_PATH)
    if run_guard_log_id not in seen_artifacts:
        artifacts.append({
            "artifact_id": run_guard_log_id,
            "path": _RUN_GUARD_LOG_PATH,
            "producer_step_id": steps[0]["step_id"],
            "required": False,
            "visibility": "internal",
        })
        steps[0]["produces"].append(run_guard_log_id)

    core_rules = []
    for rule_id, instruction_path in manifest["required_core_rules"].items():
        _add_source_path(source_paths, source_path_seen, instruction_path)
        core_rules.append({
            "rule_id": f"rule.{rule_id}",
            "instruction_path": instruction_path,
            "acknowledgement_required": True,
        })

    for path in source_paths:
        _require_file(root, path)

    inputs = [{
        "input_id": "input.source",
        "label": f"Source materials for {workflow_path}",
        "kind": "workspace",
        "required": True,
    }]
    if source_scenario_id == "issue-regression":
        inputs.append({
            "input_id": "input.mr-link",
            "label": "MR link",
            "kind": "url",
            "required": True,
        })

    judge_required = source_scenario_id == "module-analysis"
    document = {
        "schema_version": "codetalk-skill-v1",
        "skill_id": skill_id,
        "name": "Codetalks module full analysis" if source_scenario_id == "module-analysis" else f"Codetalks {source_scenario_id}",
        "selected_workflow_path": workflow_path,
        "required_agent_capabilities": ["tools", "artifact_collection", "session_isolation"],
        "inputs": inputs,
        "steps": steps,
        "artifacts": artifacts,
        "deliveries": deliveries,
        "scripts": [{
            "script_id": "script.run_guard",
            "path": "scripts/run_guard.py",
            "timeout_seconds": 60,
            "working_directory": ".",
            "allowed_exit_codes": [0],
            "log_artifact_ids": [run_guard_log_id],
            "write_scope": ["活文档", "内部索引", "正式输出"],
        }],
        "core_rules": core_rules,
        "judge": {
            "required": judge_required,
            "isolated_session": judge_required,
            "artifact_ids": ["artifact.formal-complete-analysis-report"] if judge_required else [],
        },
    }
    validation = validate_skill_document(document, source_root=root, source_path=root / "workflow-manifest.json")
    if not validation.ok:
        raise SkillPackageValidationError(validation.issues)
    if source_scenario_id == "module-analysis":
        _validate_module_analysis_artifact_contract(manifest)
    return document


def compile_codetalks_v24_skill(source_root: str | Path, *, source_scenario_id: str) -> dict[str, Any]:
    """Compile the explicit Codetalks v2.4 source manifest without prose inference."""

    root = Path(source_root)
    document = _codetalks_v24_document(root, source_scenario_id)
    ir = copy.deepcopy(document)
    ir["schema_version"] = "skill-ir-v1"
    ir.pop("name", None)
    ir["content_digest"] = "sha256:" + "0" * 64
    ir["topological_order"] = topological_order(document)
    ir["source_file_digests"] = _all_source_file_digests(root)
    _raise_ir_schema_errors(ir)
    return _with_content_digest(ir)
