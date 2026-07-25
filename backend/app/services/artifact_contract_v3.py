"""Profile-aware, layered artifact declarations for test workflow runs."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any


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
            [
                "# 开发给测试讲代码",
                "\n## 源码入口与证据", *_evidence_lines(evidence),
                "\n## 业务流程", *_flow_lines(flow_cards),
                "\n## 测试解读",
                "请以外部输入、状态、日志、指标和可观测结果构造测试，不把内部函数调用写入黑盒步骤。",
            ],
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
    return written


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
    return {
        "kind": "artifact_contract_v3_validation",
        "schema_version": "artifact-contract-v3-validation-v1",
        "profile_id": profile_id,
        "status": "passed" if not missing else "blocked",
        "required": required,
        "present_required": present,
        "missing_required": missing,
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
        path = str(item.get("file_path") or item.get("path") or "未知文件")
        symbols = ", ".join(str(value) for value in item.get("symbols") or [] if str(value))
        result.append(f"- `{path}`{f'：{symbols}' if symbols else ''}")
    return result or ["- 未发现可交付的代码证据。"]


def _flow_lines(flow_cards: list[dict[str, Any]]) -> list[str]:
    result = []
    for item in flow_cards[:20]:
        title = str(item.get("title") or item.get("name") or item.get("id") or "流程节点")
        detail = str(item.get("summary") or item.get("description") or "")
        result.append(f"- **{title}**{f'：{detail}' if detail else ''}")
    return result or ["- 未形成可交付的流程卡片。"]


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
        f"- **{item.get('title') or item.get('scenario_name') or item.get('case_id') or '测试用例'}**：{item.get('expected_result') or item.get('expected') or '详见 JSON 交付件'}"
        for item in rows[:100]
    ]


def _write_markdown(path: Path, lines: list[str]) -> None:
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def _json_sha256(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    temporary.replace(path)
