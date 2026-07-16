from __future__ import annotations

import hashlib
import json
import re
import subprocess
import time
from pathlib import Path
from typing import Any


FLOW_EVIDENCE_VERSION = "flow-evidence-pack-v2"
_FLOW_EVIDENCE_VERSION = FLOW_EVIDENCE_VERSION
_FLOW_OUTLINE_VERSION = "flow-outline-v1"
_CALL_PATTERN = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\(")
_STATE_PATTERN = re.compile(
    r"\b([A-Za-z_][A-Za-z0-9_]*(?:state|session|conn|ctrlr|qpair|request|task|ctx)[A-Za-z0-9_]*)\b",
    re.IGNORECASE,
)
_CONTROL_WORDS = {
    "if",
    "for",
    "while",
    "switch",
    "return",
    "sizeof",
    "defined",
    "offsetof",
    "assert",
}
_FUNCTION_DEFINITION_PATTERN = re.compile(
    r"^\s*(?:[A-Za-z_][A-Za-z0-9_]*[\s*]+)+"
    r"([A-Za-z_][A-Za-z0-9_]*)\s*\([^;{}]*\)\s*\{"
)


def build_flow_evidence_pack(
    source_pack: dict[str, Any],
    *,
    repo_path: str = "",
    max_files: int = 12,
) -> dict[str, Any]:
    """Build a bounded, model-independent call/state/test evidence pack."""
    cards = [
        item
        for item in source_pack.get("evidence_cards") or []
        if isinstance(item, dict)
    ][: max(1, max_files)]
    entry_points: list[dict[str, Any]] = []
    call_edges: list[dict[str, Any]] = []
    state_objects: list[dict[str, Any]] = []
    state_transitions: list[dict[str, Any]] = []
    conditions: list[dict[str, Any]] = []
    error_paths: list[dict[str, Any]] = []
    cleanup_paths: list[dict[str, Any]] = []
    recovery_paths: list[dict[str, Any]] = []
    related_tests: list[dict[str, Any]] = []
    seen_states: set[tuple[str, str]] = set()
    edge_keys: set[tuple[str, str, str, int]] = set()
    revision_verified, actual_revision = _verify_repo_revision(
        repo_path=repo_path,
        expected_revision=str(source_pack.get("repo_revision") or ""),
    )
    provider_status = _provider_status(
        source_pack=source_pack,
        revision_verified=revision_verified,
        actual_revision=actual_revision,
    )

    for card_index, card in enumerate(cards, 1):
        file_path = str(card.get("file_path") or "")
        start_line = max(1, int(card.get("start_line") or 1))
        end_line = max(start_line, int(card.get("end_line") or start_line))
        symbols = [str(value) for value in card.get("symbols") or [] if str(value)]
        excerpt = str(card.get("excerpt") or "")
        provider = _flow_provider(card, repo_path=repo_path)
        evidence_sha256 = str(card.get("sha256") or "")
        excerpt_lines = excerpt.splitlines()
        code_lines = _sanitize_c_like_lines(excerpt_lines)
        definitions = _excerpt_function_definitions(code_lines, symbols)
        entry_symbols = _dedupe(definitions.values())
        for symbol_index, symbol in enumerate(entry_symbols[:8], 1):
            entry_points.append(
                _evidence_node(
                    evidence_id=f"FLOW-ENTRY-{card_index:03d}-{symbol_index:02d}",
                    file_path=file_path,
                    symbol=symbol,
                    start_line=start_line,
                    end_line=end_line,
                    provider=provider,
                    sha256=evidence_sha256,
                    details={"classification": str(card.get("classification") or "source")},
                )
            )
        current_symbol = ""
        for line_offset, line in enumerate(code_lines):
            if line_offset in definitions:
                current_symbol = definitions[line_offset]
            absolute_line = min(end_line, start_line + line_offset)
            stripped = line.strip()
            declaration_symbol = _function_declaration_start_symbol(line)
            for called in _CALL_PATTERN.findall(line):
                if (
                    called in _CONTROL_WORDS
                    or called == current_symbol
                    or called == declaration_symbol
                    or called.isupper()
                    or not current_symbol
                ):
                    continue
                key = (file_path, current_symbol, called, absolute_line)
                if key in edge_keys:
                    continue
                edge_keys.add(key)
                call_edges.append(
                    _evidence_node(
                        evidence_id=f"FLOW-EDGE-{len(call_edges) + 1:03d}",
                        file_path=file_path,
                        symbol=current_symbol,
                        start_line=absolute_line,
                        end_line=absolute_line,
                        provider=provider,
                        sha256=evidence_sha256,
                        details={"from_symbol": current_symbol, "to_symbol": called},
                    )
                )
            for state_name in _STATE_PATTERN.findall(line):
                state_key = (file_path, state_name)
                if state_key in seen_states:
                    continue
                seen_states.add(state_key)
                state_objects.append(
                    _evidence_node(
                        evidence_id=f"FLOW-STATE-{len(state_objects) + 1:03d}",
                        file_path=file_path,
                        symbol=state_name,
                        start_line=absolute_line,
                        end_line=absolute_line,
                        provider=provider,
                        sha256=evidence_sha256,
                    )
                )
            lower = stripped.lower()
            if re.search(r"\b(if|switch|case)\b", lower):
                conditions.append(
                    _line_evidence(
                        prefix="FLOW-COND",
                        items=conditions,
                        card=card,
                        symbol=current_symbol,
                        line=absolute_line,
                        text=stripped,
                        provider=provider,
                    )
                )
            if re.search(r"\b(error|fail|timeout|invalid|denied|reject)\b", lower):
                error_paths.append(
                    _line_evidence(
                        prefix="FLOW-ERROR",
                        items=error_paths,
                        card=card,
                        symbol=current_symbol,
                        line=absolute_line,
                        text=stripped,
                        provider=provider,
                    )
                )
            if re.search(r"\b(free|destroy|cleanup|close|release|put|fini)\b", lower):
                cleanup_paths.append(
                    _line_evidence(
                        prefix="FLOW-CLEANUP",
                        items=cleanup_paths,
                        card=card,
                        symbol=current_symbol,
                        line=absolute_line,
                        text=stripped,
                        provider=provider,
                    )
                )
            if re.search(r"\b(retry|reconnect|recover|reset|resume|restart)\b", lower):
                recovery_paths.append(
                    _line_evidence(
                        prefix="FLOW-RECOVERY",
                        items=recovery_paths,
                        card=card,
                        symbol=current_symbol,
                        line=absolute_line,
                        text=stripped,
                        provider=provider,
                    )
                )
            if re.search(r"\b(state|status)\b.*=|set_[a-z0-9_]*state", lower):
                state_transitions.append(
                    _line_evidence(
                        prefix="FLOW-TRANSITION",
                        items=state_transitions,
                        card=card,
                        symbol=current_symbol,
                        line=absolute_line,
                        text=stripped,
                        provider=provider,
                    )
                )
        if str(card.get("classification") or "") == "test":
            related_tests.append(
                _evidence_node(
                    evidence_id=f"FLOW-TEST-{len(related_tests) + 1:03d}",
                    file_path=file_path,
                    symbol=entry_symbols[0] if entry_symbols else (symbols[0] if symbols else ""),
                    start_line=start_line,
                    end_line=end_line,
                    provider=provider,
                    sha256=evidence_sha256,
                    details={"matched_terms": list(card.get("matched_terms") or [])[:8]},
                )
            )

    if revision_verified:
        discovered = _discover_with_git_grep(
            source_pack=source_pack,
            repo_path=Path(repo_path),
            revision=actual_revision,
            max_symbols=12,
            max_matches=80,
        )
        for edge in discovered["call_edges"]:
            key = (
                str(edge.get("file_path") or ""),
                str(edge.get("from_symbol") or ""),
                str(edge.get("to_symbol") or ""),
                int(edge.get("start_line") or 0),
            )
            if key not in edge_keys:
                edge_keys.add(key)
                edge["evidence_id"] = f"FLOW-EDGE-{len(call_edges) + 1:03d}"
                call_edges.append(edge)
        existing_tests = {
            (str(item.get("file_path") or ""), int(item.get("start_line") or 0))
            for item in related_tests
        }
        for item in discovered["related_tests"]:
            key = (str(item.get("file_path") or ""), int(item.get("start_line") or 0))
            if key in existing_tests:
                continue
            existing_tests.add(key)
            item["evidence_id"] = f"FLOW-TEST-{len(related_tests) + 1:03d}"
            related_tests.append(item)

    gaps = [str(value) for value in (source_pack.get("source_scope") or {}).get("evidence_gaps") or []]
    if not entry_points:
        gaps.append("没有提取到已验证入口符号")
    if not call_edges:
        gaps.append("现有有界证据未包含可验证调用边")
    if not related_tests:
        gaps.append("现有证据未包含相关测试目录引用")
    if repo_path and not revision_verified:
        gaps.append("仓库 revision 校验失败，已禁止使用工作区内容扩展调用链证据")
    return {
        "version": _FLOW_EVIDENCE_VERSION,
        "analysis_target": str(source_pack.get("analysis_target") or ""),
        "repo_revision": str(source_pack.get("repo_revision") or ""),
        "repo_revision_verified": revision_verified,
        "actual_repo_revision": actual_revision,
        "source_pack_sha256": stable_payload_sha256(source_pack),
        "discovery_order": ["gitnexus", "cgc", "git-grep", "bounded-local-excerpts"],
        "provider_status": provider_status,
        "entry_points": entry_points[:24],
        "call_edges": call_edges[:80],
        "state_objects": state_objects[:40],
        "state_transitions": state_transitions[:40],
        "conditions": conditions[:40],
        "error_paths": error_paths[:40],
        "cleanup_paths": cleanup_paths[:40],
        "recovery_paths": recovery_paths[:40],
        "related_tests": related_tests[:24],
        "evidence_gaps": _dedupe(gaps),
    }


def build_flow_outline(flow_pack: dict[str, Any]) -> dict[str, Any]:
    edges = [item for item in flow_pack.get("call_edges") or [] if isinstance(item, dict)]
    entries = [item for item in flow_pack.get("entry_points") or [] if isinstance(item, dict)]
    usable_edges: list[dict[str, Any]] = []
    edge_keys: set[tuple[str, str]] = set()
    for edge in edges:
        caller = str(edge.get("from_symbol") or edge.get("symbol") or "")
        callee = str(edge.get("to_symbol") or "")
        key = (caller, callee)
        if not caller or not callee or caller == callee or key in edge_keys:
            continue
        edge_keys.add(key)
        usable_edges.append(edge)
        if len(usable_edges) >= 80:
            break
    from_symbols = _dedupe(
        str(edge.get("from_symbol") or edge.get("symbol") or "")
        for edge in usable_edges
    )
    entry_symbols = _dedupe(
        str(entry.get("symbol") or "")
        for entry in entries
        if str(entry.get("symbol") or "")
    )
    entry_roots = [symbol for symbol in entry_symbols if symbol in from_symbols]
    roots = entry_roots
    used_edges: set[int] = set()
    main_flows: list[dict[str, Any]] = []
    steps: list[dict[str, Any]] = []
    for root in roots:
        queue = [root]
        component: list[dict[str, Any]] = []
        while queue and len(component) < 32:
            caller = queue.pop(0)
            for edge_index, edge in enumerate(usable_edges):
                if edge_index in used_edges:
                    continue
                from_symbol = str(edge.get("from_symbol") or edge.get("symbol") or "")
                if from_symbol != caller:
                    continue
                used_edges.add(edge_index)
                component.append(edge)
                callee = str(edge.get("to_symbol") or "")
                if callee in from_symbols and callee not in queue:
                    queue.append(callee)
        if not component:
            continue
        flow_id = f"main-flow-{len(main_flows) + 1:02d}"
        flow_steps = [
            {
                "step": index,
                "flow_id": flow_id,
                "action": f"{edge.get('from_symbol') or edge.get('symbol')} 调用 {edge.get('to_symbol')}",
                "from_symbol": str(edge.get("from_symbol") or edge.get("symbol") or ""),
                "to_symbol": str(edge.get("to_symbol") or ""),
                "evidence_ids": [str(edge.get("evidence_id") or "")],
            }
            for index, edge in enumerate(component, 1)
        ]
        steps.extend(flow_steps)
        main_flows.append(
            {
                "id": flow_id,
                "name": f"从 {root} 出发的已验证连通流程",
                "root_symbol": root,
                "steps": flow_steps,
            }
        )
    entry_evidence = {
        str(entry.get("symbol") or ""): str(entry.get("evidence_id") or "")
        for entry in entries
    }
    represented_roots = {str(item.get("root_symbol") or "") for item in main_flows}
    for entry_symbol in entry_symbols[:12]:
        if entry_symbol in represented_roots:
            continue
        flow_id = f"entry-only-{len(main_flows) + 1:02d}"
        flow_steps = [
            {
                "step": 1,
                "flow_id": flow_id,
                "action": f"进入 {entry_symbol}",
                "from_symbol": "external_caller",
                "to_symbol": entry_symbol,
                "evidence_ids": [entry_evidence.get(entry_symbol, "")],
            }
        ]
        steps.extend(flow_steps)
        main_flows.append(
            {
                "id": flow_id,
                "name": f"入口 {entry_symbol}（后续调用证据待补充）",
                "root_symbol": entry_symbol,
                "steps": flow_steps,
            }
        )
    if not steps:
        for index, entry in enumerate(entries[:12], 1):
            steps.append(
                {
                    "step": index,
                    "flow_id": "entry-only",
                    "action": f"进入 {entry.get('symbol') or entry.get('file_path')}",
                    "from_symbol": "external_caller",
                    "to_symbol": str(entry.get("symbol") or ""),
                    "evidence_ids": [str(entry.get("evidence_id") or "")],
                }
            )
    outline_gaps = list(flow_pack.get("evidence_gaps") or [])
    excluded_edges = len(usable_edges) - len(used_edges)
    if entry_roots and excluded_edges:
        outline_gaps.append(
            f"排除 {excluded_edges} 条不属于已验证入口可达分量的调用边"
        )
    if entries and not entry_roots and usable_edges:
        outline_gaps.append(
            "已验证入口缺少可达调用边，未将其他任意调用根提升为主流程"
        )
    if len(main_flows) > 1:
        outline_gaps.append(
            f"当前证据形成 {len(main_flows)} 个互不连通的调用分量，不能证明单一端到端业务顺序"
        )
    if usable_edges and not main_flows:
        outline_gaps.append("调用边缺少可验证连通根，未将发现顺序解释为业务顺序")
    selected_edges = [edge for index, edge in enumerate(usable_edges) if index in used_edges]
    all_evidence_ids = _dedupe(
        [
            *(str(item.get("evidence_id") or "") for item in entries),
            *(str(item.get("evidence_id") or "") for item in selected_edges),
        ]
    )
    return {
        "version": _FLOW_OUTLINE_VERSION,
        "analysis_target": str(flow_pack.get("analysis_target") or ""),
        "repo_revision": str(flow_pack.get("repo_revision") or ""),
        "actors": ["external_caller", "target_entry", "state_backend"],
        "entry_points": entries,
        "main_flows": main_flows,
        "steps": steps,
        "branches": list(flow_pack.get("conditions") or []),
        "error_flows": list(flow_pack.get("error_paths") or []),
        "cleanup_flows": list(flow_pack.get("cleanup_paths") or []),
        "recovery_flows": list(flow_pack.get("recovery_paths") or []),
        "state_objects": list(flow_pack.get("state_objects") or []),
        "state_transitions": list(flow_pack.get("state_transitions") or []),
        "related_tests": list(flow_pack.get("related_tests") or []),
        "evidence_ids": all_evidence_ids,
        "evidence_gaps": _dedupe(outline_gaps),
    }


def build_business_flow_context(
    *,
    plan: dict[str, Any],
    source_pack: dict[str, Any],
    flow_pack: dict[str, Any],
    outline: dict[str, Any],
) -> dict[str, Any]:
    cards = []
    referenced = set(str(value) for value in outline.get("evidence_ids") or [])
    source_cards = [
        card
        for card in source_pack.get("evidence_cards") or []
        if isinstance(card, dict)
    ]
    selected_cards = source_cards[:6]
    if not any(str(card.get("classification") or "") == "test" for card in selected_cards):
        test_card = next(
            (
                card
                for card in source_cards[6:]
                if str(card.get("classification") or "") == "test"
            ),
            None,
        )
        if test_card is not None:
            selected_cards = [*selected_cards[:5], test_card]
    for card in selected_cards:
        if not isinstance(card, dict):
            continue
        cards.append(
            {
                "evidence_id": str(card.get("evidence_id") or ""),
                "file_path": str(card.get("file_path") or ""),
                "classification": str(card.get("classification") or ""),
                "start_line": int(card.get("start_line") or 0),
                "end_line": int(card.get("end_line") or 0),
                "symbols": list(card.get("symbols") or [])[:6],
                "excerpt": str(card.get("excerpt") or "")[:400],
                "sha256": str(card.get("sha256") or ""),
            }
        )
    selected_flow_nodes = lambda key: [
        item
        for item in flow_pack.get(key) or []
        if isinstance(item, dict) and str(item.get("evidence_id") or "") in referenced
    ]
    compact_flow_pack = {
        "provider_status": list(flow_pack.get("provider_status") or [])[:4],
        "entry_points": _compact_flow_nodes(selected_flow_nodes("entry_points"), 4),
        "call_edges": _compact_flow_nodes(selected_flow_nodes("call_edges"), 8),
        "state_objects": _compact_flow_nodes(flow_pack.get("state_objects"), 3),
        "state_transitions": _compact_flow_nodes(flow_pack.get("state_transitions"), 3),
        "conditions": _compact_flow_nodes(flow_pack.get("conditions"), 2),
        "error_paths": _compact_flow_nodes(flow_pack.get("error_paths"), 2),
        "cleanup_paths": _compact_flow_nodes(flow_pack.get("cleanup_paths"), 2),
        "recovery_paths": _compact_flow_nodes(flow_pack.get("recovery_paths"), 2),
        "related_tests": _compact_flow_nodes(flow_pack.get("related_tests"), 3),
        "evidence_gaps": _compact_strings(flow_pack.get("evidence_gaps"), 6, 240),
    }
    compact_outline = {
        "actors": _compact_strings(outline.get("actors"), 8, 120),
        "entry_evidence_ids": [
            str(item.get("evidence_id") or "")
            for item in outline.get("entry_points") or []
            if isinstance(item, dict) and str(item.get("evidence_id") or "")
        ][:8],
        "main_flows": [
            {
                "id": str(item.get("id") or ""),
                "name": str(item.get("name") or "")[:600],
            }
            for item in outline.get("main_flows") or []
            if isinstance(item, dict)
        ][:4],
        "steps": _compact_outline_items(outline.get("steps"), 12),
        "branches": _compact_outline_items(outline.get("branches"), 3),
        "error_flows": _compact_outline_items(outline.get("error_flows"), 3),
        "cleanup_flows": _compact_outline_items(outline.get("cleanup_flows"), 3),
        "recovery_flows": _compact_outline_items(outline.get("recovery_flows"), 3),
        "state_evidence_ids": [
            str(item.get("evidence_id") or "")
            for item in outline.get("state_objects") or []
            if isinstance(item, dict) and str(item.get("evidence_id") or "")
        ][:8],
        "evidence_gaps": _compact_strings(outline.get("evidence_gaps"), 6, 240),
    }
    return {
        "version": "business-flow-context-v2",
        "analysis_target": str(plan.get("original_user_request") or plan.get("target") or "")[:1600],
        "repo_revision": str(source_pack.get("repo_revision") or ""),
        "flow_evidence_pack": compact_flow_pack,
        "flow_outline": compact_outline,
        "verified_evidence_cards": cards,
        "input_materials": _compact_input_materials(source_pack.get("input_materials")),
        "required_evidence_ids": sorted(referenced)[:48],
        "output_constraint": "仅补充公开业务流程叙述，不得生成 SFMEA 或测试用例",
    }


def _compact_flow_nodes(values: Any, limit: int) -> list[dict[str, Any]]:
    keys = (
        "evidence_id",
        "file_path",
        "symbol",
        "start_line",
        "end_line",
        "provider",
        "classification",
        "from_symbol",
        "to_symbol",
    )
    compact = []
    for item in values or []:
        if not isinstance(item, dict):
            continue
        node = {key: item[key] for key in keys if item.get(key) not in (None, "", [])}
        if item.get("text"):
            node["text"] = str(item["text"])[:160]
        if item.get("matched_terms"):
            node["matched_terms"] = list(item["matched_terms"])[:6]
        compact.append(node)
        if len(compact) >= limit:
            break
    return compact


def _compact_outline_items(values: Any, limit: int) -> list[dict[str, Any]]:
    keys = (
        "step",
        "action",
        "evidence_id",
        "evidence_ids",
        "from_symbol",
        "to_symbol",
        "symbol",
        "file_path",
    )
    compact = []
    for item in values or []:
        if not isinstance(item, dict):
            continue
        value = {key: item[key] for key in keys if item.get(key) not in (None, "", [])}
        if item.get("text"):
            value["text"] = str(item["text"])[:120]
        compact.append(value)
        if len(compact) >= limit:
            break
    return compact


def _compact_strings(values: Any, limit: int, characters: int) -> list[str]:
    return [str(value)[:characters] for value in (values or []) if str(value).strip()][:limit]


def _compact_input_materials(values: Any) -> list[Any]:
    compact: list[Any] = []
    for item in values or []:
        if isinstance(item, dict):
            compact.append(
                {
                    str(key): str(value)[:600]
                    for key, value in item.items()
                    if key in {"name", "label", "type", "summary", "sha256"}
                    and value not in (None, "", [])
                }
            )
        else:
            compact.append(str(item)[:600])
        if len(compact) >= 4:
            break
    return compact


def render_business_flow_markdown(outline: dict[str, Any]) -> str:
    lines = [
        "# 关键业务流程分析",
        "",
        f"- 分析目标：{outline.get('analysis_target') or '(未提供)'}",
        f"- Repo revision：{outline.get('repo_revision') or '(未记录)'}",
        "- 生成方式：确定性 Flow Outline renderer；模型叙述仅作为可选补充。",
        "",
        "## 外部触发",
        "",
    ]
    for entry in outline.get("entry_points") or []:
        if not isinstance(entry, dict):
            continue
        lines.append(
            f"- 外部调用进入 `{entry.get('symbol') or entry.get('file_path')}` "
            f"（`{entry.get('evidence_id')}`）。"
        )
    if not outline.get("entry_points"):
        lines.append("- 当前证据未确认外部入口，需补充入口证据。")
    lines.extend([
        "",
        "## 流程序列图",
        "",
        "```mermaid",
        "sequenceDiagram",
        "    participant Caller as 外部调用方",
        "    participant Target as 目标入口",
        "    Caller->>Target: 触发目标流程",
    ])
    for step in outline.get("steps") or []:
        if not isinstance(step, dict):
            continue
        action = _mermaid_text(str(step.get("action") or "执行流程步骤"))
        lines.append(f"    Target->>Target: {action}")
    lines.extend(["```", "", "## 流程步骤（主流程表）", "", "| 步骤 | 行为 | 证据 |", "|---:|---|---|"])
    for step in outline.get("steps") or []:
        if not isinstance(step, dict):
            continue
        evidence = ", ".join(str(value) for value in step.get("evidence_ids") or [])
        lines.append(f"| {step.get('step')} | {step.get('action')} | `{evidence}` |")
    if not outline.get("steps"):
        lines.append("| 1 | 当前证据不足以构造调用步骤 | `evidence_gap` |")
    lines.extend(["", "## 异常分支", "", "| 类型 | 位置/条件 | 证据 |", "|---|---|---|"])
    for label, key in (("异常", "error_flows"), ("清理", "cleanup_flows"), ("恢复", "recovery_flows")):
        for item in outline.get(key) or []:
            if not isinstance(item, dict):
                continue
            lines.append(
                f"| {label} | {item.get('text') or item.get('symbol') or '(未记录)'} | "
                f"`{item.get('evidence_id')}` |"
            )
    if not any(outline.get(key) for key in ("error_flows", "cleanup_flows", "recovery_flows")):
        lines.append("| 缺口 | 当前证据未覆盖异常、清理或恢复路径 | `evidence_gap` |")
    lines.extend(["", "## 状态生命周期", "", "| 状态对象 | 转换证据 |", "|---|---|"])
    transitions = list(outline.get("state_transitions") or [])
    for item in outline.get("state_objects") or []:
        if not isinstance(item, dict):
            continue
        related = [
            str(value.get("evidence_id") or "")
            for value in transitions
            if isinstance(value, dict) and value.get("file_path") == item.get("file_path")
        ]
        lines.append(f"| `{item.get('symbol')}` | `{', '.join(related) or item.get('evidence_id')}` |")
    if not outline.get("state_objects"):
        lines.append("| 未识别 | 当前有界证据未暴露状态对象 |")
    lines.extend(["", "## 观测点与证据引用"])
    for item in outline.get("entry_points") or []:
        if not isinstance(item, dict):
            continue
        lines.append(
            f"- `{item.get('evidence_id')}` `{item.get('file_path')}:{item.get('start_line')}-{item.get('end_line')}` "
            f"symbol=`{item.get('symbol')}` provider=`{item.get('provider')}`"
        )
    lines.extend(["", "## 证据缺口"])
    gaps = [str(value) for value in outline.get("evidence_gaps") or []]
    lines.extend(f"- {gap}" for gap in gaps)
    if not gaps:
        lines.append("- 未发现结构性证据缺口；仍需通过实机测试确认运行时行为。")
    return "\n".join(lines).rstrip() + "\n"


def stable_payload_sha256(payload: Any) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _evidence_node(
    *,
    evidence_id: str,
    file_path: str,
    symbol: str,
    start_line: int,
    end_line: int,
    provider: str,
    sha256: str = "",
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "evidence_id": evidence_id,
        "file_path": file_path,
        "symbol": symbol,
        "start_line": start_line,
        "end_line": end_line,
        "provider": provider,
        "sha256": sha256,
        **(details or {}),
    }


def _line_evidence(
    *,
    prefix: str,
    items: list[dict[str, Any]],
    card: dict[str, Any],
    symbol: str,
    line: int,
    text: str,
    provider: str,
) -> dict[str, Any]:
    return _evidence_node(
        evidence_id=f"{prefix}-{len(items) + 1:03d}",
        file_path=str(card.get("file_path") or ""),
        symbol=symbol,
        start_line=line,
        end_line=line,
        provider=provider,
        sha256=str(card.get("sha256") or ""),
        details={"text": text[:500]},
    )


def _verify_repo_revision(*, repo_path: str, expected_revision: str) -> tuple[bool, str]:
    root = Path(repo_path)
    if not expected_revision or not root.is_dir():
        return False, ""
    try:
        actual = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
            timeout=3,
        ).stdout.strip()
        expected = subprocess.run(
            ["git", "rev-parse", f"{expected_revision}^{{commit}}"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
            timeout=3,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return False, ""
    return bool(actual and expected and actual == expected), actual


def _provider_status(
    *,
    source_pack: dict[str, Any],
    revision_verified: bool,
    actual_revision: str,
) -> list[dict[str, Any]]:
    summaries = source_pack.get("tool_summaries") or {}
    return [
        {
            "provider": "gitnexus",
            "available": bool(str(summaries.get("gitnexus") or "").strip()),
            "mode": "prefetched-summary",
        },
        {
            "provider": "cgc",
            "available": bool(str(summaries.get("cgc") or "").strip()),
            "mode": "prefetched-summary",
        },
        {
            "provider": "git-grep",
            "available": revision_verified,
            "mode": "revision-pinned-bounded-search",
            "repo_revision": actual_revision,
        },
    ]


def _discover_with_git_grep(
    *,
    source_pack: dict[str, Any],
    repo_path: Path,
    revision: str,
    max_symbols: int,
    max_matches: int,
) -> dict[str, list[dict[str, Any]]]:
    cards = [
        item for item in source_pack.get("evidence_cards") or [] if isinstance(item, dict)
    ]
    declared_symbols: list[str] = []
    called_symbols: list[str] = []
    roots: list[str] = []
    for card in cards:
        declared_symbols.extend(
            str(value)
            for value in card.get("symbols") or []
            if str(value) not in _CONTROL_WORDS and not str(value).isupper()
        )
        sanitized_excerpt = "\n".join(
            _sanitize_c_like_lines(str(card.get("excerpt") or "").splitlines())
        )
        for called in _CALL_PATTERN.findall(sanitized_excerpt):
            if called not in _CONTROL_WORDS and not called.isupper():
                called_symbols.append(called)
        root = _search_root(str(card.get("file_path") or ""))
        if root:
            roots.append(root)
    scope = source_pack.get("source_scope") or {}
    for file_path in [*(scope.get("source_files") or []), *(scope.get("test_files") or [])]:
        root = _search_root(str(file_path))
        if root:
            roots.append(root)
    symbols = _dedupe([*declared_symbols, *called_symbols])[: max(1, max_symbols)]
    roots = _dedupe(roots)[:12]
    if not symbols or not roots:
        return {"call_edges": [], "related_tests": []}

    blob_cache: dict[str, tuple[list[str], str]] = {}
    sanitized_blob_cache: dict[str, list[str]] = {}
    edges: list[dict[str, Any]] = []
    tests: list[dict[str, Any]] = []
    deadline = time.monotonic() + 35.0
    for symbol in symbols:
        if len(edges) + len(tests) >= max_matches or time.monotonic() >= deadline:
            break
        command_timeout = max(0.1, min(3.0, deadline - time.monotonic()))
        try:
            result = subprocess.run(
                [
                    "git",
                    "grep",
                    "-n",
                    "--full-name",
                    "-F",
                    symbol,
                    revision,
                    "--",
                    *roots,
                ],
                cwd=repo_path,
                check=False,
                capture_output=True,
                text=True,
                timeout=command_timeout,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if result.returncode not in {0, 1}:
            continue
        for raw_line in result.stdout.splitlines():
            match = re.match(r"^[^:]+:(.*?):(\d+):(.*)$", raw_line)
            if not match:
                continue
            file_path, line_text = match.group(1), match.group(3)
            line_number = int(match.group(2))
            lines, file_sha256 = _git_blob(
                repo_path=repo_path,
                revision=revision,
                file_path=file_path,
                cache=blob_cache,
                deadline=deadline,
            )
            if not lines or not file_sha256:
                continue
            sanitized_lines = sanitized_blob_cache.get(file_path)
            if sanitized_lines is None:
                sanitized_lines = _sanitize_c_like_lines(lines)
                sanitized_blob_cache[file_path] = sanitized_lines
            sanitized_line = (
                sanitized_lines[line_number - 1]
                if 0 < line_number <= len(sanitized_lines)
                else ""
            )
            if not _line_has_symbol_call(sanitized_line, symbol):
                continue
            caller = _nearest_function_symbol(sanitized_lines, line_number)
            details = {
                "from_symbol": caller or "unknown_caller",
                "to_symbol": symbol,
                "matched_text": line_text.strip()[:300],
            }
            if _is_test_path(file_path):
                tests.append(
                    _evidence_node(
                        evidence_id="",
                        file_path=file_path,
                        symbol=caller or symbol,
                        start_line=line_number,
                        end_line=line_number,
                        provider="git-grep",
                        sha256=file_sha256,
                        details={"matched_symbol": symbol},
                    )
                )
            elif caller and caller != symbol:
                edges.append(
                    _evidence_node(
                        evidence_id="",
                        file_path=file_path,
                        symbol=caller,
                        start_line=line_number,
                        end_line=line_number,
                        provider="git-grep",
                        sha256=file_sha256,
                        details=details,
                    )
                )
            if len(edges) + len(tests) >= max_matches:
                break
    return {
        "call_edges": _dedupe_evidence(edges, ("file_path", "start_line", "to_symbol")),
        "related_tests": _dedupe_evidence(tests, ("file_path", "start_line", "matched_symbol")),
    }


def _line_has_symbol_call(line_text: str, symbol: str) -> bool:
    return re.search(rf"\b{re.escape(symbol)}\s*\(", line_text) is not None


def _sanitize_c_like_lines(lines: list[str]) -> list[str]:
    """Remove comments and literals while preserving line and column positions."""
    output: list[str] = []
    in_block_comment = False
    in_string = False
    quote = ""
    escaped = False
    for line in lines:
        chars = list(line)
        clean = [" "] * len(chars)
        index = 0
        while index < len(chars):
            char = chars[index]
            following = chars[index + 1] if index + 1 < len(chars) else ""
            if in_block_comment:
                if char == "*" and following == "/":
                    in_block_comment = False
                    index += 2
                else:
                    index += 1
                continue
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == quote:
                    in_string = False
                    quote = ""
                index += 1
                continue
            if char == "/" and following == "/":
                break
            if char == "/" and following == "*":
                in_block_comment = True
                index += 2
                continue
            if char in {'"', "'"}:
                in_string = True
                quote = char
                escaped = False
                index += 1
                continue
            clean[index] = char
            index += 1
        output.append("".join(clean))
    return output


def _git_blob(
    *,
    repo_path: Path,
    revision: str,
    file_path: str,
    cache: dict[str, tuple[list[str], str]],
    deadline: float,
) -> tuple[list[str], str]:
    if file_path in cache:
        return cache[file_path]
    if time.monotonic() >= deadline:
        return [], ""
    try:
        content = subprocess.run(
            ["git", "show", f"{revision}:{file_path}"],
            cwd=repo_path,
            check=True,
            capture_output=True,
            timeout=max(0.1, min(3.0, deadline - time.monotonic())),
        ).stdout
    except (OSError, subprocess.SubprocessError):
        cache[file_path] = ([], "")
        return cache[file_path]
    if len(content) > 2_000_000:
        content = content[:2_000_000]
    text = content.decode("utf-8", errors="replace")
    cache[file_path] = (text.splitlines(), hashlib.sha256(content).hexdigest())
    return cache[file_path]


def _nearest_function_symbol(lines: list[str], line_number: int) -> str:
    start = min(max(0, line_number - 1), len(lines) - 1)
    for index in range(start, max(-1, start - 80), -1):
        symbol = _definition_symbol("\n".join(lines[index : index + 16]))
        if symbol:
            return symbol
    return ""


def _definition_symbol(line: str) -> str:
    match = _FUNCTION_DEFINITION_PATTERN.search(line)
    return match.group(1) if match else ""


def _function_declaration_start_symbol(line: str) -> str:
    """Recognize a column-zero C declaration even when an excerpt truncates it."""
    if not line or line[0].isspace() or "(" not in line:
        return ""
    prefix = line.split("(", 1)[0]
    if "=" in prefix:
        return ""
    identifiers = re.findall(r"[A-Za-z_][A-Za-z0-9_]*", prefix)
    if len(identifiers) < 2 or identifiers[0] in _CONTROL_WORDS:
        return ""
    return identifiers[-1]


def _excerpt_function_definitions(
    lines: list[str], symbols: list[str]
) -> dict[int, str]:
    definitions: dict[int, str] = {}
    for index, line in enumerate(lines):
        window = "\n".join(lines[index : index + 5])
        direct = _definition_symbol(window)
        if direct:
            definitions[index] = direct
            continue
        for symbol in symbols:
            match = re.match(
                rf"^\s*{re.escape(symbol)}\s*\([^;{{}}]*\)\s*\{{",
                window,
            )
            if not match:
                continue
            definitions[index] = symbol
            break
    return definitions


def _search_root(file_path: str) -> str:
    path = Path(file_path)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        return ""
    if len(path.parts) >= 3:
        return "/".join(path.parts[:2])
    return path.parts[0]


def _is_test_path(file_path: str) -> bool:
    parts = {part.lower() for part in Path(file_path).parts}
    return bool(parts.intersection({"test", "tests", "unittest", "unit_tests"}))


def _dedupe_evidence(
    values: list[dict[str, Any]], keys: tuple[str, ...]
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[tuple[str, ...]] = set()
    for value in values:
        key = tuple(str(value.get(item) or "") for item in keys)
        if key in seen:
            continue
        seen.add(key)
        result.append(value)
    return result


def _flow_provider(card: dict[str, Any], *, repo_path: str) -> str:
    source = str(card.get("source") or "").lower()
    if "gitnexus" in source:
        return "gitnexus"
    if "cgc" in source or "joern" in source:
        return "cgc"
    return "source-evidence-pack"


def _dedupe(values: Any) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _mermaid_text(value: str) -> str:
    return value.replace("\n", " ").replace(":", "：").replace(";", "；")[:160]
