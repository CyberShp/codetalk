#!/usr/bin/env python3
"""Codetalks V2.4 Markdown-first workflow and directory-layout guard."""

from __future__ import annotations
import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Any

RESERVED_DIR_NAMES = {"活文档", "内部索引", "正式输出"}
FORBIDDEN_NESTED = [
    Path("活文档/活文档"),
    Path("活文档/内部索引"),
    Path("活文档/正式输出"),
    Path("内部索引/活文档"),
    Path("正式输出/活文档"),
]
STEP_FILE_RE = re.compile(r"^\d{2}-.*\.md$")
FORMAL_STEP_FILE_RE = re.compile(r"^\d{2}-.*\.md$")

def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()

def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)

def save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    temp.replace(path)

def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()

def resolve_run_root(value: str) -> Path:
    root = Path(value).expanduser().resolve()
    if root.name in RESERVED_DIR_NAMES:
        raise SystemExit(
            f"`--workspace` 必须指向三个目录的共同父目录，不能直接指向 `{root.name}/`。\n"
            f"正确示例：--workspace {root.parent}\n"
            f"系统将自动创建：{root.parent}/活文档、{root.parent}/内部索引、{root.parent}/正式输出"
        )
    return root

def state_path(root: Path) -> Path:
    return root / "内部索引/运行状态.json"

def ensure_state(root: Path) -> dict:
    path = state_path(root)
    if not path.exists():
        raise SystemExit(f"任务未初始化：{path}")
    return load_json(path)

def load_manifest(state: dict) -> dict:
    return load_json(Path(state["skill_root"]) / "workflow-manifest.json")

def find_step(manifest: dict, step_id: str) -> dict:
    for step in manifest["steps"]:
        if step["id"] == step_id:
            return step
    raise SystemExit(f"未知步骤：{step_id}")

def nonspace_length(text: str) -> int:
    return len(re.sub(r"\s+", "", text))

def strip_tables_code_and_headers(text: str) -> str:
    text = re.sub(r"```.*?```", "", text, flags=re.S)
    retained = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#") or stripped.startswith("|"):
            continue
        if re.fullmatch(r"[-:| ]+", stripped):
            continue
        retained.append(stripped)
    return "\n".join(retained)

