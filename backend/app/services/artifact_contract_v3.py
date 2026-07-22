"""Profile-aware, layered artifact declarations for test workflow runs."""

from __future__ import annotations

import hashlib
import json
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


def materialize_claim_evidence_ledger(artifact_dir: str | Path) -> dict[str, Any]:
    """Persist the shared L1/L2 truth source consumed by later test stages."""
    root = Path(artifact_dir)
    evidence_cards = _read_json_list(root / "evidence_cards.json")
    sfmea = _read_json_list(root / "sfmea.json")
    black_box_cases = _read_json_list(root / "black_box_cases.json")

    # Import here to keep the contract module dependency-light during task prepare.
    from app.services.source_driven_test_design import verify_technical_claims

    l1 = verify_technical_claims(
        source_pack={"evidence_cards": evidence_cards},
        sfmea=sfmea,
        black_box_cases=black_box_cases,
    )
    l2_by_claim = _behavior_verdicts_by_claim(root / "behavior_claim_validation.json")
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
