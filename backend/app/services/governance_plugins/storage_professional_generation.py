"""Evidence-bound SFMEA and black-box generation for storage governance."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any


@dataclass(frozen=True)
class StorageProfessionalGenerationError(Exception):
    code: str
    message: str
    artifact_id: str = ""
    details: dict[str, Any] | None = None


def generate_storage_professional_payloads(
    *,
    inputs: dict[str, Any],
    roles: tuple[str, ...],
    node_id: str,
    artifact_id: str,
) -> dict[str, list[dict[str, Any]]]:
    """Generate distinct role payloads and accept them through canonical rules."""
    evidence = _validated_explicit_evidence(
        inputs=inputs,
        node_id=node_id,
        artifact_id=artifact_id,
    )
    target = str(inputs.get("target") or "storage workflow").strip()
    repo = Path(str(inputs.get("repo_path") or ""))
    test_roots = _canonical_test_roots(
        target=target,
        repo=repo,
        evidence=evidence,
    )
    product_evidence = _product_source_evidence(evidence)
    payloads: dict[str, list[dict[str, Any]]] = {}
    if "sfmea" in roles:
        if not product_evidence:
            raise StorageProfessionalGenerationError(
                code="storage_test_design_product_source_evidence_required",
                message="SFMEA 生成需要至少一张产品源码证据卡，测试桩或用例代码不能单独支撑产品风险。",
                artifact_id=artifact_id,
            )
        payloads["sfmea"] = _sfmea_rows(
            evidence=product_evidence,
            target=target,
            repo=repo,
            test_roots=test_roots,
        )
    if "black_box_cases" in roles:
        payloads["black_box_cases"] = _black_box_cases(
            evidence=product_evidence if "sfmea" in roles else evidence,
            target=target,
            repo=repo,
            test_roots=test_roots,
            link_sfmea="sfmea" in roles,
        )
    _accept_with_canonical_professional_rules(payloads=payloads, repo=repo)
    return payloads


def _validated_explicit_evidence(
    *,
    inputs: dict[str, Any],
    node_id: str,
    artifact_id: str,
) -> tuple[dict[str, Any], ...]:
    raw = inputs.get("source_evidence")
    if not isinstance(raw, list) or not raw:
        raise StorageProfessionalGenerationError(
            code="storage_test_design_source_evidence_required",
            message="SFMEA 和黑盒用例生成必须绑定非空的显式源码证据卡。",
            artifact_id=artifact_id,
        )
    if not all(isinstance(item, dict) for item in raw):
        raise StorageProfessionalGenerationError(
            code="storage_test_design_source_evidence_invalid",
            message="显式源码证据必须是结构化卡片数组。",
            artifact_id=artifact_id,
        )
    repo = Path(str(inputs.get("repo_path") or ""))
    from app.services.validators.source_evidence import _validate_card

    validated: list[dict[str, Any]] = []
    for index, card in enumerate(raw):
        issue = _validate_card(
            card,
            index=index,
            output_id=artifact_id,
            source_root=repo,
            node_id=node_id,
        )
        if issue is not None:
            raise StorageProfessionalGenerationError(
                code="storage_test_design_source_evidence_invalid",
                message=(
                    "显式源码证据未通过路径、行号、片段、符号和摘要校验。"
                ),
                artifact_id=artifact_id,
                details={
                    "evidence_issue_code": issue.code,
                    "evidence_issue_details": issue.details,
                },
            )
        validated.append(dict(card))
    return tuple(validated)


def _product_source_evidence(
    evidence: tuple[dict[str, Any], ...],
) -> tuple[dict[str, Any], ...]:
    return tuple(
        card for card in evidence
        if not _is_test_or_harness_path(str(card.get("file_path") or ""))
    )


def _is_test_or_harness_path(file_path: str) -> bool:
    parts = PurePosixPath(file_path).parts
    return bool(parts) and parts[0] in {"test", "tests"}


def _sfmea_rows(
    *,
    evidence: tuple[dict[str, Any], ...],
    target: str,
    repo: Path,
    test_roots: tuple[str, ...],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, card in enumerate(evidence, start=1):
        file_path = str(card["file_path"])
        symbol = str((card.get("symbols") or ["evidenced path"])[0])
        module = _module_for_path(file_path)
        test_mapping = _test_mapping(
            card=card,
            test_roots=test_roots,
            repo=repo,
        )
        severity = 8 if module in {"iscsi", "nvmf", "bdev"} else 6
        occurrence = 3
        detection_score = 4
        rows.append(
            {
                "sfmea_id": f"storage_sfmea_{index:03d}",
                "module": module,
                "failure_mode": (
                    f"{module} public workflow can return an incorrect status or retain "
                    f"stale state when the evidenced {symbol} path fails"
                ),
                "mechanism": (
                    f"风险假设：若 {file_path} 中 {symbol} 的错误或清理路径"
                    "偏离公开"
                    "工作流约束，状态和资源可能无法一致收敛。"
                ),
                "cause": (
                    f"The failure and cleanup behavior evidenced at {file_path} must remain "
                    "consistent with the public storage workflow."
                ),
                "effect": (
                    f"{target} may expose an error status, stale session or resource state, "
                    "or a failed retry at the public boundary."
                ),
                "detection": (
                    f"Run black-box cases mapped to {test_mapping}; compare public status, "
                    "logs, counters, reconnect behavior, and final cleanup state."
                ),
                "severity": severity,
                "occurrence": occurrence,
                "detection_score": detection_score,
                "rpn": severity * occurrence * detection_score,
                "rpn_status": "provisional",
                "risk_status": "test_hypothesis",
                "occurrence_basis": (
                    "Low confidence expert engineering review of one hash-validated source "
                    "slice; runtime test statistics remain the promotion criterion."
                ),
                "score_explanation": (
                    f"severity={severity} for externally visible storage state; "
                    f"occurrence={occurrence} from low confidence expert engineering review; "
                    f"detection={detection_score} because {file_path} is hash-validated but "
                    "the public failure behavior still requires runtime observation."
                ),
                "evidence_interpretation": (
                    f"风险假设：若 {symbol} 在异常输入或恢复期间未保持状态"
                    "和资源"
                    "一致性，则需要通过公开接口故障场景验证该偏离。"
                ),
                "mitigation": (
                    f"Enforce bounded error propagation and cleanup for {symbol} in "
                    f"{file_path}; add targeted black-box regression tests in {test_mapping} "
                    "and monitor public status, logs, resource counters, and recovery."
                ),
                "source_evidence": [file_path],
                "test_mapping": test_mapping,
                "evidence": {
                    "file_path": file_path,
                    "start_line": card["start_line"],
                    "end_line": card["end_line"],
                    "excerpt": card["excerpt"],
                    "symbols": list(card["symbols"]),
                    "sha256": card["sha256"],
                },
            }
        )
    return rows


def _black_box_cases(
    *,
    evidence: tuple[dict[str, Any], ...],
    target: str,
    repo: Path,
    test_roots: tuple[str, ...],
    link_sfmea: bool,
) -> list[dict[str, Any]]:
    from app.services.test_activity_contract import BLACK_BOX_REQUIRED_DIMENSIONS

    cases: list[dict[str, Any]] = []
    for evidence_index, card in enumerate(evidence, start=1):
        file_path = str(card["file_path"])
        module = _module_for_path(file_path)
        evidence_label = _evidence_case_label(evidence_index)
        mapping = _test_mapping(
            card=card,
            test_roots=test_roots,
            repo=repo,
        )
        for dimension in BLACK_BOX_REQUIRED_DIMENSIONS:
            case_index = len(cases) + 1
            case = {
                "case_id": f"storage_black_box_{case_index:03d}",
                "case_type": "black_box_ready",
                "test_dimension": dimension,
                "scenario_name": (
                    f"{module} {dimension} public workflow for {evidence_label}"
                ),
                "preconditions": [
                    f"A supported public harness for {target} is available",
                    "The test target is isolated and its initial public state is recorded",
                ],
                "inputs": f"public workflow for {target} under {dimension}",
                "steps": [
                    f"Start the supported {module} target and client through public commands",
                    (
                        f"Execute the {dimension} scenario through supported public "
                        "commands and environment controls"
                    ),
                    (
                        "Collect exit status, protocol or command response, logs, "
                        "metrics, timing, and final public state"
                    ),
                    "Run cleanup and repeat the normal public operation to verify recovery",
                ],
                "expected_result": (
                    f"The {dimension} operation reaches a documented public status; logs "
                    "and metrics identify the outcome, state remains consistent, and the "
                    "post-cleanup retry succeeds or returns the documented error."
                ),
                "observability": [
                    "client-visible status and exit code",
                    "target logs and public metrics",
                    "connection, session, or resource state after cleanup",
                ],
                "failure_diagnostics": [
                    (
                        f"Compare the {dimension} result with the recorded "
                        "normal-path baseline"
                    ),
                    f"Correlate public failures with the evidence at {file_path}",
                    "Retain command output, timestamps, logs, metrics, and before/after state",
                ],
                "oracle_basis": _oracle_basis(
                    dimension=dimension,
                    file_path=file_path,
                ),
                "mapped_test_dir": mapping,
                "source_or_test_evidence": [file_path],
                "source_evidence": {
                    "file_path": file_path,
                    "start_line": card["start_line"],
                    "end_line": card["end_line"],
                    "sha256": card["sha256"],
                },
            }
            if link_sfmea:
                case["risk_ids"] = [f"storage_sfmea_{evidence_index:03d}"]
            cases.append(case)
    return cases


def _oracle_basis(*, dimension: str, file_path: str) -> str:
    if dimension == "performance":
        return (
            f"Use source evidence {file_path} and the same-environment baseline; warmup 5 "
            "runs, repeat 30 samples, compare P50/P95 plus variance, and record commit, "
            "hardware, kernel, network, and configuration."
        )
    return (
        f"Use source evidence {file_path}, documented public configuration or specification, "
        "and a same-environment normal-path baseline for the exact limit and expected status."
    )


def _evidence_case_label(index: int) -> str:
    return f"evidence slice {index:03d}"


def _accept_with_canonical_professional_rules(
    *,
    payloads: dict[str, list[dict[str, Any]]],
    repo: Path,
) -> None:
    from app.services.test_activity_contract import ARTIFACT_TEMPLATES, _audit_json_artifact

    artifact_for_role = {
        "sfmea": "sfmea.json",
        "black_box_cases": "black_box_cases.json",
    }
    for role, payload in payloads.items():
        artifact = artifact_for_role[role]
        issues = _audit_json_artifact(
            artifact=artifact,
            payload=payload,
            spec=ARTIFACT_TEMPLATES[artifact],
            repo=repo,
        )
        if issues:
            raise StorageProfessionalGenerationError(
                code="storage_test_design_professional_quality_failed",
                message=f"{artifact} 未通过现有专业质量规则。",
                details={"artifact": artifact, "issues": issues},
            )


def _module_for_path(file_path: str) -> str:
    parts = PurePosixPath(file_path).parts
    if len(parts) >= 2 and parts[0] in {"lib", "module", "test", "tests"}:
        return parts[1]
    return parts[0] if parts else "storage"


def _canonical_test_roots(
    *,
    target: str,
    repo: Path,
    evidence: tuple[dict[str, Any], ...],
) -> tuple[str, ...]:
    from app.services.test_activity_contract import build_test_activity_contract

    evidence_paths = " ".join(str(card["file_path"]) for card in evidence)
    contract = build_test_activity_contract(
        target=f"{target} {evidence_paths}".strip(),
        repo_path=str(repo),
        workflow_outputs=[],
    )
    project_profile = contract.get("project_profile")
    if not isinstance(project_profile, dict):
        return ()
    return tuple(
        str(item)
        for item in project_profile.get("test_roots") or []
        if str(item).strip()
    )


def _test_mapping(
    *,
    card: dict[str, Any],
    test_roots: tuple[str, ...],
    repo: Path,
) -> str:
    explicit = str(card.get("test_mapping") or "").strip()
    candidates = (explicit,) if explicit else test_roots
    candidate = next(
        (item for item in candidates if (repo / item).exists()),
        candidates[0] if candidates else "test",
    )
    if (repo / candidate).exists():
        return candidate
    return f"ai_suggested_unverified: {candidate}"
