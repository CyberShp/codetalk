"""Profile-aware, layered artifact declarations for test workflow runs."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any


DEVELOPER_EXPLANATION_HEADINGS = (
    "1. 这里是干什么的",
    "2. 外部怎么触发",
    "3. 正常流程怎么走",
    "4. 分支怎么进入",
    "5. 状态怎么变化",
    "6. 资源怎么使用和释放",
    "7. 超时、重试、取消和恢复",
    "8. 并发和关键时序窗口",
    "9. 异常传播和潜伏故障",
    "10. 风险点",
    "11. 黑盒怎么测",
    "12. 源码追溯和未决项",
)


def default_artifact_contract_v3(*, profile_id: str) -> dict[str, object]:
    if profile_id not in {"rapid", "deep"}:
        raise ValueError(f"未知执行档位：{profile_id}")
    deep = profile_id == "deep"
    artifacts = [
        _item("快速分析报告.md", "deliverable", not deep, "markdown"),
        _item("覆盖缺口与建议.md", "deliverable", not deep, "markdown"),
        _item("完整分析报告.md", "deliverable", deep, "markdown"),
        _item("开发给测试讲代码.md", "deliverable", deep, "markdown"),
        _item("流程状态资源与异常传播.md", "deliverable", deep, "markdown"),
        _item("风险点与SFMEA.md", "deliverable", deep, "markdown"),
        _item("sfmea.json", "deliverable", True, "json"),
        _item("黑盒测试设计.md", "deliverable", deep, "markdown"),
        _item("black_box_cases.json", "deliverable", True, "json"),
        _item("source_analysis.md", "supporting", True, "markdown"),
        _item("source_scope.json", "supporting", True, "json"),
        _item("evidence_cards.json", "supporting", True, "json"),
        _item("claim_evidence_ledger.json", "supporting", True, "json"),
        _item("artifact_alignment_audit.json", "supporting", True, "json"),
        _item("input_consumption.json", "supporting", True, "json"),
        _item("task_artifact_manifest.json", "supporting", True, "json"),
        _item("provider_diagnostics.json", "diagnostic", False, "json"),
        _item("runtime_events.jsonl", "diagnostic", False, "jsonl"),
    ]
    return {
        "schema_version": "artifact-contract-v3",
        "profile_id": profile_id,
        "delivery_class": "full_test_delivery" if deep else "bounded_analysis",
        "artifacts": artifacts,
    }


def _item(artifact: str, layer: str, required: bool, format_name: str) -> dict[str, object]:
    return {"artifact": artifact, "layer": layer, "required": required, "format": format_name, "downloadable": layer != "diagnostic"}


def materialize_artifact_contract_v3_outputs(
    artifact_dir: str | Path,
    *,
    profile_id: str,
) -> list[str]:
    """Render named user deliverables from already materialized, auditable artifacts.

    This is intentionally deterministic: it never fills missing analysis with prose, so a
    named file cannot make an incomplete run look deliverable.
    """
    root = Path(artifact_dir)
    scope = _read_json_object(_find_artifact(root, "source_scope.json"))
    evidence = _read_json_list(_find_artifact(root, "evidence_cards.json"))
    flow_payload = _read_json(_find_artifact(root, "flow_cards.json"))
    flow_cards = _items(flow_payload)
    sfmea = _read_json_list(_find_artifact(root, "sfmea.json"))
    cases = _read_json_list(_find_artifact(root, "black_box_cases.json"))
    branches = _items(_read_json(_find_artifact(root, "branch_disposition.json")))
    states = _items(_read_json(_find_artifact(root, "state_transition_disposition.json")))
    resources = _items(_read_json(_find_artifact(root, "resource_lifecycle_disposition.json")))
    error_chains = _items(_read_json(_find_artifact(root, "error_propagation_chains.json")))
    explanation_coverage = _read_json_object(
        _find_artifact(root, "developer_explanation_coverage.json")
    )
    target = str(
        scope.get("analysis_target")
        or scope.get("target")
        or scope.get("query")
        or "已冻结分析范围"
    )
    written: list[str] = []

    if profile_id == "rapid":
        if scope and evidence:
            _write_markdown(
                root / "快速分析报告.md",
                [
                    "# 快速分析报告",
                    f"\n## 分析对象\n{target}",
                    "\n## 已验证代码证据",
                    *_evidence_lines(evidence),
                    "\n## 说明",
                    "本报告只引用本次运行已物化的源码证据；未验证结论不会写入交付件。",
                ],
            )
            written.append("快速分析报告.md")
        if scope and evidence:
            _write_markdown(
                root / "覆盖缺口与建议.md",
                [
                    "# 覆盖缺口与建议",
                    f"\n分析对象：{target}",
                    "\n- 请优先补充尚未形成流程或测试证据的分支。",
                    "- 对每条建议保留源码位置、外部观测点和可执行前置条件。",
                ],
            )
            written.append("覆盖缺口与建议.md")
        # Rapid is intentionally bounded, not incomplete.  It delivers the
        # targeted SFMEA and black-box design promised by its stage contract,
        # while the comprehensive report and developer explanation remain
        # deep-only outputs below.
        if sfmea:
            _write_markdown(
                root / "风险点与SFMEA.md",
                ["# 定向风险点与 SFMEA", *_sfmea_lines(sfmea)],
            )
            written.append("风险点与SFMEA.md")
        if cases:
            _write_markdown(
                root / "黑盒测试设计.md",
                ["# 定向黑盒测试设计", *_case_lines(cases)],
            )
            written.append("黑盒测试设计.md")
        materialize_artifact_alignment_audit(root, profile_id=profile_id)
        return written

    if profile_id != "deep":
        raise ValueError(f"未知执行档位：{profile_id}")
    if scope and evidence and flow_cards and sfmea and cases:
        _write_markdown(
            root / "完整分析报告.md",
            [
                "# 完整测试活动分析报告",
                f"\n## 分析对象\n{target}",
                "\n## 代码证据", *_evidence_lines(evidence),
                "\n## 流程摘要", *_flow_lines(flow_cards),
                f"\n## 风险与 SFMEA\n已形成 {len(sfmea)} 条风险项。",
                f"\n## 黑盒测试设计\n已形成 {len(cases)} 条可执行候选用例。",
            ],
        )
        written.append("完整分析报告.md")
    if evidence and flow_cards:
        _write_markdown(
            root / "开发给测试讲代码.md",
            _developer_explanation_lines(
                target=target,
                evidence=evidence,
                flow_cards=flow_cards,
                branches=branches,
                states=states,
                resources=resources,
                error_chains=error_chains,
                sfmea=sfmea,
                cases=cases,
                coverage=explanation_coverage,
            ),
        )
        written.append("开发给测试讲代码.md")
        _write_markdown(
            root / "流程状态资源与异常传播.md",
            ["# 流程、状态、资源与异常传播", "\n## 已建模流程", *_flow_lines(flow_cards)],
        )
        written.append("流程状态资源与异常传播.md")
    if sfmea:
        _write_markdown(
            root / "风险点与SFMEA.md",
            ["# 风险点与 SFMEA", *_sfmea_lines(sfmea)],
        )
        written.append("风险点与SFMEA.md")
    if cases:
        _write_markdown(
            root / "黑盒测试设计.md",
            ["# 黑盒测试设计", *_case_lines(cases)],
        )
        written.append("黑盒测试设计.md")
    materialize_artifact_alignment_audit(root, profile_id=profile_id)
    return written


def materialize_artifact_alignment_audit(
    artifact_dir: str | Path,
    *,
    profile_id: str,
) -> dict[str, Any]:
    """Verify that human-readable deliveries retain structured artifact IDs.

    JSON is the canonical task record; Markdown is the tester-facing delivery.
    This audit makes the relationship explicit so a renderer cannot silently
    omit an evidence, flow, risk, or case identifier while still looking
    complete to a human reader.
    """
    root = Path(artifact_dir)
    pairs = [
        ("evidence_cards.json", "快速分析报告.md" if profile_id == "rapid" else "完整分析报告.md", ("evidence_id", "id")),
        ("flow_cards.json", "流程状态资源与异常传播.md", ("flow_id", "id")),
        ("sfmea.json", "风险点与SFMEA.md", ("sfmea_id", "risk_id", "id")),
        ("black_box_cases.json", "黑盒测试设计.md", ("case_id", "id")),
    ]
    results: list[dict[str, Any]] = []
    for json_name, markdown_name, id_keys in pairs:
        json_path = _find_artifact(root, json_name)
        if not json_path.is_file() or json_path.stat().st_size == 0:
            continue
        rows = _items(_read_json(json_path))
        if json_name != "flow_cards.json":
            rows = _read_json_list(json_path)
        identifiers = [
            next((str(row.get(key)).strip() for key in id_keys if str(row.get(key) or "").strip()), "")
            for row in rows
        ]
        missing_identifiers = [f"row-{index}" for index, value in enumerate(identifiers, start=1) if not value]
        identifiers = [value for value in identifiers if value]
        markdown_path = _find_artifact(root, markdown_name)
        markdown = (
            markdown_path.read_text(encoding="utf-8", errors="replace")
            if markdown_path.is_file()
            else ""
        )
        missing_ids = [value for value in identifiers if value not in markdown]
        status = (
            "not_applicable"
            if not rows
            else "passed"
            if markdown and not missing_ids and not missing_identifiers
            else "blocked"
        )
        results.append({
            "json_artifact": json_name,
            "markdown_artifact": markdown_name,
            "json_sha256": _file_sha256(json_path),
            "markdown_sha256": _file_sha256(markdown_path) if markdown_path.is_file() else "",
            "structured_ids": identifiers,
            "missing_ids": missing_ids,
            "missing_identifier_rows": missing_identifiers,
            "status": status,
        })
    checked = [item for item in results if item["status"] != "not_applicable"]
    audit = {
        "kind": "artifact_alignment_audit",
        "schema_version": "artifact-alignment-audit-v1",
        "profile_id": profile_id,
        "status": "blocked" if any(item["status"] == "blocked" for item in checked) else "passed",
        "pairs": results,
    }
    _write_json(root / "artifact_alignment_audit.json", audit)
    return audit


def enrich_external_agent_claim_bindings(artifact_dir: str | Path) -> dict[str, int]:
    """Attach deterministic L1 bindings when an Agent cites evidence cards by ID.

    This adapter never invents a fact: it only turns an existing row's evidence
    card reference into a claim whose statement is the card's exact excerpt.
    Rows without a resolvable card remain unbound and fail the quality gate.
    """
    root = Path(artifact_dir)
    cards = _read_json_list(_find_artifact(root, "evidence_cards.json"))
    by_id = {str(card.get("evidence_id") or ""): card for card in cards if str(card.get("evidence_id") or "")}
    by_path: dict[str, dict[str, Any]] = {}
    for card in cards:
        file_path = str(card.get("file_path") or card.get("path") or "").strip()
        if file_path and file_path not in by_path:
            by_path[file_path] = card
    changed: dict[str, int] = {}
    for artifact, row_id_key, evidence_key in (
        ("sfmea.json", "sfmea_id", "source_evidence"),
        ("black_box_cases.json", "case_id", "source_or_test_evidence"),
    ):
        path = _find_artifact(root, artifact)
        rows = _read_json_list(path)
        count = 0
        for index, row in enumerate(rows, start=1):
            references = row.get(evidence_key) or []
            if not isinstance(references, list):
                references = []
            card = next((
                by_id.get(str(value).split(":", 1)[0].strip())
                for value in references
                if by_id.get(str(value).split(":", 1)[0].strip())
            ), None)
            if not isinstance(card, dict):
                declared_path = str(row.get("file_path") or row.get("path") or "").strip()
                card = by_path.get(declared_path)
            rebound = _rebind_external_agent_claims(
                row=row,
                cards=cards,
                by_id=by_id,
                fallback_card=card if isinstance(card, dict) else None,
            )
            if rebound:
                canonical_ids = [
                    str(ref.get("evidence_id") or "")
                    for claim in row.get("technical_claims") or []
                    if isinstance(claim, dict)
                    for ref in claim.get("evidence") or []
                    if isinstance(ref, dict) and str(ref.get("evidence_id") or "")
                ]
                for evidence_id in canonical_ids:
                    if evidence_id not in references:
                        references.append(evidence_id)
                row[evidence_key] = references
                count += 1
                continue
            if not isinstance(card, dict):
                continue
            excerpt = str(card.get("excerpt") or "").strip()
            file_path = str(card.get("file_path") or card.get("path") or "").strip()
            if not excerpt or not file_path:
                continue
            symbol = next((str(value) for value in card.get("symbols") or [] if str(value)), "")
            row_id = str(row.get(row_id_key) or f"row-{index}")
            evidence_id = str(card.get("evidence_id") or "")
            if evidence_id and evidence_id not in references:
                references.append(evidence_id)
                row[evidence_key] = references
            row["technical_claims"] = [{
                "claim_id": f"AUTO-{row_id}-E1",
                "type": "source",
                "statement": excerpt,
                "evidence": [{
                    "evidence_id": evidence_id,
                    "path": file_path,
                    "symbol": symbol,
                    "lines": f"L{int(card.get('start_line') or 0)}-L{int(card.get('end_line') or 0)}",
                    "quote": excerpt,
                }],
            }]
            count += 1
        if count:
            _write_json(path, rows)
            changed[artifact] = count
    return changed


def _rebind_external_agent_claims(
    *,
    row: dict[str, Any],
    cards: list[dict[str, Any]],
    by_id: dict[str, dict[str, Any]],
    fallback_card: dict[str, Any] | None,
) -> bool:
    """Replace Agent-authored evidence metadata with a local verified card.

    Agent output is allowed to carry a useful human explanation, but it cannot
    become a source of truth for IDs, excerpts, symbols, or line ranges.  The
    L1 claim is therefore normalized to the exact deterministic card excerpt;
    the original semantic explanation remains available as ``semantic_statement``.
    """
    claims = row.get("technical_claims")
    if not isinstance(claims, list) or not claims:
        return False
    changed = False
    for claim in claims:
        if not isinstance(claim, dict):
            continue
        normalized: list[dict[str, Any]] = []
        for reference in claim.get("evidence") or []:
            if not isinstance(reference, dict):
                continue
            card = _canonical_card_for_reference(
                reference=reference,
                cards=cards,
                by_id=by_id,
                fallback_card=fallback_card,
            )
            if card is None:
                normalized.append(reference)
                continue
            excerpt = str(card.get("excerpt") or "").strip()
            file_path = str(card.get("file_path") or card.get("path") or "").strip()
            evidence_id = str(card.get("evidence_id") or "").strip()
            if not excerpt or not file_path or not evidence_id:
                normalized.append(reference)
                continue
            symbol = next((str(value) for value in card.get("symbols") or [] if str(value)), "")
            normalized.append({
                "evidence_id": evidence_id,
                "path": file_path,
                "lines": f"L{int(card.get('start_line') or 0)}-L{int(card.get('end_line') or 0)}",
                "symbol": symbol,
                "quote": excerpt,
            })
            if str(claim.get("statement") or "") != excerpt:
                claim.setdefault("semantic_statement", str(claim.get("statement") or ""))
                claim["statement"] = excerpt
            changed = True
        if normalized:
            claim["evidence"] = normalized
    return changed


def _canonical_card_for_reference(
    *,
    reference: dict[str, Any],
    cards: list[dict[str, Any]],
    by_id: dict[str, dict[str, Any]],
    fallback_card: dict[str, Any] | None,
) -> dict[str, Any] | None:
    evidence_id = str(reference.get("evidence_id") or "").split(":", 1)[0].strip()
    if evidence_id and evidence_id in by_id:
        direct = by_id[evidence_id]
        if _card_matches_reference(direct, reference):
            return direct
    expected_path = str(reference.get("path") or "").strip()
    if expected_path:
        start, end = _reference_line_range(reference.get("lines"))
        for card in cards:
            if str(card.get("file_path") or card.get("path") or "") != expected_path:
                continue
            if _card_matches_reference(card, reference):
                return card
    return fallback_card


def _card_matches_reference(card: dict[str, Any], reference: dict[str, Any]) -> bool:
    expected_path = str(reference.get("path") or "").strip()
    card_path = str(card.get("file_path") or card.get("path") or "").strip()
    if expected_path and expected_path != card_path:
        return False
    start, end = _reference_line_range(reference.get("lines"))
    if not start:
        return True
    card_start = int(card.get("start_line") or 0)
    card_end = int(card.get("end_line") or 0)
    return card_start <= start and end <= card_end


def _reference_line_range(value: Any) -> tuple[int, int]:
    numbers = [int(item) for item in re.findall(r"\d+", str(value or ""))]
    if not numbers:
        return 0, 0
    return numbers[0], numbers[-1]


def validate_artifact_contract_v3_outputs(
    artifact_dir: str | Path,
    *,
    profile_id: str,
) -> dict[str, Any]:
    """Fail closed when a required V3 file is not physically present and non-empty."""
    root = Path(artifact_dir)
    contract = default_artifact_contract_v3(profile_id=profile_id)
    required = [
        str(item["artifact"])
        for item in contract["artifacts"]
        if isinstance(item, dict) and bool(item.get("required"))
    ]
    present = [
        name for name in required
        if (path := _find_artifact(root, name)).is_file() and path.stat().st_size > 0
    ]
    missing = [name for name in required if name not in present]
    malformed: dict[str, list[str]] = {}
    explanation = _find_artifact(root, "开发给测试讲代码.md")
    if profile_id == "deep" and explanation.is_file():
        absent_headings = [
            heading
            for heading in DEVELOPER_EXPLANATION_HEADINGS
            if heading not in explanation.read_text(encoding="utf-8", errors="replace")
        ]
        if absent_headings:
            malformed["开发给测试讲代码.md"] = absent_headings
    alignment_path = _find_artifact(root, "artifact_alignment_audit.json")
    if alignment_path.is_file():
        alignment = _read_json_object(alignment_path)
        if alignment.get("status") != "passed":
            malformed["artifact_alignment_audit.json"] = [
                "结构化 JSON 与 Markdown 交付件的 ID 对齐校验未通过。"
            ]
    return {
        "kind": "artifact_contract_v3_validation",
        "schema_version": "artifact-contract-v3-validation-v1",
        "profile_id": profile_id,
        "status": "passed" if not missing and not malformed else "blocked",
        "required": required,
        "present_required": present,
        "missing_required": missing,
        "malformed_required": malformed,
    }


def materialize_claim_evidence_ledger(artifact_dir: str | Path) -> dict[str, Any]:
    """Persist the shared L1/L2 truth source consumed by later test stages."""
    root = Path(artifact_dir)
    # Workflows materialize step-owned artifacts under agent_runs/<step>.  The
    # V3 ledger is task-owned, so it must read the same resolved files as the
    # artifact contract instead of silently producing an empty fact ledger.
    evidence_cards = _read_json_list(_find_artifact(root, "evidence_cards.json"))
    sfmea = _read_json_list(_find_artifact(root, "sfmea.json"))
    black_box_cases = _read_json_list(_find_artifact(root, "black_box_cases.json"))

    # Import here to keep the contract module dependency-light during task prepare.
    from app.services.source_driven_test_design import verify_technical_claims

    l1 = verify_technical_claims(
        source_pack={"evidence_cards": evidence_cards},
        sfmea=sfmea,
        black_box_cases=black_box_cases,
    )
    l2_by_claim = _behavior_verdicts_by_claim(
        _find_artifact(root, "behavior_claim_validation.json")
    )
    claims: list[dict[str, Any]] = []
    for raw_claim in l1.get("claims") or []:
        if not isinstance(raw_claim, dict):
            continue
        claim_id = str(raw_claim.get("claim_id") or "")
        binding = str(raw_claim.get("binding") or "")
        l1_status = str(raw_claim.get("status") or "insufficient")
        l2_status = _lookup_behavior_verdict(l2_by_claim, claim_id, binding)
        verification_status = _combined_verification_status(l1_status, l2_status)
        claims.append(
            {
                "claim_id": claim_id,
                "type": str(raw_claim.get("type") or "source_behavior"),
                "statement": str(raw_claim.get("statement") or ""),
                "artifact": str(raw_claim.get("artifact") or ""),
                "binding": binding,
                "l1_status": l1_status,
                "l2_status": l2_status,
                "verification_status": verification_status,
                "evidence_checks": [
                    dict(item)
                    for item in raw_claim.get("evidence_checks") or []
                    if isinstance(item, dict)
                ],
            }
        )
    contradicted = sum(item["verification_status"] == "contradicted" for item in claims)
    insufficient = sum(item["verification_status"] == "insufficient" for item in claims)
    verified = sum(item["verification_status"] == "verified" for item in claims)
    status = (
        "not_checked"
        if not claims
        else "blocked"
        if contradicted or insufficient
        else "passed"
    )
    payload: dict[str, Any] = {
        "kind": "claim_evidence_ledger",
        "schema_version": "claim-evidence-ledger-v3",
        "status": status,
        "evidence_cards_sha256": _json_sha256(evidence_cards),
        "summary": {
            "total": len(claims),
            "verified": verified,
            "contradicted": contradicted,
            "insufficient": insufficient,
        },
        "claims": claims,
    }
    _write_json(root / "claim_evidence_ledger.json", payload)
    return payload


def _behavior_verdicts_by_claim(path: Path) -> dict[tuple[str, str], str]:
    payload = _read_json_object(path)
    result: dict[tuple[str, str], str] = {}
    for item in payload.get("claims") or []:
        if not isinstance(item, dict):
            continue
        claim_id = str(item.get("claim_id") or "")
        if not claim_id:
            continue
        result[(claim_id, str(item.get("binding") or ""))] = str(
            item.get("status") or "insufficient"
        )
    return result


def _lookup_behavior_verdict(
    verdicts: dict[tuple[str, str], str], claim_id: str, binding: str
) -> str:
    value = verdicts.get((claim_id, binding))
    if value is None:
        value = verdicts.get((claim_id, ""))
    return str(value or "not_checked")


def _combined_verification_status(l1_status: str, l2_status: str) -> str:
    if l1_status == "contradicted" or l2_status in {"contradicts", "contradicted"}:
        return "contradicted"
    if l1_status != "verified":
        return "insufficient"
    if l2_status in {"not_checked", "supports", "verified"}:
        return "verified"
    return "insufficient"


def _read_json_list(path: Path) -> list[dict[str, Any]]:
    payload = _read_json(path)
    return [dict(item) for item in payload] if isinstance(payload, list) else []


def _read_json_object(path: Path) -> dict[str, Any]:
    payload = _read_json(path)
    return dict(payload) if isinstance(payload, dict) else {}


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _find_artifact(root: Path, name: str) -> Path:
    direct = root / name
    if direct.is_file():
        return direct
    return next((path for path in root.rglob(name) if path.is_file()), direct)


def _items(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [dict(item) for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict) and isinstance(payload.get("items"), list):
        return [dict(item) for item in payload["items"] if isinstance(item, dict)]
    return []


def _evidence_lines(evidence: list[dict[str, Any]]) -> list[str]:
    result = []
    for item in evidence[:20]:
        evidence_id = str(item.get("evidence_id") or item.get("id") or "").strip()
        path = str(item.get("file_path") or item.get("path") or "未知文件")
        start = int(item.get("start_line") or 0)
        end = int(item.get("end_line") or 0)
        location = (
            f":L{start}-L{end}" if start and end and start != end
            else f":L{start}" if start
            else ""
        )
        symbols = ", ".join(str(value) for value in item.get("symbols") or [] if str(value))
        label = f"[{evidence_id}] " if evidence_id else ""
        result.append(f"- {label}`{path}{location}`{f'：{symbols}' if symbols else ''}")
    return result or ["- 未发现可交付的代码证据。"]


def _flow_lines(flow_cards: list[dict[str, Any]]) -> list[str]:
    result = []
    for item in flow_cards[:20]:
        flow_id = str(item.get("flow_id") or item.get("id") or "").strip()
        title = str(item.get("title") or item.get("name") or flow_id or "流程节点")
        detail = str(item.get("summary") or item.get("description") or "")
        label = f"[{flow_id}] " if flow_id else ""
        result.append(f"- {label}**{title}**{f'：{detail}' if detail else ''}")
    return result or ["- 未形成可交付的流程卡片。"]


def _developer_explanation_lines(
    *,
    target: str,
    evidence: list[dict[str, Any]],
    flow_cards: list[dict[str, Any]],
    branches: list[dict[str, Any]],
    states: list[dict[str, Any]],
    resources: list[dict[str, Any]],
    error_chains: list[dict[str, Any]],
    sfmea: list[dict[str, Any]],
    cases: list[dict[str, Any]],
    coverage: dict[str, Any],
) -> list[str]:
    """Render the fixed test-engineering explanation contract without inventing facts.

    The source-driven stage owns discovery.  This renderer only assembles its
    ledgers into the twelve questions a black-box tester needs answered.  A
    missing ledger becomes an explicit verification gap rather than prose that
    accidentally upgrades an assumption into a source fact.
    """
    flow = flow_cards[0] if flow_cards else {}
    unresolved = [str(item) for item in coverage.get("uncovered_items") or [] if str(item)]
    purpose = str(flow.get("purpose") or flow.get("summary") or "当前流程用途尚未形成可交付说明。")
    trigger = str(flow.get("trigger") or "当前证据未直接覆盖外部触发入口，需在测试环境确认。")
    normal_path = _string_values(flow.get("normal_path"))
    abnormal_paths = _string_values(flow.get("abnormal_paths"))
    concurrency = _string_values(flow.get("concurrency_windows"))
    observations = _string_values(flow.get("external_observations"))

    lines = [
        "# 开发给测试讲代码",
        f"\n分析对象：{target}",
        "\n本交付件由本次已物化的证据、流程与测试台账确定性生成。"
        "未直接被证据覆盖的内容会显式标为待验证，不作为源码事实交付。",
        "\n## 1. 这里是干什么的",
        f"- {purpose}",
        "\n## 2. 外部怎么触发",
        f"- 已识别触发点：{trigger}",
        "- 黑盒入口应通过公开协议、CLI、配置或服务 API 构造；不要把内部函数调用写进测试步骤。",
        "\n## 3. 正常流程怎么走",
        *_bullet_or_gap(normal_path, "当前证据未直接覆盖完整正常路径，需补充流程证据。"),
        "\n## 4. 分支怎么进入",
        *_branch_lines(branches, abnormal_paths),
        "\n## 5. 状态怎么变化",
        *_state_lines(states),
        "\n## 6. 资源怎么使用和释放",
        *_resource_lines(resources),
        "\n## 7. 超时、重试、取消和恢复",
        *_timeout_recovery_lines(flow, error_chains),
        "\n## 8. 并发和关键时序窗口",
        *_bullet_or_gap(concurrency, "当前证据未直接覆盖并发窗口，需用并发压力或时序注入验证。"),
        "\n## 9. 异常传播和潜伏故障",
        *_error_chain_lines(error_chains),
        "\n## 10. 风险点",
        *_risk_lines(sfmea),
        "\n## 11. 黑盒怎么测",
        *_test_lines(cases, observations),
        "\n## 12. 源码追溯和未决项",
        "- 已验证源码证据：",
        *_evidence_lines(evidence),
        *_unresolved_lines(unresolved),
    ]
    return lines


def _string_values(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _bullet_or_gap(values: list[str], gap: str) -> list[str]:
    return [f"- {value}" for value in values[:12]] or [f"- {gap}"]


def _branch_lines(branches: list[dict[str, Any]], abnormal_paths: list[str]) -> list[str]:
    lines = _bullet_or_gap(abnormal_paths, "当前证据未直接覆盖可交付的异常路径，需补充分支场景。")
    for item in branches[:12]:
        condition = str(item.get("condition") or item.get("id") or "未命名分支")
        disposition = str(item.get("disposition") or "need_verify")
        lines.append(f"- `{condition}`：{_disposition_text(disposition, item.get('reason'))}")
    return lines


def _state_lines(states: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    for item in states[:12]:
        state = str(item.get("state") or item.get("id") or "未命名状态")
        transitions = [
            str(row.get("text") or row.get("symbol") or "").strip()
            for row in item.get("transitions") or []
            if isinstance(row, dict) and str(row.get("text") or row.get("symbol") or "").strip()
        ]
        detail = "；".join(transitions[:3]) or _disposition_text(item.get("disposition"), item.get("reason"))
        lines.append(f"- `{state}`：{detail}")
    return lines or ["- 当前证据未直接覆盖状态迁移，需补充状态机或运行观测。"]


def _resource_lines(resources: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    for item in resources[:12]:
        name = str(item.get("name") or item.get("kind") or item.get("id") or "未命名资源")
        detail = "；".join(
            value for value in (
                str(item.get("allocation") or "").strip(),
                str(item.get("normal_release") or "").strip(),
                str(item.get("abnormal_release") or "").strip(),
                str(item.get("invariant") or "").strip(),
            ) if value and value != "need_verify"
        )
        lines.append(f"- {name}：{detail or '当前证据未直接覆盖完整生命周期，需验证申请、释放、耗尽与恢复。'}")
    return lines or ["- 当前证据未直接覆盖资源生命周期，需补充资源台账。"]


def _timeout_recovery_lines(flow: dict[str, Any], error_chains: list[dict[str, Any]]) -> list[str]:
    values = _string_values(flow.get("boundary_and_wrap"))
    if error_chains:
        values.extend(str(item.get("local_effect") or item.get("external_observation") or "").strip() for item in error_chains[:8])
    return _bullet_or_gap(
        [value for value in values if value],
        "当前证据未直接覆盖超时、重试、取消和恢复的完整闭环，需以故障注入和恢复后请求验证。",
    )


def _error_chain_lines(error_chains: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    for item in error_chains[:12]:
        trigger = str(item.get("trigger") or item.get("source") or item.get("id") or "未命名异常")
        effect = str(item.get("downstream_effect") or item.get("local_effect") or item.get("external_observation") or "").strip()
        lines.append(f"- {trigger}：{effect or '待验证异常传播与外部可观测结果。'}")
    return lines or ["- 当前证据未直接覆盖异常传播链；不要把上游异常在下游表现正常误判为正常分支。"]


def _risk_lines(sfmea: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    for item in sfmea[:12]:
        title = str(item.get("failure_mode") or item.get("title") or item.get("id") or "未命名风险")
        status = str(item.get("risk_status") or "test_hypothesis")
        lines.append(f"- {title}：{'已观测缺陷' if status == 'observed_defect' else '风险假设，须通过测试验证'}。")
    return lines or ["- 当前证据未直接覆盖可交付风险项，需补充 SFMEA。"]


def _test_lines(cases: list[dict[str, Any]], observations: list[str]) -> list[str]:
    lines: list[str] = []
    for item in cases[:12]:
        title = str(item.get("title") or item.get("case_id") or item.get("id") or "未命名用例")
        expected = str(item.get("expected_result") or item.get("expected") or "观察外部结果、日志和指标")
        lines.append(f"- {title}：预期 {expected}")
    if not lines:
        lines.append("- 当前证据未直接覆盖可执行黑盒用例，需补充前置条件、操作、观测点和诊断线索。")
    if observations:
        lines.append(f"- 统一观测点：{'；'.join(observations[:8])}")
    return lines


def _unresolved_lines(unresolved: list[str]) -> list[str]:
    if not unresolved:
        return ["- 未决项：本次开发讲解台账未报告未覆盖条目。"]
    preview = "、".join(unresolved[:20])
    suffix = " 等" if len(unresolved) > 20 else ""
    return [f"- 未决项：{preview}{suffix}。这些条目仍需补充证据或测试映射。"]


def _disposition_text(disposition: Any, reason: Any) -> str:
    text = str(reason or "").strip()
    if text:
        return text
    return "已保留" if str(disposition) in {"retain", "covered_by_other", "merge_into"} else "当前证据未直接覆盖，需要验证。"


def _sfmea_lines(rows: list[dict[str, Any]]) -> list[str]:
    lines = [
        "本文件由 `sfmea.json` 确定性生成。每项先区分已验证的源码事实与待执行测试验证的风险假设；"
        "风险假设不是已观测缺陷。",
    ]
    for index, item in enumerate(rows[:50], start=1):
        risk_id = str(
            item.get("sfmea_id") or item.get("risk_id") or item.get("id") or f"SFMEA-{index:02d}"
        ).strip()
        title = str(
            item.get("failure_mode") or item.get("title") or "未命名风险项"
        ).strip()
        is_hypothesis = str(item.get("risk_status") or "test_hypothesis") != "observed_defect"
        lines.extend([f"\n## {risk_id} · {title}", ""])
        if is_hypothesis:
            lines.append("- 风险状态：风险假设，待故障注入验证（不是已观测缺陷）。")
        else:
            lines.append("- 风险状态：已观测缺陷；仍须以本项已绑定的源码证据复核。")
        facts = _sfmea_verified_fact_lines(item)
        if facts:
            lines.extend(["- 已验证源码事实：", *facts])
        else:
            lines.append("- 已验证源码事实：未形成可展示的逐字源码锚点，不能据此确认产品事实。")
        interpretation = str(item.get("evidence_interpretation") or "").strip()
        if interpretation:
            lines.append(f"- 证据解释：{interpretation}")
        cause = str(item.get("cause") or item.get("mechanism") or "待补充").strip()
        lines.append(
            f"- {'待验证的偏离条件' if is_hypothesis else '缺陷触发条件'}：{cause}"
        )
        effect = str(item.get("effect") or item.get("description") or "待补充").strip()
        lines.append(
            f"- {'若该假设发生的潜在影响' if is_hypothesis else '已观测影响'}：{effect}"
        )
        lines.append(f"- 验证/缓解：{_artifact_value(item.get('mitigation'))}")
        mapping = _artifact_value(item.get("test_mapping"))
        if mapping != "待补充":
            lines.append(f"- 测试映射：{mapping}")
        evidence = _artifact_value(item.get("source_evidence"))
        if evidence != "待补充":
            lines.append(f"- 证据卡引用：{evidence}")
    return lines


def _sfmea_verified_fact_lines(item: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    for claim in item.get("technical_claims") or []:
        if not isinstance(claim, dict):
            continue
        statement = str(claim.get("statement") or "").strip()
        for evidence in claim.get("evidence") or []:
            if not isinstance(evidence, dict):
                continue
            path = str(evidence.get("path") or "").strip()
            location = str(evidence.get("lines") or "").strip()
            anchor = f"{path}:{location}" if path and location else path or location
            quote = str(evidence.get("quote") or statement).strip()
            if anchor or quote:
                lines.append(
                    f"  - `{anchor or '未定位源码'}`：`{quote or '未提取原文'}`"
                )
    return list(dict.fromkeys(lines))


def _artifact_value(value: Any) -> str:
    if isinstance(value, list):
        rendered = "；".join(str(item).strip() for item in value if str(item).strip())
        return rendered or "待补充"
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value or "待补充").strip()


def _case_lines(rows: list[dict[str, Any]]) -> list[str]:
    return [
        f"- **[{item.get('case_id') or item.get('id') or f'CASE-{index:03d}'}] {item.get('title') or item.get('scenario_name') or '测试用例'}**：{item.get('expected_result') or item.get('expected') or '详见 JSON 交付件'}"
        for index, item in enumerate(rows[:100], start=1)
    ]


def _write_markdown(path: Path, lines: list[str]) -> None:
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def _json_sha256(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    temporary.replace(path)
