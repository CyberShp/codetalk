from __future__ import annotations

import hashlib
import json
import re
import subprocess
import time
from collections import deque
from pathlib import Path
from typing import Any


# Bump when deterministic extraction semantics change so cached packs cannot
# hide newly recognized source evidence from downstream flow quality gates.
FLOW_EVIDENCE_VERSION = "flow-evidence-pack-v5"
_FLOW_EVIDENCE_VERSION = FLOW_EVIDENCE_VERSION
FLOW_OUTLINE_VERSION = "flow-outline-v3"
_FLOW_OUTLINE_VERSION = FLOW_OUTLINE_VERSION
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
_ERROR_GUARD_PATTERN = re.compile(
    r"\bif\s*\([^\n)]*(?:<\s*0|!=\s*0|==\s*NULL|!\s*[A-Za-z_])",
    re.IGNORECASE,
)
_ERROR_RETURN_PATTERN = re.compile(
    r"\breturn\s+[^;]*(?:ERROR|FAIL|TIMEOUT|INVALID|DENIED|REJECT)",
    re.IGNORECASE,
)
_ERROR_STATUS_PATTERN = re.compile(
    r"\b(?:status_(?:class|detail)|rc)\s*=\s*[^;]*(?:ERROR|FAIL|TIMEOUT|INVALID|DENIED|REJECT)",
    re.IGNORECASE,
)
_ERROR_CALLEE_PATTERN = re.compile(
    r"\b[A-Za-z_][A-Za-z0-9_]*(?:_err|_error|_fail)[A-Za-z0-9_]*\s*\(",
    re.IGNORECASE,
)
_CALLBACK_ARGUMENT_PATTERN = re.compile(
    r"\b([A-Za-z_][A-Za-z0-9_]*(?:complete|callback|handler|_cb))\b",
    re.IGNORECASE,
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
            if _is_executable_error_path(
                stripped,
                declaration_symbol=declaration_symbol,
            ):
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
            # Leave bounded capacity for reverse-call expansion from verified
            # callbacks to the ingress that reaches them.
            max_symbols=32,
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
    # Test helpers often reuse product symbol names for fuzzing or fixtures.
    # They remain related-test evidence, but joining them to product nodes by
    # bare symbol would fabricate an end-to-end runtime path.
    edges = [
        item
        for item in flow_pack.get("call_edges") or []
        if isinstance(item, dict) and not _is_test_path(str(item.get("file_path") or ""))
    ]
    entries = [
        item
        for item in flow_pack.get("entry_points") or []
        if isinstance(item, dict) and not _is_test_path(str(item.get("file_path") or ""))
    ]
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
    # Evidence cards often anchor a callback or terminal handler rather than
    # the function that receives the external event.  Follow only verified
    # reverse edges to pick the ingress of that same connected component.
    incoming_edges: dict[str, list[dict[str, Any]]] = {}
    for edge in usable_edges:
        target = str(edge.get("to_symbol") or "")
        if target:
            incoming_edges.setdefault(target, []).append(edge)

    def verified_ingress(symbol: str) -> str:
        current = symbol
        visited = {current}
        while True:
            candidates = sorted(
                incoming_edges.get(current) or [],
                key=lambda item: (
                    str(item.get("from_symbol") or ""),
                    str(item.get("file_path") or ""),
                    int(item.get("start_line") or 0),
                ),
            )
            if not candidates:
                return current
            previous = str(candidates[0].get("from_symbol") or "")
            if not previous or previous in visited:
                return current
            visited.add(previous)
            current = previous

    entry_roots = _dedupe(verified_ingress(symbol) for symbol in entry_symbols)
    roots = entry_roots
    used_edges: set[int] = set()
    main_flows: list[dict[str, Any]] = []
    steps: list[dict[str, Any]] = []
    supporting_components: list[dict[str, Any]] = []
    primary_selection = _select_primary_normal_path(
        usable_edges=usable_edges,
        entry_roots=entry_roots,
        analysis_target=str(flow_pack.get("analysis_target") or ""),
    )
    primary_selection_kind = "normal_completion"
    if primary_selection is None:
        primary_selection = _select_primary_target_slice(
            usable_edges=usable_edges,
            entry_roots=entry_roots,
            analysis_target=str(flow_pack.get("analysis_target") or ""),
        )
        if primary_selection is not None:
            primary_selection_kind = "target_slice"
    if primary_selection:
        root, edge_indexes = primary_selection
        component = [usable_edges[index] for index in edge_indexes]
        used_edges.update(edge_indexes)
        flow_id = "main-flow-01"
        flow_steps = [
            {
                "step": index,
                "flow_id": flow_id,
                "action": (
                    f"{edge.get('from_symbol') or edge.get('symbol')} 传入回调 {edge.get('to_symbol')}"
                    if edge.get("relation") == "callback_reference"
                    else f"{edge.get('from_symbol') or edge.get('symbol')} 调用 {edge.get('to_symbol')}"
                ),
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
                "name": (
                    f"从 {root} 到正常完成的已验证主流程"
                    if primary_selection_kind == "normal_completion"
                    else f"从 {root} 到目标范围的已验证流程"
                ),
                "root_symbol": root,
                "scope": primary_selection_kind,
                "steps": flow_steps,
            }
        )
        primary_symbols = {
            root,
            *(str(edge.get("from_symbol") or edge.get("symbol") or "") for edge in component),
            *(str(edge.get("to_symbol") or "") for edge in component),
        }
        for supporting_root in entry_roots:
            if not supporting_root or supporting_root in primary_symbols:
                continue
            branch_edges = [
                edge
                for edge in usable_edges
                if str(edge.get("from_symbol") or edge.get("symbol") or "") == supporting_root
            ][:8]
            if branch_edges:
                supporting_components.append(
                    {
                        "root_symbol": supporting_root,
                        "edge_count": len(branch_edges),
                        "purpose": "已验证支撑分支；不作为正常主流程顺序",
                        "evidence_ids": _dedupe(
                            str(edge.get("evidence_id") or "") for edge in branch_edges
                        ),
                    }
                )
    for root in ([] if primary_selection else roots):
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
                "action": (
                    f"{edge.get('from_symbol') or edge.get('symbol')} 传入回调 {edge.get('to_symbol')}"
                    if edge.get("relation") == "callback_reference"
                    else f"{edge.get('from_symbol') or edge.get('symbol')} 调用 {edge.get('to_symbol')}"
                ),
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
    for entry_symbol in ([] if primary_selection else entry_symbols[:12]):
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
    scope_exclusions: list[dict[str, Any]] = []
    if entry_roots and excluded_edges:
        # These edges were deliberately excluded because they cannot be
        # reached from the verified entry. They are useful scope diagnostics,
        # but are not a missing fact in the selected business flow.
        scope_exclusions.append(
            {
                "kind": "unreachable_call_edges",
                "count": excluded_edges,
                "reason": "不属于已验证入口可达分量的调用边",
            }
        )
    entry_has_reachable_edge = any(root in from_symbols for root in entry_roots)
    if entries and usable_edges and not entry_has_reachable_edge:
        outline_gaps.append(
            "已验证入口缺少可达调用边，未将其他任意调用根提升为主流程"
        )
    if len(main_flows) > 1 and not primary_selection:
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
        "supporting_components": supporting_components,
        "steps": steps,
        "branches": list(flow_pack.get("conditions") or []),
        "error_flows": list(flow_pack.get("error_paths") or []),
        "cleanup_flows": list(flow_pack.get("cleanup_paths") or []),
        "recovery_flows": list(flow_pack.get("recovery_paths") or []),
        "state_objects": list(flow_pack.get("state_objects") or []),
        "state_transitions": list(flow_pack.get("state_transitions") or []),
        "related_tests": list(flow_pack.get("related_tests") or []),
        "evidence_ids": all_evidence_ids,
        "scope_exclusions": scope_exclusions,
        "evidence_gaps": _dedupe(outline_gaps),
    }


