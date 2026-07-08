"""Testing activity contracts, profiles, artifact templates, and quality audit."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


def _profile(
    *,
    name: str,
    aliases: list[str],
    scenarios: list[str],
    failure_modes: list[str],
    observability: list[str],
    graybox_evidence: list[str],
    source_entries: list[str],
    test_dirs: list[str],
    forbidden_internal_steps: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "name": name,
        "aliases": aliases,
        "required_scenarios": scenarios,
        "failure_modes": failure_modes,
        "black_box_observability": observability,
        "graybox_evidence_points": graybox_evidence,
        "recommended_source_entries": source_entries,
        "recommended_test_dirs": test_dirs,
        "log_metric_rpc_observability": observability,
        "forbidden_internal_steps": forbidden_internal_steps
        or [
            "direct internal function invocation",
            "modify source code to trigger the scenario",
            "assert private struct fields as the external expected result",
        ],
    }


PROFILE_REGISTRY: dict[str, dict[str, Any]] = {
    "iscsi_login": _profile(
        name="iSCSI login/session",
        aliases=["iscsi", "login", "chap", "digest", "session reset"],
        scenarios=["login negotiation", "CHAP success/failure", "digest mismatch", "session reset", "multi-connection recovery"],
        failure_modes=["bad credentials", "redirect loop", "digest validation failure", "half-open session", "initiator disconnect"],
        observability=["initiator login result", "SPDK logs", "session state", "connection reset behavior", "test/iscsi_tgt output"],
        graybox_evidence=["login state machine", "CHAP decision point", "session cleanup path"],
        source_entries=["lib/iscsi", "lib/iscsi/iscsi.c"],
        test_dirs=["test/iscsi_tgt"],
    ),
    "nvmeof_transport": _profile(
        name="NVMe-oF transport/connect/IO",
        aliases=["nvme", "nvmeof", "nvmf", "nvme-o-f", "tcp", "transport", "connect", "queue"],
        scenarios=["connect", "authentication", "queue creation", "IO submit/complete", "disconnect/reconnect", "controller reset"],
        failure_modes=["connect timeout", "queue teardown leak", "controller reset race", "IO completion loss", "transport error propagation"],
        observability=["nvme connect status", "RPC result", "SPDK logs", "host-visible namespace state", "test/nvmf output"],
        graybox_evidence=["transport ops", "controller state", "request completion path"],
        source_entries=["lib/nvmf", "lib/nvmf/tcp.c"],
        test_dirs=["test/nvmf"],
    ),
    "security_tls": _profile(
        name="TLS/security handshake",
        aliases=["tls", "ssl", "certificate", "cert", "key", "psk", "auth"],
        scenarios=["valid certificate", "expired certificate", "wrong identity", "cipher mismatch", "credential rotation"],
        failure_modes=["handshake failure", "silent downgrade", "credential leak", "bad error reporting"],
        observability=["connection result", "TLS alert/log", "RPC/config status", "certificate file diagnostics"],
        graybox_evidence=["TLS config parsing", "auth handshake branch", "credential loading path"],
        source_entries=["lib/nvmf", "lib/sock", "lib/iscsi"],
        test_dirs=["test/nvmf", "test/iscsi_tgt"],
    ),
    "tcp_network": _profile(
        name="TCP/network disruption",
        aliases=["tcp", "network", "timeout", "reconnect", "disconnect", "packet loss"],
        scenarios=["timeout", "disconnect", "reconnect", "partial write", "address conflict"],
        failure_modes=["stuck connection", "retry storm", "resource leak", "wrong timeout surface"],
        observability=["socket status", "client-visible error", "logs", "metrics", "reconnect behavior"],
        graybox_evidence=["socket callbacks", "poller path", "transport error mapping"],
        source_entries=["lib/sock", "lib/nvmf", "lib/iscsi"],
        test_dirs=["test/nvmf", "test/iscsi_tgt"],
    ),
    "bdev_io": _profile(
        name="bdev IO lifecycle",
        aliases=["bdev", "block", "io", "submit", "complete", "reset", "failover"],
        scenarios=["open/close", "submit", "complete", "error return", "reset", "I/O drain"],
        failure_modes=["completion lost", "reset while pending", "double close", "wrong error propagation"],
        observability=["RPC status", "fio/bdev test output", "logs", "latency/IO counters"],
        graybox_evidence=["bdev descriptor", "I/O channel", "completion callback"],
        source_entries=["lib/bdev"],
        test_dirs=["test/bdev"],
    ),
    "rpc_config": _profile(
        name="RPC/config",
        aliases=["rpc", "jsonrpc", "config", "parameter", "duplicate"],
        scenarios=["invalid parameter", "duplicate call", "order error", "partial success rollback"],
        failure_modes=["stale config", "unclear error", "non-idempotent retry", "partial rollback failure"],
        observability=["RPC response", "config dump", "logs", "process state"],
        graybox_evidence=["RPC handler", "config object", "rollback path"],
        source_entries=["lib/rpc", "lib/jsonrpc"],
        test_dirs=["test/rpc"],
    ),
    "reactor_thread_poller": _profile(
        name="reactor/thread/poller",
        aliases=["reactor", "thread", "poller", "message", "scheduler"],
        scenarios=["cross-thread message", "poller blocking", "long task scheduling", "shutdown ordering"],
        failure_modes=["deadlock", "poller starvation", "message ordering bug", "shutdown hang"],
        observability=["thread logs", "latency", "task completion", "shutdown status"],
        graybox_evidence=["thread message queue", "poller registration", "reactor loop"],
        source_entries=["lib/thread", "lib/event"],
        test_dirs=["test/thread", "test/event"],
    ),
    "persistence_recovery": _profile(
        name="persistence/recovery",
        aliases=["blobstore", "ftl", "metadata", "persist", "recovery", "power loss"],
        scenarios=["metadata recovery", "space exhaustion", "unclean shutdown", "replay"],
        failure_modes=["metadata corruption", "lost allocation", "recovery hang", "wrong rollback"],
        observability=["mount result", "state after restart", "logs", "integrity check"],
        graybox_evidence=["metadata load", "superblock path", "replay path"],
        source_entries=["lib/blobstore", "lib/ftl"],
        test_dirs=["test/blobstore", "test/ftl"],
    ),
    "performance_regression": _profile(
        name="performance/regression",
        aliases=["performance", "latency", "throughput", "regression", "soak"],
        scenarios=["baseline throughput", "tail latency", "resource saturation", "long run"],
        failure_modes=["latency spike", "throughput drop", "memory growth", "CPU spin"],
        observability=["latency histogram", "throughput", "CPU/memory metrics", "logs"],
        graybox_evidence=["hot path", "poller cost", "queue depth behavior"],
        source_entries=["lib"],
        test_dirs=["test"],
    ),
    "resource_lifecycle": _profile(
        name="resource lifecycle",
        aliases=["resource", "leak", "cleanup", "free", "close", "teardown"],
        scenarios=["allocation failure", "partial init", "cleanup", "double close", "error path"],
        failure_modes=["leak", "use after free", "double free", "stale handle"],
        observability=["process memory", "logs", "repeat operation behavior", "sanitizer/test output"],
        graybox_evidence=["goto err path", "free/close pairing", "ownership transfer"],
        source_entries=["lib"],
        test_dirs=["test"],
    ),
    "concurrency_race": _profile(
        name="concurrency/race",
        aliases=["concurrency", "race", "parallel", "multi", "thread", "simultaneous"],
        scenarios=["parallel requests", "cancel during operation", "shutdown with in-flight IO", "repeated reconnect"],
        failure_modes=["race", "lost wakeup", "ordering violation", "deadlock"],
        observability=["operation outcome", "logs", "latency", "state convergence"],
        graybox_evidence=["lock boundary", "state transition", "thread handoff"],
        source_entries=["lib"],
        test_dirs=["test"],
    ),
    "observability_diagnostics": _profile(
        name="observability/diagnostics",
        aliases=["observability", "diagnostic", "log", "metric", "trace", "error message"],
        scenarios=["clear error", "log correlation", "metric update", "diagnostic package"],
        failure_modes=["misleading error", "missing log", "missing metric", "sensitive data exposure"],
        observability=["logs", "metrics", "RPC error", "diagnostic bundle"],
        graybox_evidence=["log branch", "error code mapping", "metric increment"],
        source_entries=["lib"],
        test_dirs=["test"],
    ),
}


SPDK_PROJECT_PROFILE: dict[str, Any] = {
    "project": "spdk",
    "modules": {
        "lib/nvmf": {"profiles": ["nvmeof_transport", "security_tls", "tcp_network"], "test_roots": ["test/nvmf"]},
        "lib/iscsi": {"profiles": ["iscsi_login", "security_tls", "tcp_network"], "test_roots": ["test/iscsi_tgt"]},
        "lib/bdev": {"profiles": ["bdev_io", "resource_lifecycle", "performance_regression"], "test_roots": ["test/bdev"]},
        "lib/blobstore": {"profiles": ["persistence_recovery", "resource_lifecycle"], "test_roots": ["test/blobstore"]},
        "lib/thread": {"profiles": ["reactor_thread_poller", "concurrency_race"], "test_roots": ["test/thread"]},
        "lib/event": {"profiles": ["reactor_thread_poller", "concurrency_race"], "test_roots": ["test/event"]},
        "lib/rpc": {"profiles": ["rpc_config", "observability_diagnostics"], "test_roots": ["test/rpc"]},
        "lib/jsonrpc": {"profiles": ["rpc_config", "observability_diagnostics"], "test_roots": ["test/rpc"]},
    },
}


ARTIFACT_TEMPLATES: dict[str, dict[str, Any]] = {
    "project_structure.md": {"preview": "markdown", "sections": ["项目结构", "测试相关目录", "入口说明"], "required_fields": ["source_roots", "test_roots"]},
    "source_reading_plan.md": {"preview": "markdown", "sections": ["阅读目标", "阅读顺序", "证据缺口"], "required_fields": ["target", "read_order", "evidence_policy"]},
    "module_map.md": {"preview": "markdown", "sections": ["模块边界", "入口", "依赖", "测试映射"], "required_fields": ["module", "entries", "test_mapping"]},
    "business_flow.md": {"preview": "markdown", "sections": ["外部触发", "流程步骤", "异常分支", "观测点"], "required_fields": ["steps", "evidence"]},
    "tester_code_understanding.md": {"preview": "markdown", "sections": ["测试视角摘要", "可观测行为", "不可直接依赖的内部细节"], "required_fields": ["observable_behavior", "boundaries"]},
    "sfmea.json": {
        "preview": "table",
        "required_fields": ["failure_mode", "cause", "effect", "detection", "severity", "occurrence", "detection_score", "rpn", "score_explanation", "mitigation", "source_evidence", "test_mapping"],
        "schema": {"type": "array"},
    },
    "black_box_cases.json": {
        "preview": "table",
        "required_fields": ["case_id", "scenario_name", "preconditions", "steps", "expected_result", "observability", "failure_diagnostics", "mapped_test_dir", "source_or_test_evidence"],
        "schema": {"type": "array"},
    },
    "black_box_cases.md": {"preview": "markdown", "sections": ["用例列表", "观测点", "诊断线索"], "required_fields": ["case_id", "steps", "expected_result"]},
    "test_strategy.md": {"preview": "markdown", "sections": ["范围", "风险", "分层策略", "执行顺序"], "required_fields": ["scope", "risks", "layers"]},
    "test_design.md": {"preview": "markdown", "sections": ["目标", "输入", "用例设计", "覆盖矩阵", "剩余风险"], "required_fields": ["target", "cases", "coverage"]},
    "coverage_gap_report.md": {"preview": "markdown", "sections": ["覆盖缺口", "入口", "补充建议"], "required_fields": ["gaps", "recommendations"]},
    "risk_review.md": {"preview": "markdown", "sections": ["高风险项", "证据", "建议"], "required_fields": ["risks", "evidence"]},
    "execution_checklist.md": {"preview": "markdown", "sections": ["前置检查", "执行步骤", "验收"], "required_fields": ["preflight", "steps", "acceptance"]},
}


def build_test_activity_contract(
    *,
    target: str,
    repo_path: str = "",
    workflow_outputs: list[dict[str, Any]] | None = None,
    user_requirements: str = "",
) -> dict[str, Any]:
    target_text = str(target or "").strip()
    combined_text = " ".join([target_text, str(user_requirements or "")]).strip()
    domain_profiles = _matched_profiles(combined_text)
    project_profile = _spdk_project_profile(repo_path=repo_path, target=combined_text, domain_profiles=domain_profiles)
    required_outputs = _requested_outputs(workflow_outputs or [], combined_text)
    artifact_contract = {
        artifact: _artifact_contract_payload(artifact, template)
        for artifact, template in ARTIFACT_TEMPLATES.items()
        if artifact in required_outputs
    }
    focus_rationale = _focus_rationale(
        domain_profiles=domain_profiles,
        project_profile=project_profile,
        user_requirements=user_requirements,
    )
    return {
        "contract_version": 1,
        "target": target_text,
        "domain_profiles": domain_profiles,
        "project_profile": project_profile,
        "user_requirements": str(user_requirements or ""),
        "required_outputs": required_outputs,
        "focus_rationale": focus_rationale,
        "evidence_policy": {
            "source_first": True,
            "prefer_artifacts": ["GitNexus", "CGC"],
            "must_cite_existing_source_or_test": True,
            "unverified_ai_suggestions_label": "ai_suggested_unverified",
        },
        "black_box_boundary": {
            "external_inputs_only": True,
            "forbidden_internal_steps": _unique_strings(
                item
                for profile_id in domain_profiles
                for item in PROFILE_REGISTRY[profile_id].get("forbidden_internal_steps", [])
            ),
        },
        "quality_gates": {
            "min_score": 80,
            "high_risk_requires_source_or_test_evidence": True,
            "black_box_cases_must_not_call_internal_functions": True,
            "missing_required_artifacts_block_delivery": True,
        },
        "executor_requirements": {
            "must_receive_full_user_input": True,
            "must_read_workspace_source_unless_user_declines": True,
            "must_generate_declared_artifacts": True,
            "invalid_short_greeting_is_failure": True,
        },
        "artifact_contract": artifact_contract,
    }


def audit_test_activity_artifacts(
    *,
    artifact_dir: str | Path,
    contract: dict[str, Any],
    repo_path: str = "",
) -> dict[str, Any]:
    root = Path(artifact_dir)
    repo = Path(str(repo_path or ""))
    issues: list[dict[str, Any]] = []
    for artifact, spec in (contract.get("artifact_contract") or {}).items():
        path = _artifact_path(root, artifact)
        if not path.exists():
            issues.append(_issue("missing_required_artifact", artifact, f"缺少交付件 {artifact}"))
            continue
        if artifact.endswith(".json"):
            payload = _read_json(path)
            issues.extend(_audit_json_artifact(artifact=artifact, payload=payload, spec=spec, repo=repo))
        elif not path.read_text(encoding="utf-8", errors="ignore").strip():
            issues.append(_issue("empty_artifact", artifact, f"{artifact} 内容为空"))
    score = max(0, 100 - len(issues) * 15)
    status = "deliverable" if score >= int((contract.get("quality_gates") or {}).get("min_score") or 80) and not issues else "needs_rework"
    return {
        "kind": "test_activity_quality_audit",
        "status": status,
        "deliverable": status == "deliverable",
        "score": score,
        "issue_count": len(issues),
        "issues": issues,
        "recommendations": _recommendations_for_issues(issues),
    }


def _matched_profiles(text: str) -> list[str]:
    matched: list[str] = []
    for profile_id, profile in PROFILE_REGISTRY.items():
        aliases = [profile_id.replace("_", " "), *profile.get("aliases", [])]
        if any(_term_matches(text, alias) for alias in aliases):
            matched.append(profile_id)
    if not matched and _term_matches(text, "kv"):
        matched.extend(["bdev_io", "persistence_recovery", "performance_regression"])
    if "nvmeof_transport" in matched and _term_matches(text, "tls") and "security_tls" not in matched:
        matched.append("security_tls")
    if "nvmeof_transport" in matched and _term_matches(text, "tcp") and "tcp_network" not in matched:
        matched.append("tcp_network")
    return matched or ["observability_diagnostics"]


def _term_matches(text: str, term: str) -> bool:
    haystack = str(text or "").lower()
    needle = str(term or "").lower().strip()
    if not needle:
        return False
    if len(needle) <= 3 and needle.isalpha():
        return bool(re.search(rf"(?<![a-z0-9]){re.escape(needle)}(?![a-z0-9])", haystack))
    return needle in haystack


def _spdk_project_profile(*, repo_path: str, target: str, domain_profiles: list[str]) -> dict[str, Any]:
    source_roots: list[str] = []
    test_roots: list[str] = []
    related_profiles: list[str] = []
    for root, payload in SPDK_PROJECT_PROFILE["modules"].items():
        profiles = [str(item) for item in payload.get("profiles") or []]
        if root in target or any(profile in domain_profiles for profile in profiles):
            source_roots.append(root)
            test_roots.extend(str(item) for item in payload.get("test_roots") or [])
            related_profiles.extend(profiles)
    for profile_id in domain_profiles:
        profile = PROFILE_REGISTRY.get(profile_id, {})
        source_roots.extend(str(item) for item in profile.get("recommended_source_entries") or [])
        test_roots.extend(str(item) for item in profile.get("recommended_test_dirs") or [])
    return {
        "project": "spdk" if "spdk" in str(repo_path).lower() or source_roots else "generic",
        "source_roots": _unique_strings(source_roots),
        "test_roots": _unique_strings(test_roots),
        "related_profiles": _unique_strings(related_profiles),
    }


def _requested_outputs(outputs: list[dict[str, Any]], text: str) -> list[str]:
    requested = [
        str(item.get("artifact") or item.get("path") or "").strip()
        for item in outputs
        if isinstance(item, dict)
        and str(item.get("artifact") or item.get("path") or "").strip() in ARTIFACT_TEMPLATES
    ]
    lower = text.lower()
    keyword_map = {
        "sfmea": "sfmea.json",
        "黑盒": "black_box_cases.json",
        "测试用例": "black_box_cases.json",
        "测试策略": "test_strategy.md",
        "测试设计": "test_design.md",
        "流程": "business_flow.md",
        "模块": "module_map.md",
        "项目结构": "project_structure.md",
    }
    for keyword, artifact in keyword_map.items():
        if keyword in lower or keyword in text:
            if artifact == "black_box_cases.json" and any(
                item in requested for item in ("black_box_cases.json", "black_box_cases.md")
            ):
                continue
            requested.append(artifact)
    return _unique_strings(requested or ["business_flow.md", "sfmea.json", "black_box_cases.json"])


def _artifact_contract_payload(artifact: str, template: dict[str, Any]) -> dict[str, Any]:
    payload = {
        "artifact": artifact,
        "preview": str(template.get("preview") or ""),
        "required_fields": [str(item) for item in template.get("required_fields") or []],
        "sections": [str(item) for item in template.get("sections") or []],
        "quality_checks": [
            "required_fields_present",
            "source_or_test_evidence_present",
            "black_box_boundary_respected",
        ],
        "download_filename": artifact,
    }
    if isinstance(template.get("schema"), dict):
        payload["schema"] = dict(template["schema"])
    return payload


def _focus_rationale(
    *,
    domain_profiles: list[str],
    project_profile: dict[str, Any],
    user_requirements: str,
) -> list[dict[str, Any]]:
    rationale = []
    if str(user_requirements or "").strip():
        rationale.append({"source": "user_explicit_requirement", "summary": str(user_requirements).strip()[:500]})
    for profile_id in domain_profiles:
        rationale.append({
            "source": "domain_test_profile",
            "profile_id": profile_id,
            "summary": PROFILE_REGISTRY[profile_id]["name"],
        })
    if project_profile.get("source_roots") or project_profile.get("test_roots"):
        rationale.append({
            "source": "project_source_and_test_layout",
            "source_roots": project_profile.get("source_roots") or [],
            "test_roots": project_profile.get("test_roots") or [],
        })
    rationale.append({"source": "team_policy", "summary": "源码优先、黑盒边界、低质量产物需补证据"})
    return rationale


def _audit_json_artifact(
    *,
    artifact: str,
    payload: Any,
    spec: dict[str, Any],
    repo: Path,
) -> list[dict[str, Any]]:
    if payload is None:
        return [_issue("invalid_json", artifact, f"{artifact} 不是有效 JSON")]
    rows = payload if isinstance(payload, list) else payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return [_issue("json_shape_invalid", artifact, f"{artifact} 必须是数组或包含 items 数组")]
    issues: list[dict[str, Any]] = []
    required_fields = [str(item) for item in spec.get("required_fields") or []]
    for index, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            issues.append(_issue("json_item_invalid", artifact, f"{artifact} 第 {index} 项不是对象"))
            continue
        missing = [
            field for field in required_fields
            if not _field_present(row, field)
        ]
        if missing:
            code = "missing_sfmea_fields" if artifact == "sfmea.json" else "missing_black_box_fields"
            issues.append(_issue(code, artifact, f"{artifact} 第 {index} 项缺少字段: {', '.join(missing)}", index=index, fields=missing))
        if artifact.startswith("black_box") and _black_box_boundary_violation(row):
            issues.append(_issue("black_box_boundary_violation", artifact, f"{artifact} 第 {index} 项混入内部函数调用或修改源码步骤", index=index))
        evidence_values = _evidence_strings(row)
        if artifact in {"sfmea.json", "black_box_cases.json"} and not evidence_values:
            issues.append(_issue("missing_source_or_test_evidence", artifact, f"{artifact} 第 {index} 项缺少源码或测试证据", index=index))
        for evidence in evidence_values:
            if _looks_like_repo_path(evidence) and not _repo_path_exists(repo, evidence):
                issues.append(_issue("evidence_path_not_found", artifact, f"证据路径不存在: {evidence}", index=index))
    return issues


def _black_box_boundary_violation(row: dict[str, Any]) -> bool:
    action_fields = [
        row.get("steps"),
        row.get("inputs"),
        row.get("operations"),
        row.get("test_steps"),
    ]
    text = " ".join(part for value in action_fields for part in _flatten_text(value)).lower()
    return bool(re.search(r"\b(call|invoke)\s+[a-z0-9_]*\(|直接调用|调用内部函数|修改源码|private struct|internal function", text))


def _field_present(row: dict[str, Any], field: str) -> bool:
    value = row.get(field)
    if value not in (None, "", []):
        return True
    aliases = {
        "severity": ["severity_score"],
        "occurrence": ["occurrence_score"],
        "source_evidence": ["file_path", "source_file"],
        "test_mapping": ["test_directory", "mapped_test_dir", "mitigation"],
        "source_or_test_evidence": ["file_path", "mapped_test_dir", "test_directory"],
        "observability": ["observable_signals"],
        "expected_result": ["expected"],
        "failure_diagnostics": ["diagnostics"],
    }
    return any(row.get(alias) not in (None, "", []) for alias in aliases.get(field, []))


def _evidence_strings(row: dict[str, Any]) -> list[str]:
    values: list[str] = []
    for key in ("source_evidence", "test_mapping", "source_or_test_evidence", "mapped_test_dir", "file_path", "test_directory"):
        value = row.get(key)
        if isinstance(value, str):
            values.append(value)
        elif isinstance(value, list):
            values.extend(str(item) for item in value if str(item).strip())
        elif isinstance(value, dict):
            values.extend(str(item) for item in value.values() if str(item).strip())
    return values


def _flatten_text(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [part for item in value.values() for part in _flatten_text(item)]
    if isinstance(value, list):
        return [part for item in value for part in _flatten_text(item)]
    return []


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _artifact_path(root: Path, artifact: str) -> Path:
    direct = root / artifact
    if direct.exists():
        return direct
    matches = sorted(root.glob(f"**/{artifact}"))
    return matches[0] if matches else direct


def _looks_like_repo_path(value: str) -> bool:
    text = str(value or "").strip()
    return bool(re.match(r"^(lib|test|include|module|app)/", text))


def _repo_path_exists(repo: Path, value: str) -> bool:
    if not repo.exists():
        return True
    candidate = repo / value.split(":", 1)[0]
    return candidate.exists()


def _issue(code: str, artifact: str, message: str, **extra: Any) -> dict[str, Any]:
    return {"code": code, "artifact": artifact, "message": message, **extra}


def _recommendations_for_issues(issues: list[dict[str, Any]]) -> list[str]:
    if not issues:
        return ["质量门禁已通过，可以交付。"]
    codes = {str(issue.get("code") or "") for issue in issues}
    recommendations: list[str] = []
    if any(code.startswith("missing_") for code in codes):
        recommendations.append("补齐缺失字段、源码证据和测试目录映射后重跑质量审计。")
    if "black_box_boundary_violation" in codes:
        recommendations.append("将黑盒步骤改为外部输入、操作、期望输出和观测点，不要要求调用内部函数或修改源码。")
    if "evidence_path_not_found" in codes:
        recommendations.append("重新检索 GitNexus/CGC 和本地源码，修正不存在的证据引用。")
    return recommendations or ["从低质量交付件重跑，要求执行器严格遵守 TestActivityContract。"]


def _unique_strings(values: Any) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result