def validate_markdown(path: Path, minimum: int) -> list[str]:
    if not path.exists():
        return [f"缺少 Markdown：{path}"]
    text = path.read_text(encoding="utf-8", errors="replace")
    errors = []
    if nonspace_length(text) < minimum:
        errors.append(f"Markdown 内容过少（{nonspace_length(text)} < {minimum}）：{path}")
    narrative = strip_tables_code_and_headers(text)
    if nonspace_length(narrative) < max(120, minimum // 4):
        errors.append(f"Markdown 叙述不足，疑似纯表格或空模板：{path}")
    if re.search(r'"(?:flow_id|branch_id|risk_id|case_id)"\s*:', text):
        errors.append(f"Markdown 疑似直接倾倒 JSON：{path}")
    return errors

def section_text(text: str, heading: str, headings: list[str]) -> str:
    start = text.find(heading)
    if start < 0:
        return ""
    start += len(heading)
    candidates = [text.find(h, start) for h in headings if text.find(h, start) >= 0]
    end = min(candidates) if candidates else len(text)
    return text[start:end]

def validate_flow(path: Path, manifest: dict) -> list[str]:
    text = path.read_text(encoding="utf-8", errors="replace")
    errors = []
    headings = manifest["flow_required_headings"]
    for heading in headings:
        if heading not in text:
            errors.append(f"流程讲解缺少章节“{heading}”：{path}")
    if nonspace_length(text) < 2200:
        errors.append(f"流程讲解过短，无法视为完整开发讲解：{path}")
    for heading in manifest["flow_key_narrative_headings"]:
        section = section_text(text, heading, headings)
        if nonspace_length(strip_tables_code_and_headers(section)) < 90:
            errors.append(f"流程关键章节缺少自然语言叙述“{heading}”：{path}")
    return errors

def validate_coverage(path: Path, manifest: dict) -> list[str]:
    errors = validate_markdown(path, 300)
    if not path.exists():
        return errors
    text = path.read_text(encoding="utf-8", errors="replace")
    if "Pass ID" not in text and "分析项" not in text:
        errors.append(f"Coverage Gate 缺少标准表头：{path}")
    if re.search(r"\bskipped\b|跳过", text, flags=re.I):
        errors.append(f"Coverage Gate 使用了禁止状态 skipped/跳过：{path}")
    if not any(status in text for status in manifest["coverage_allowed_outcomes"]):
        errors.append(f"Coverage Gate 未包含合法 Outcome：{path}")
    return errors

def validate_evidence_index(root: Path, manifest: dict) -> list[str]:
    path = root / "内部索引/输入材料索引.json"
    if not path.exists():
        return [f"缺少输入材料内部索引：{path}"]
    try:
        data = load_json(path)
    except Exception as exc:
        return [f"输入材料索引无法解析：{exc}"]
    allowed = set(manifest["evidence_allowed_status"])
    errors = []
    for number, item in enumerate(data.get("items", []), 1):
        identity = item.get("id", str(number))
        status = item.get("status")
        if status not in allowed:
            errors.append(f"材料 {identity} 状态非法：{status}")
        if status in {"parsed", "partially_parsed"}:
            if not item.get("parser"):
                errors.append(f"材料 {identity} 缺少 parser")
            if not item.get("consumed_ranges"):
                errors.append(f"材料 {identity} 缺少 consumed_ranges")
            kind = str(item.get("type", "")).lower()
            if kind in {"xlsx", "xls", "spreadsheet", "coverage_xlsx"}:
                if not item.get("sheets"):
                    errors.append(f"电子表格 {identity} 未记录 sheets")
                rows = item.get("records_parsed")
                if not isinstance(rows, int) or rows <= 0:
                    errors.append(f"电子表格 {identity} 未记录有效 records_parsed")
    return errors

def validate_layout(root: Path, *, final_phase: bool = False) -> list[str]:
    errors = []

    # Three sibling directories must exist directly under the run root.
    for name in ["活文档", "内部索引", "正式输出"]:
        path = root / name
        if not path.exists() or not path.is_dir():
            errors.append(f"缺少顶层同级目录：{path}")

    # Reject nested copies and misplaced sibling directories.
    for relative in FORBIDDEN_NESTED:
        path = root / relative
        if path.exists():
            errors.append(f"检测到禁止的嵌套目录：{path}")

    # Reject numbered process documents scattered at root level.
    for path in root.iterdir() if root.exists() else []:
        if path.is_file() and STEP_FILE_RE.match(path.name):
            errors.append(f"步骤文档不得散落在运行根目录：{path}")

    # Internal index must never be an empty placeholder.
    index_dir = root / "内部索引"
    required_index = [
        index_dir / "运行状态.json",
        index_dir / "运行计划.json",
        index_dir / "输入材料索引.json",
    ]
    for path in required_index:
        if not path.exists():
            errors.append(f"内部索引缺少必要文件：{path}")

    # Formal output contains final deliverables only; no numbered step files or subdirectories.
    formal_dir = root / "正式输出"
    if formal_dir.exists():
        for path in formal_dir.iterdir():
            if path.is_dir():
                errors.append(f"正式输出目录不得包含子目录：{path}")
            elif FORMAL_STEP_FILE_RE.match(path.name):
                errors.append(f"步骤编号文件不得放入正式输出目录：{path}")
        if not final_phase and any(formal_dir.iterdir()):
            errors.append("Step 01–08 期间 `正式输出/` 必须保持为空；正式交付只在 Step 09 生成。")

    return errors

def validate_step(root: Path, step: dict, manifest: dict) -> list[str]:
    final_phase = step["id"] == "09"
    errors = validate_layout(root, final_phase=final_phase)
    minimum = int(step.get("markdown_min_chars", 300))

    for relative in step.get("required", []):
        path = root / relative
        if not path.exists() or (path.is_file() and path.stat().st_size == 0):
            errors.append(f"缺少或为空：{relative}")
            continue
        if path.suffix.lower() == ".md":
            if "覆盖门禁" in relative:
                errors.extend(validate_coverage(path, manifest))
            else:
                errors.extend(validate_markdown(path, minimum))

    for pattern in step.get("requires_glob", []):
        matches = list(root.glob(pattern))
        if not matches:
            errors.append(f"未找到必需工件：{pattern}")
        elif step.get("flow_narrative_validation"):
            for path in matches:
                errors.extend(validate_flow(path, manifest))

    if step["id"] == "02":
        errors.extend(validate_evidence_index(root, manifest))

    if step["id"] == "08":
        judge_path = root / "内部索引/独立审查状态.json"
        if judge_path.exists():
            try:
                judge = load_json(judge_path)
                if judge.get("independent") is not True:
                    errors.append("独立审查状态未声明 independent=true")
                if not judge.get("checked_artifacts"):
                    errors.append("独立审查未记录 checked_artifacts")
            except Exception as exc:
                errors.append(f"独立审查状态无法解析：{exc}")

    return errors

def require_core_rules(state: dict, manifest: dict) -> None:
    missing = [
        rule for rule in manifest["required_core_rules"]
        if rule not in state.get("core_rules_ack", {})
    ]
    if missing:
        raise SystemExit("核心规则未 ACK：" + ", ".join(missing))

def command_init(args) -> None:
    root = resolve_run_root(args.workspace)
    root.mkdir(parents=True, exist_ok=True)
    skill_root = Path(args.skill_root).expanduser().resolve()
    manifest = load_json(skill_root / "workflow-manifest.json")

    for relative in [
        "活文档/流程讲解",
        "活文档/覆盖门禁",
        "内部索引",
        "正式输出",
    ]:
        (root / relative).mkdir(parents=True, exist_ok=True)

    state = {
        "version": "2.4",
        "created_at": now(),
        "updated_at": now(),
        "skill_root": str(skill_root),
        "run_root": str(root),
        "source_raw": args.source_raw,
        "source_verified": args.source_verified,
        "scenario": args.scenario,
        "mode": args.mode,
        "status": "initialized",
        "current_step": None,
        "completed_steps": [],
        "core_rules_ack": {},
        "judge": {
            "required": args.scenario == "module-analysis" and args.mode == "depth",
            "status": "pending",
        },
        "verdict": None,
    }
    save_json(state_path(root), state)
    save_json(root / "内部索引/运行计划.json", {"version": "2.4", "passes": []})
    save_json(root / "内部索引/输入材料索引.json", {"version": "2.4", "items": []})

    layout_errors = validate_layout(root, final_phase=False)
    if layout_errors:
        raise SystemExit("\n".join(layout_errors))

    print(json.dumps({
        "ok": True,
        "run_root": str(root),
        "created_sibling_directories": [
            str(root / "活文档"),
            str(root / "内部索引"),
            str(root / "正式输出"),
        ],
        "note": "Step 01–08 只写活文档和内部索引；正式输出仅在 Step 09 生成。"
    }, ensure_ascii=False))

def command_ack(args) -> None:
    root = resolve_run_root(args.workspace)
    state = ensure_state(root)
    manifest = load_manifest(state)
    required = manifest["required_core_rules"]
    if args.rule not in required:
        raise SystemExit(f"未知核心规则：{args.rule}")
    expected = (Path(state["skill_root"]) / required[args.rule]).resolve()
    actual = Path(args.file).expanduser().resolve()
    if expected != actual or not actual.exists():
        raise SystemExit(f"核心规则文件不匹配：期望 {expected}，实际 {actual}")
    state["core_rules_ack"][args.rule] = {
        "file": str(actual),
        "sha256": sha256(actual),
        "ack_at": now(),
    }
    state["updated_at"] = now()
    save_json(state_path(root), state)
    print(json.dumps({"ok": True, "ack": args.rule}, ensure_ascii=False))

def command_start(args) -> None:
    root = resolve_run_root(args.workspace)
    state = ensure_state(root)
    manifest = load_manifest(state)
    require_core_rules(state, manifest)
    layout_errors = validate_layout(root, final_phase=args.step == "09")
    if layout_errors:
        raise SystemExit("\n".join(layout_errors))

    step = find_step(manifest, args.step)
    ids = [item["id"] for item in manifest["steps"]]
    previous = ids[:ids.index(args.step)]
    missing = [item for item in previous if item not in state["completed_steps"]]
    if missing:
        raise SystemExit("前置步骤未完成：" + ", ".join(missing))
    if state["current_step"] not in {None, args.step}:
        raise SystemExit(f"当前仍在 Step {state['current_step']}")

    state["current_step"] = args.step
    state["status"] = "in_progress"
    state["updated_at"] = now()
    save_json(state_path(root), state)
    print(json.dumps({
        "ok": True,
        "step": args.step,
        "only_load_step_file": str(Path(state["skill_root"]) / step["file"]),
        "write_scope": "正式输出/" if args.step == "09" else "活文档/ 与 内部索引/"
    }, ensure_ascii=False))

def command_complete(args) -> None:
    root = resolve_run_root(args.workspace)
    state = ensure_state(root)
    manifest = load_manifest(state)
    if state.get("current_step") != args.step:
        raise SystemExit(f"当前步骤为 {state.get('current_step')}，不是 {args.step}")
    errors = validate_step(root, find_step(manifest, args.step), manifest)
    if errors:
        print(json.dumps({"ok": False, "errors": errors}, ensure_ascii=False, indent=2))
        raise SystemExit(2)

    if args.step not in state["completed_steps"]:
        state["completed_steps"].append(args.step)
    state["current_step"] = None
    state["updated_at"] = now()
    if args.step == "08":
        state["judge"]["status"] = "complete"
    save_json(state_path(root), state)
    print(json.dumps({"ok": True, "completed_step": args.step}, ensure_ascii=False))

def command_validate(args) -> None:
    root = resolve_run_root(args.workspace)
    state = ensure_state(root)
    manifest = load_manifest(state)
    require_core_rules(state, manifest)
    errors = []
    for step_id in state.get("completed_steps", []):
        errors.extend(validate_step(root, find_step(manifest, step_id), manifest))
    print(json.dumps({
        "ok": not errors,
        "errors": errors,
        "completed_steps": state.get("completed_steps", []),
        "current_step": state.get("current_step"),
    }, ensure_ascii=False, indent=2))
    if errors:
        raise SystemExit(2)

def command_handoff(args) -> None:
    root = resolve_run_root(args.workspace)
    state = ensure_state(root)
    manifest = load_manifest(state)
    completed = set(state.get("completed_steps", []))
    remaining = [step for step in manifest["steps"] if step["id"] not in completed]
    next_step = remaining[0] if remaining else None

    lines = [
        "# 任务交接",
        "",
        f"- 更新时间：{now()}",
        f"- 运行根目录：`{root}`",
        f"- 场景：{state['scenario']}",
        f"- 模式：{state['mode']}",
        f"- 当前步骤：{state.get('current_step') or '无'}",
        f"- 已完成步骤：{', '.join(state.get('completed_steps', [])) or '无'}",
        "",
        "## 当前活文档",
    ]
    documents = sorted(str(path.relative_to(root)) for path in (root / "活文档").rglob("*.md"))
    lines.extend([f"- `{item}`" for item in documents] or ["- 暂无"])

    lines += ["", "## 动态下一步"]
    if next_step:
        lines += [
            f"1. 启动 Step {next_step['id']}。",
            f"2. 只读取 `{next_step['file']}`。",
            "3. 按目录契约写入对应目录。",
        ]
    else:
        lines.append("所有步骤完成，执行 validate 和 finalize。")

    path = root / "活文档/任务交接.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"ok": True, "handoff": str(path)}, ensure_ascii=False))

def command_finalize(args) -> None:
    root = resolve_run_root(args.workspace)
    state = ensure_state(root)
    manifest = load_manifest(state)
    errors = []
    for step in manifest["steps"]:
        if step["id"] not in state.get("completed_steps", []):
            errors.append(f"未完成步骤：{step['id']}")
        else:
            errors.extend(validate_step(root, step, manifest))
    if state.get("judge", {}).get("required") and state.get("judge", {}).get("status") != "complete":
        errors.append("深度型模块全量分析未完成独立审查")

    if errors:
        state["status"] = "validation_failed"
        state["verdict"] = "PARTIAL"
        save_json(state_path(root), state)
        print(json.dumps({"ok": False, "verdict": "PARTIAL", "errors": errors},
                         ensure_ascii=False, indent=2))
        raise SystemExit(2)

    state["status"] = "complete"
    state["verdict"] = "READY"
    state["updated_at"] = now()
    save_json(state_path(root), state)
    print(json.dumps({
        "ok": True,
        "verdict": "READY",
        "formal_output": str(root / "正式输出"),
    }, ensure_ascii=False))

def main() -> None:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)

    init = commands.add_parser("init")
    init.add_argument("--skill-root", required=True)
    init.add_argument("--workspace", required=True,
                      help="运行根目录；不得直接填写活文档/、内部索引/或正式输出/")
    init.add_argument("--source-raw", required=True)
    init.add_argument("--source-verified", required=True)
    init.add_argument("--output", required=False,
                      help="兼容旧调用参数；V2.4 正式输出固定为 <workspace>/正式输出/")
    init.add_argument("--scenario", required=True,
                      choices=["module-analysis", "issue-regression", "root-cause",
                               "special-risk", "custom"])
    init.add_argument("--mode", required=True, choices=["speed", "depth"])
    init.set_defaults(function=command_init)

    ack = commands.add_parser("ack-core")
    ack.add_argument("--workspace", required=True)
    ack.add_argument("--rule", required=True)
    ack.add_argument("--file", required=True)
    ack.set_defaults(function=command_ack)

    start = commands.add_parser("start-step")
    start.add_argument("--workspace", required=True)
    start.add_argument("--step", required=True)
    start.set_defaults(function=command_start)

    complete = commands.add_parser("complete-step")
    complete.add_argument("--workspace", required=True)
    complete.add_argument("--step", required=True)
    complete.set_defaults(function=command_complete)

    validate = commands.add_parser("validate")
    validate.add_argument("--workspace", required=True)
    validate.set_defaults(function=command_validate)

    handoff = commands.add_parser("handoff")
    handoff.add_argument("--workspace", required=True)
    handoff.set_defaults(function=command_handoff)

    finalize = commands.add_parser("finalize")
    finalize.add_argument("--workspace", required=True)
    finalize.set_defaults(function=command_finalize)

    args = parser.parse_args()
    args.function(args)

if __name__ == "__main__":
    main()