def _select_primary_normal_path(
    *,
    usable_edges: list[dict[str, Any]],
    entry_roots: list[str],
    analysis_target: str,
) -> tuple[str, list[int]] | None:
    """Return one source-supported normal path, never a stitched inference."""
    primary_terms = _primary_flow_terms(analysis_target)
    if not primary_terms:
        return None
    normal_terminal = re.compile(r"(?:success|complete|ready|finish)", re.IGNORECASE)
    error_terminal = re.compile(r"(?:err|error|fail|timeout|reject)", re.IGNORECASE)
    terminals = {
        str(edge.get("to_symbol") or "")
        for edge in usable_edges
        if str(edge.get("to_symbol") or "")
        and normal_terminal.search(str(edge.get("to_symbol") or ""))
        and not error_terminal.search(str(edge.get("to_symbol") or ""))
        and any(term in str(edge.get("to_symbol") or "").lower() for term in primary_terms)
    }
    if not terminals:
        return None
    outgoing: dict[str, list[tuple[int, dict[str, Any]]]] = {}
    for index, edge in enumerate(usable_edges):
        source = str(edge.get("from_symbol") or edge.get("symbol") or "")
        if source:
            outgoing.setdefault(source, []).append((index, edge))
    candidates: list[tuple[int, str, list[int]]] = []
    for root in entry_roots:
        if not root or error_terminal.search(root) or "fuzz" in root.lower():
            continue
        queue: deque[tuple[str, list[int]]] = deque([(root, [])])
        visited = {root}
        while queue:
            symbol, path = queue.popleft()
            if symbol in terminals and len(path) >= 3:
                # A longer source-supported path is more useful for a testing
                # flow than a late internal handler that happens to reach the
                # same normal completion in one hop.
                candidates.append((len(path), root, path))
                break
            if len(path) >= 16:
                continue
            for edge_index, edge in outgoing.get(symbol) or []:
                target = str(edge.get("to_symbol") or "")
                if not target or target in visited:
                    continue
                visited.add(target)
                queue.append((target, [*path, edge_index]))
    if not candidates:
        return None
    _, root, path = max(candidates, key=lambda item: (item[0], item[1]))
    return root, path


def _select_primary_target_slice(
    *,
    usable_edges: list[dict[str, Any]],
    entry_roots: list[str],
    analysis_target: str,
) -> tuple[str, list[int]] | None:
    """Choose a bounded verified slice when the user explicitly named a range.

    This is deliberately not a fallback for generic analysis: without an
    explicit range, a connected component must not be promoted to an
    end-to-end flow merely because it is long.  A selected slice is labelled
    separately so downstream reports cannot call it a normal completion path.
    """
    if not re.search(r"(?:\\bfrom\\b|\\bto\\b|从|到|流程|flow)", analysis_target, re.IGNORECASE):
        return None
    primary_terms = _primary_flow_terms(analysis_target)
    if not primary_terms:
        return None
    outgoing: dict[str, list[tuple[int, dict[str, Any]]]] = {}
    for index, edge in enumerate(usable_edges):
        source = str(edge.get("from_symbol") or edge.get("symbol") or "")
        if source:
            outgoing.setdefault(source, []).append((index, edge))
    candidates: list[tuple[int, int, str, list[int]]] = []
    for root in entry_roots:
        if not root or "fuzz" in root.lower():
            continue
        queue: deque[tuple[str, list[int]]] = deque([(root, [])])
        visited: set[tuple[str, tuple[int, ...]]] = {(root, ())}
        while queue:
            symbol, path = queue.popleft()
            if len(path) >= 3:
                relevance = sum(term in symbol.lower() for term in primary_terms)
                if relevance:
                    candidates.append((len(path), relevance, root, path))
            if len(path) >= 16:
                continue
            for edge_index, edge in outgoing.get(symbol) or []:
                target = str(edge.get("to_symbol") or "")
                if not target or edge_index in path:
                    continue
                next_path = [*path, edge_index]
                state = (target, tuple(next_path))
                if state in visited:
                    continue
                visited.add(state)
                queue.append((target, next_path))
    if not candidates:
        return None
    _, _, root, path = max(candidates, key=lambda item: (item[0], item[1], item[2]))
    return root, path


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
        lines.append(
            f"| {step.get('step')} | {_markdown_table_cell(step.get('action'))} | `{evidence}` |"
        )
    if not outline.get("steps"):
        lines.append("| 1 | 当前证据不足以构造调用步骤 | `evidence_gap` |")
    lines.extend(["", "## 异常分支", "", "| 类型 | 位置/条件 | 证据 |", "|---|---|---|"])
    for label, key in (("异常", "error_flows"), ("清理", "cleanup_flows"), ("恢复", "recovery_flows")):
        for item in outline.get(key) or []:
            if not isinstance(item, dict):
                continue
            lines.append(
                f"| {label} | {_markdown_table_cell(item.get('text') or item.get('symbol') or '(未记录)')} | "
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
    lines.extend(["", "## 关联测试证据", "", "| 测试文件 | 符号 | 行号 | 证据 |", "|---|---|---:|---|"])
    for item in outline.get("related_tests") or []:
        if not isinstance(item, dict):
            continue
        lines.append(
            f"| `{_markdown_table_cell(item.get('file_path'))}` | "
            f"`{_markdown_table_cell(item.get('symbol') or '(未记录)')}` | "
            f"{item.get('start_line') or '-'}-{item.get('end_line') or '-'} | "
            f"`{item.get('evidence_id') or 'evidence_gap'}` |"
        )
    if not outline.get("related_tests"):
        lines.append("| 当前未定位到相关测试 | - | - | `evidence_gap` |")
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


def _markdown_table_cell(value: Any) -> str:
    """Render source text safely inside a Markdown table cell."""
    return " ".join(str(value or "").splitlines()).replace("|", "\\|").strip()


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


def _is_executable_error_path(line: str, *, declaration_symbol: str) -> bool:
    """Keep executable failure handling, not names or preprocessor constants."""
    if not line or line.startswith("#") or declaration_symbol:
        return False
    return bool(
        _ERROR_GUARD_PATTERN.search(line)
        or _ERROR_RETURN_PATTERN.search(line)
        or _ERROR_STATUS_PATTERN.search(line)
        or "SPDK_ERRLOG(" in line
        or _ERROR_CALLEE_PATTERN.search(line)
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
    # Start with verified card symbols, then walk a bounded reverse-call frontier.
    # A one-hop grep can find ``payload_login -> login_complete`` but misses the
    # verified ingress that reaches that callback through the read loop.
    all_seed_symbols = _prioritize_flow_symbols(
        _dedupe([*declared_symbols, *called_symbols]),
        analysis_target=str(source_pack.get("analysis_target") or ""),
    )
    # Reserve half of the finite search budget for verified expansion.  Feeding
    # every evidence-card symbol into the queue first starves the callback
    # chain discovered from the earliest, most relevant anchors.
    seed_symbols = all_seed_symbols[: max(1, (max_symbols + 1) // 2)]
    symbols = deque(seed_symbols)
    queued_symbols = set(seed_symbols)
    processed_symbols: set[str] = set()
    project_prefixes = {
        symbol.lstrip("_").split("_", 1)[0]
        for symbol in all_seed_symbols
        if "_" in symbol and not symbol.lstrip("_").isupper()
    }
    primary_terms = _primary_flow_terms(str(source_pack.get("analysis_target") or ""))
    primary_prefixes = {
        symbol.lstrip("_").split("_", 1)[0]
        for symbol in all_seed_symbols
        if "_" in symbol
        and any(term in symbol.lower() for term in primary_terms)
    }
    roots = _dedupe(roots)[:12]
    if not symbols or not roots:
        return {"call_edges": [], "related_tests": []}

    blob_cache: dict[str, tuple[list[str], str]] = {}
    sanitized_blob_cache: dict[str, list[str]] = {}
    edges: list[dict[str, Any]] = []
    tests: list[dict[str, Any]] = []
    deadline = time.monotonic() + 35.0
    while (
        symbols
        and len(processed_symbols) < max(1, max_symbols)
        and len(edges) + len(tests) < max_matches
        and time.monotonic() < deadline
    ):
        symbol = symbols.popleft()
        if symbol in processed_symbols:
            continue
        processed_symbols.add(symbol)
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
            reference_kind = _line_symbol_reference_kind(sanitized_line, symbol)
            if not reference_kind:
                continue
            caller = _nearest_function_symbol(sanitized_lines, line_number)
            if caller == symbol:
                # A definition hit gives us an independently verified function
                # body.  Expand only project-local callees, still under the
                # same symbol/deadline budget, so a receive loop can reach its
                # login handler without sweeping in libc or unrelated modules.
                for callee, call_line in _function_local_calls(
                    sanitized_lines,
                    line_number=line_number,
                    symbol=symbol,
                ):
                    if (
                        callee in _CONTROL_WORDS
                        or callee.isupper()
                        or (
                            project_prefixes
                            and callee.lstrip("_").split("_", 1)[0]
                            not in project_prefixes
                        )
                        or (
                            primary_prefixes
                            and callee.lstrip("_").split("_", 1)[0]
                            not in primary_prefixes
                        )
                    ):
                        continue
                    # A callee may already be a seed or a previously found
                    # reverse caller.  That suppresses only another search,
                    # never the source-verified edge itself.  Otherwise a
                    # crowded seed queue can silently remove the bridge that
                    # connects a receive path to its terminal handler.
                    if callee not in queued_symbols:
                        queued_symbols.add(callee)
                        # Follow the verified chain before consuming unrelated
                        # evidence-card seeds.  The edge below remains tied to
                        # a concrete source line; this changes only search
                        # order.
                        symbols.appendleft(callee)
                    edges.append(
                        _evidence_node(
                            evidence_id="",
                            file_path=file_path,
                            symbol=symbol,
                            start_line=call_line,
                            end_line=call_line,
                            provider="git-grep",
                            sha256=file_sha256,
                            details={
                                "from_symbol": symbol,
                                "to_symbol": callee,
                                # The downstream L1 ledger verifies this excerpt
                                # against the revision-pinned file. Do not strip
                                # indentation here, or a real call edge becomes
                                # unverifiable after materialization.
                                "matched_text": lines[call_line - 1][:300],
                            },
                        )
                    )
                # A response path can receive its completion function as an
                # argument rather than calling it directly. Record the
                # verified callback handoff so the flow reaches a real
                # completion endpoint without inventing a synchronous call.
                for callback, callback_line in _function_local_callback_references(
                    sanitized_lines,
                    line_number=line_number,
                    symbol=symbol,
                ):
                    callback_is_target_relevant = any(
                        term in callback.lower() for term in primary_terms
                    )
                    if (
                        callback in _CONTROL_WORDS
                        or callback.isupper()
                        or (
                            project_prefixes
                            and callback.lstrip("_").split("_", 1)[0]
                            not in project_prefixes
                            and not callback_is_target_relevant
                        )
                        or (
                            primary_prefixes
                            and callback.lstrip("_").split("_", 1)[0]
                            not in primary_prefixes
                            and not callback_is_target_relevant
                        )
                    ):
                        continue
                    if callback not in queued_symbols:
                        queued_symbols.add(callback)
                        symbols.appendleft(callback)
                    edges.append(
                        _evidence_node(
                            evidence_id="",
                            file_path=file_path,
                            symbol=symbol,
                            start_line=callback_line,
                            end_line=callback_line,
                            provider="git-grep",
                            sha256=file_sha256,
                            details={
                                "from_symbol": symbol,
                                "to_symbol": callback,
                                "relation": "callback_reference",
                                "matched_text": lines[callback_line - 1][:300],
                            },
                        )
                    )
                continue
            details = {
                "from_symbol": caller or "unknown_caller",
                "to_symbol": symbol,
                "matched_text": lines[line_number - 1][:300],
                "relation": reference_kind,
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
                # The caller is a verified reverse edge, so it is safe to use
                # as the next bounded search target.  This discovers the
                # upstream ingress without inventing a control-flow link.
                if caller not in queued_symbols:
                    queued_symbols.add(caller)
                    # Reverse callers are the shortest path from a callback
                    # or terminal handler to its real ingress.  Treat them as
                    # the active frontier so a crowded card set cannot spend
                    # the finite budget on unrelated symbols first.
                    symbols.appendleft(caller)
            if len(edges) + len(tests) >= max_matches:
                break
    return {
        "call_edges": _dedupe_evidence(edges, ("file_path", "start_line", "to_symbol")),
        "related_tests": _dedupe_evidence(tests, ("file_path", "start_line", "matched_symbol")),
    }


def _line_has_symbol_call(line_text: str, symbol: str) -> bool:
    return re.search(rf"\b{re.escape(symbol)}\s*\(", line_text) is not None


def _line_symbol_reference_kind(line_text: str, symbol: str) -> str:
    """Classify a verified direct call or C callback reference on one code line."""
    if _line_has_symbol_call(line_text, symbol):
        return "direct_call"
    if re.search(rf"\b{re.escape(symbol)}\b", line_text):
        return "callback_reference"
    return ""


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
    # Keep this bounded, but allow normal C functions that contain a state
    # machine or parsing loop to exceed the old 80-line lookup window.
    for index in range(start, max(-1, start - 600), -1):
        symbol = _definition_symbol("\n".join(lines[index : index + 16]))
        if symbol:
            return symbol
    return ""


def _function_local_calls(
    lines: list[str],
    *,
    line_number: int,
    symbol: str,
) -> list[tuple[str, int]]:
    """Return direct calls from one verified C-like function definition."""
    origin = min(max(0, line_number - 1), len(lines) - 1)
    start = origin
    for index in range(origin, max(-1, origin - 12), -1):
        if _definition_symbol("\n".join(lines[index : index + 16])) == symbol:
            start = index
            break
    calls: list[tuple[str, int]] = []
    depth = 0
    opened = False
    for index in range(start, min(len(lines), start + 600)):
        line = lines[index]
        if "{" in line:
            opened = True
        if opened:
            for callee in _CALL_PATTERN.findall(line):
                if callee not in _CONTROL_WORDS and callee != symbol:
                    calls.append((callee, index + 1))
        depth += line.count("{") - line.count("}")
        if opened and depth <= 0:
            break
    return calls


def _function_local_callback_references(
    lines: list[str],
    *,
    line_number: int,
    symbol: str,
) -> list[tuple[str, int]]:
    """Return callback-like function identifiers passed by one C function.

    The pattern is deliberately narrower than general identifier extraction:
    it requires a completion/handler/callback naming signal on a line that
    invokes another function. The resulting edge represents callback handoff,
    not a fabricated synchronous invocation.
    """
    origin = min(max(0, line_number - 1), len(lines) - 1)
    start = origin
    for index in range(origin, max(-1, origin - 12), -1):
        if _definition_symbol("\n".join(lines[index : index + 16])) == symbol:
            start = index
            break
    callbacks: list[tuple[str, int]] = []
    seen: set[tuple[str, int]] = set()
    depth = 0
    opened = False
    for index in range(start, min(len(lines), start + 600)):
        line = lines[index]
        if "{" in line:
            opened = True
        if opened and _CALL_PATTERN.search(line):
            for callback in _CALLBACK_ARGUMENT_PATTERN.findall(line):
                lowered = callback.lower()
                if lowered in {"callback", "handler", "complete"} or callback == symbol:
                    continue
                key = (callback, index + 1)
                if key not in seen:
                    seen.add(key)
                    callbacks.append(key)
        depth += line.count("{") - line.count("}")
        if opened and depth <= 0:
            break
    return callbacks


def _definition_symbol(line: str) -> str:
    lines = line.splitlines()
    if not lines:
        return ""
    # This helper is called while scanning a source window.  Only inspect the
    # current source line (or its immediately following split signature), never
    # a later function in the window: otherwise a call can be attributed to a
    # definition that happens to appear several lines below it.
    # Match from the current source line only.  The existing C definition
    # expression handles split return types, pointers, and multi-line
    # arguments; using ``search`` against a source window was the bug because
    # it could instead select a later definition in that window.
    match = _FUNCTION_DEFINITION_PATTERN.match("\n".join(lines[:10]))
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


def _prioritize_flow_symbols(symbols: list[str], *, analysis_target: str) -> list[str]:
    """Keep target-relevant verified symbols at the front of a bounded search."""
    primary_terms = _primary_flow_terms(analysis_target)
    target_terms = {
        term.lower()
        for term in re.findall(r"[A-Za-z][A-Za-z0-9_]{2,}", analysis_target)
        if term.lower() not in {"the", "and", "for", "with", "from", "target"}
    }
    if not target_terms:
        return symbols

    def relevance(symbol: str) -> int:
        lowered = symbol.lower()
        # The clause before the first colon is the user's core analysis
        # subject.  The rest commonly enumerates CHAP, Digest, recovery, and
        # other coverage dimensions; keep those useful, but never let them
        # starve the primary protocol path in a finite traversal.
        primary_score = sum(len(term) for term in primary_terms if term in lowered)
        supporting_score = sum(len(term) for term in target_terms if term in lowered)
        branch_penalty = 50 if re.search(r"(?:err|error|fail|timeout|reject)", lowered) else 0
        return primary_score * 8 + supporting_score - branch_penalty

    # ``sorted`` is stable, preserving evidence-card order when relevance is
    # equal and preventing the ranker from inventing a source relationship.
    return sorted(symbols, key=relevance, reverse=True)


def _primary_flow_terms(analysis_target: str) -> set[str]:
    primary_clause = re.split(r"[:：]", analysis_target, maxsplit=1)[0]
    return {
        term.lower()
        for term in re.findall(r"[A-Za-z][A-Za-z0-9_]{2,}", primary_clause)
        if term.lower() not in {"the", "and", "for", "with", "from", "target"}
    }


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
