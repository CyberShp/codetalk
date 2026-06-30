"""真实覆盖率推荐 E2E 门禁。

该脚本只模拟覆盖率命中数据；backend/frontend/GitNexus/CGC/AI/目标仓库都
必须是真实运行环境。失败或外部能力不可用时输出 blocked/failed，不用 mock
冒充通过。
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx


DEFAULT_BACKEND = os.environ.get("CODETALK_BACKEND", "http://127.0.0.1:3004")
DEFAULT_FRONTEND = os.environ.get("CODETALK_FRONTEND", "http://127.0.0.1:3205")
DEFAULT_REPO = os.environ.get(
    "CODETALK_E2E_REPO",
    r"E:\codetalk_test\codetalks-Test\fixtures\spdk",
)


def _json_dump(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _request(client: httpx.Client, method: str, url: str, **kwargs: Any) -> httpx.Response:
    resp = client.request(method, url, **kwargs)
    if resp.status_code >= 400:
        raise RuntimeError(f"{method} {url} HTTP {resp.status_code}: {resp.text[:500]}")
    return resp


def _block(reason: str, out_dir: Path, details: dict[str, Any] | None = None) -> int:
    payload = {
        "status": "blocked",
        "reason": reason,
        "details": details or {},
        "generated_at": datetime.now().isoformat(),
    }
    _json_dump(out_dir / "coverage_real_e2e_result.json", payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 2


def _fail(reason: str, out_dir: Path, details: dict[str, Any] | None = None) -> int:
    payload = {
        "status": "failed",
        "reason": reason,
        "details": details or {},
        "generated_at": datetime.now().isoformat(),
    }
    _json_dump(out_dir / "coverage_real_e2e_result.json", payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 1


def _pass(out_dir: Path, details: dict[str, Any]) -> int:
    payload = {
        "status": "passed",
        "details": details,
        "generated_at": datetime.now().isoformat(),
    }
    _json_dump(out_dir / "coverage_real_e2e_result.json", payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def _ensure_services(client: httpx.Client, backend: str, frontend: str, out_dir: Path) -> dict:
    health = _request(client, "GET", f"{backend}/health").json()
    try:
        frontend_resp = client.get(frontend, timeout=10.0)
    except Exception as exc:
        raise RuntimeError(f"frontend 不可用：{exc}") from exc
    if frontend_resp.status_code >= 500:
        raise RuntimeError(f"frontend HTTP {frontend_resp.status_code}")
    tools = _request(client, "GET", f"{backend}/api/tools/status").json()
    llms = _request(client, "GET", f"{backend}/api/settings/llm").json()
    active_llm = next((cfg for cfg in llms if cfg.get("is_active")), None)
    if active_llm is None:
        active_llm = next((cfg for cfg in llms if cfg.get("is_chat_model")), None)
    payload = {
        "backend_health": health,
        "frontend_status": frontend_resp.status_code,
        "tools": tools,
        "active_llm": {
            k: active_llm.get(k)
            for k in ("id", "name", "provider", "model", "base_url")
        } if active_llm else None,
    }
    _json_dump(out_dir / "service_probe.json", payload)
    if not active_llm:
        raise RuntimeError("没有 active LLM 配置")
    for tool in ("gitnexus", "cgc"):
        status = tools.get(tool) or {}
        if not status.get("healthy"):
            raise RuntimeError(f"{tool} 不健康：{status}")
    return payload


def _find_or_create_workspace(
    client: httpx.Client,
    backend: str,
    repo_path: str,
    out_dir: Path,
) -> dict:
    workspaces = _request(client, "GET", f"{backend}/api/workspaces").json()
    for ws in workspaces:
        if str(ws.get("repo_path", "")).lower() == repo_path.lower():
            _json_dump(out_dir / "workspace.json", ws)
            return ws
    ws = _request(
        client,
        "POST",
        f"{backend}/api/workspaces",
        json={"name": f"coverage-real-e2e-{uuid.uuid4().hex[:8]}", "repo_path": repo_path},
    ).json()
    _json_dump(out_dir / "workspace.json", ws)
    return ws


def _wait_indexed(client: httpx.Client, backend: str, ws_id: str, out_dir: Path) -> dict:
    last = {}
    for _ in range(90):
        last = _request(client, "GET", f"{backend}/api/workspaces/{ws_id}/index-status").json()
        if last.get("indexed") == 1:
            _json_dump(out_dir / "index_status.json", last)
            return last
        if last.get("indexed") == -1:
            raise RuntimeError(f"索引失败：{last}")
        time.sleep(5)
    raise RuntimeError(f"索引超时：{last}")


def _default_plan(client: httpx.Client, backend: str, ws_id: str) -> dict:
    plan = _request(client, "GET", f"{backend}/api/workspaces/{ws_id}/analysis/default-plan").json()
    plan["analysis_objects"] = [
        {
            "id": "obj_real_flow",
            "text": "登录、连接、状态切换、异常传播、资源清理这些测试关注路径",
            "kind": "topic",
            "priority": "high",
            "path_hints": ["lib/iscsi", "app/iscsi_tgt", "test/iscsi_tgt"],
            "scope_hints": [
                {"path": "lib/iscsi", "role": "primary"},
                {"path": "app/iscsi_tgt", "role": "supporting"},
                {"path": "test/iscsi_tgt", "role": "supporting"},
            ],
        }
    ]
    enabled_reports = {"source_reading", "business_flow", "test_design"}
    for report in plan.get("reports", []):
        report_type = report.get("report_type") or report.get("template_id") or report.get("id")
        report["enabled"] = report_type in enabled_reports
    plan["user_guidance"] = (
        "从测试人员视角说明外部触发、输入构造、正常路径、异常路径、可观测信号、"
        "灰盒辅助和 SFMEA，不要把源码函数调用写成黑盒步骤。"
    )
    return plan


def _run_workspace_analysis(
    client: httpx.Client,
    backend: str,
    ws_id: str,
    out_dir: Path,
) -> dict:
    plan = _default_plan(client, backend, ws_id)
    _json_dump(out_dir / "analysis_plan.json", plan)
    resp = _request(
        client,
        "POST",
        f"{backend}/api/workspaces/{ws_id}/analyze",
        json={"plan": plan, "include_coverage_gaps": False},
    ).json()
    task_id = resp["task_id"]
    last = {}
    for _ in range(180):
        last = _request(client, "GET", f"{backend}/api/workspaces/{ws_id}/analyze-status").json()
        if last.get("analyze_status") in {"done", "failed"}:
            break
        time.sleep(10)
    _json_dump(out_dir / "analysis_status.json", last)
    if last.get("analyze_status") == "failed":
        raise RuntimeError(f"分析任务失败：{last}")
    if last.get("analyze_status") != "done":
        raise RuntimeError(f"分析任务超时：{last}")
    ws = _request(client, "GET", f"{backend}/api/workspaces/{ws_id}").json()
    reports = ws.get("reports") or []
    needed = {"source_reading", "business_flow", "test_design"}
    found = {r.get("report_type") for r in reports if r.get("task_id") == task_id}
    missing = needed - found
    if missing:
        raise RuntimeError(f"分析报告缺失：{sorted(missing)}")
    _json_dump(out_dir / "workspace_after_analysis.json", ws)
    return {"task_id": task_id, "reports": reports}


def _function_exists(repo: Path, function_name: str) -> tuple[str, int] | None:
    pattern = re.compile(rf"\b{re.escape(function_name)}\s*\(")
    for root, dirs, files in os.walk(repo):
        dirs[:] = [d for d in dirs if d not in {".git", "build", "node_modules"}]
        for name in files:
            if not name.endswith((".c", ".h", ".cc", ".cpp")):
                continue
            path = Path(root) / name
            try:
                lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue
            for idx, line in enumerate(lines, start=1):
                if pattern.search(line):
                    return path.relative_to(repo).as_posix(), idx
    return None


def _make_simulated_coverage(repo: Path, out_dir: Path) -> Path:
    candidates = [
        "iscsi_conn_construct",
        "iscsi_conn_start",
        "iscsi_conn_cleanup_backend",
        "iscsi_conn_destruct",
        "iscsi_conn_read_data",
        "iscsi_conn_write_pdu",
        "iscsi_conn_logout",
        "iscsi_op_login_check_target",
        "iscsi_op_login_check_session",
        "iscsi_pdu_hdr_op_login",
        "iscsi_pdu_payload_op_login",
        "iscsi_pdu_hdr_op_logout",
        "iscsi_tgt_node_access",
        "iscsi_tgt_node_add_pg_ig_map",
        "iscsi_tgt_node_cleanup_luns",
        "iscsi_opts_verify",
    ]
    rows = [["feature", "module", "code_location", "function", "triggered", "hit_count"]]
    covered = True
    for fn in candidates:
        found = _function_exists(repo, fn)
        if not found:
            continue
        rel, line = found
        rows.append([
            "real_e2e",
            str(Path(rel).parent).replace("\\", "/"),
            f"{rel}:{line}-{line + 20}",
            fn,
            "true" if covered else "false",
            "1" if covered else "0",
        ])
        covered = not covered
    if len(rows) < 5:
        raise RuntimeError("目标仓库中可用于模拟覆盖率的真实函数不足")
    path = out_dir / "simulated_spdk_coverage.csv"
    with path.open("w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerows(rows)
    return path


def _upload_and_analyze_coverage(
    client: httpx.Client,
    backend: str,
    ws_id: str,
    coverage_path: Path,
    out_dir: Path,
) -> dict:
    with coverage_path.open("rb") as f:
        upload = _request(
            client,
            "POST",
            f"{backend}/api/coverage/upload",
            data={"name": "真实 E2E 模拟覆盖率", "workspace_id": ws_id},
            files={"files": (coverage_path.name, f, "text/csv")},
        ).json()
    _json_dump(out_dir / "coverage_upload.json", upload)
    result = _request(client, "POST", f"{backend}/api/coverage/{upload['id']}/analyze").json()
    detail = _request(client, "GET", f"{backend}/api/coverage/{upload['id']}").json()
    _json_dump(out_dir / "coverage_analyze_response.json", result)
    _json_dump(out_dir / "coverage_detail.json", detail)
    if detail.get("status") != "analyzed":
        raise RuntimeError(f"覆盖率分析状态不是 analyzed：{detail.get('status')}")
    return {"upload": upload, "result": result, "detail": detail}


def _artifact_dir(coverage_id: str) -> Path:
    return Path("backend") / "data" / "outputs" / "coverage" / coverage_id


def _validate_design(repo: Path, coverage_id: str, detail: dict, out_dir: Path) -> dict:
    artifact_dir = _artifact_dir(coverage_id)
    context_path = artifact_dir / "coverage_test_context.json"
    entry_path = artifact_dir / "coverage_entry_discovery.json"
    design_path = artifact_dir / "coverage_test_design.json"
    if not context_path.exists() or not entry_path.exists() or not design_path.exists():
        raise RuntimeError(f"缺少覆盖率产物：{context_path} / {entry_path} / {design_path}")
    context = json.loads(context_path.read_text(encoding="utf-8"))
    entry_discovery = json.loads(entry_path.read_text(encoding="utf-8"))
    design = json.loads(design_path.read_text(encoding="utf-8"))
    _json_dump(out_dir / "coverage_test_context.json", context)
    _json_dump(out_dir / "coverage_entry_discovery.json", entry_discovery)
    _json_dump(out_dir / "coverage_test_design.json", design)

    counts = context.get("evidence_source_counts") or {}
    for key in ("coverage", "source", "gitnexus", "cgc", "report", "entry_discovery"):
        if counts.get(key, 0) <= 0:
            raise RuntimeError(f"上下文缺少 {key} 证据：{counts}")
    entry_cards = entry_discovery.get("cards") or []
    if not entry_cards:
        raise RuntimeError("coverage_entry_discovery.json 未生成入口发现卡")
    entry_candidate_count = sum(
        len(card.get("candidate_external_entries") or [])
        for card in entry_cards
    )
    if entry_candidate_count <= 0:
        raise RuntimeError("入口发现没有产生任何外部入口候选")
    if not ((context.get("entry_discovery") or {}).get("cards")):
        raise RuntimeError("coverage_test_context.json 未包含 entry_discovery")
    if not ((design.get("entry_discovery") or {}).get("cards")):
        raise RuntimeError("coverage_test_design.json 未包含 entry_discovery")
    if design.get("summary", {}).get("ai_status") != "available":
        raise RuntimeError(f"真实 AI 未参与覆盖率推荐：{design.get('summary')}")
    scenarios = design.get("test_scenarios") or []
    if not scenarios:
        raise RuntimeError("AI 未生成结构化测试场景")
    black_box_ready = sum(1 for item in scenarios if item.get("case_type") == "black_box_ready")
    gray_box_required = sum(1 for item in scenarios if item.get("case_type") == "gray_box_required")
    black_box_hypothesis = sum(1 for item in scenarios if item.get("case_type") == "black_box_hypothesis")
    total_scenarios = len(scenarios)
    if black_box_hypothesis:
        raise RuntimeError(
            "AI 输出仍包含 black_box_hypothesis，未形成可验收的黑盒/灰盒推荐："
            f"black_box_ready={black_box_ready}, gray_box_required={gray_box_required}, "
            f"black_box_hypothesis={black_box_hypothesis}, total={total_scenarios}"
        )
    if black_box_ready * 100 < total_scenarios * 70:
        raise RuntimeError(
            "黑盒推荐比例不足 70%："
            f"black_box_ready={black_box_ready}, gray_box_required={gray_box_required}, total={total_scenarios}"
        )
    if gray_box_required * 100 > total_scenarios * 30:
        raise RuntimeError(
            "灰盒降级比例超过 30%："
            f"black_box_ready={black_box_ready}, gray_box_required={gray_box_required}, total={total_scenarios}"
        )

    required = {
        "flow_purpose",
        "external_trigger",
        "input_construction",
        "normal_path",
        "error_path",
        "expected_result",
        "observable_signals",
        "gray_box_aid",
        "sfmea",
        "evidence_refs",
    }
    for scenario in scenarios:
        missing = [field for field in required if not scenario.get(field)]
        if missing:
            raise RuntimeError(f"测试场景缺少字段 {missing}: {scenario}")
        for list_field in (
            "observable_signals",
            "evidence_refs",
            "key_call_chain",
            "related_gaps",
            "verification_gaps",
        ):
            value = scenario.get(list_field)
            if not isinstance(value, list):
                raise RuntimeError(f"测试场景字段必须是列表 {list_field}: {scenario}")
            if any(
                len(str(item).strip()) <= 1
                and str(item).strip().lower() not in {"无", "n/a", "na", "no"}
                for item in value
            ):
                raise RuntimeError(f"测试场景字段疑似被拆成单字列表 {list_field}: {scenario}")
        sfmea = scenario.get("sfmea") or {}
        sfmea_missing = [
            field for field in (
                "failure_mode",
                "trigger_condition",
                "propagation_effect",
                "observable_effect",
                "recommended_test",
            )
            if not sfmea.get(field)
        ]
        if sfmea_missing:
            raise RuntimeError(f"SFMEA 字段缺失 {sfmea_missing}: {scenario}")
        black_box_text = "\n".join(
            str(scenario.get(k) or "")
            for k in ("external_trigger", "input_construction", "normal_path", "error_path")
        )
        if scenario.get("case_type") == "black_box_ready" and re.search(
            r"调用\s*\w+\s*\(|\b[\w./\\-]+\.(?:c|h)(?::\d+)?\b|进入.*分支|修改.*内部变量",
            black_box_text,
        ):
            raise RuntimeError(f"黑盒步骤泄漏白盒操作：{scenario}")

    raw = json.dumps({"detail": detail, "design": design, "context": context}, ensure_ascii=False)
    banned = [
        "Public workflow handles",
        "Run valid/boundary/malformed input",
        "Tool Orchestration",
        "Evidence Basis",
        "Test execution area",
        "CodeTalk Diagram",
        "4 跳内未追踪到外部入口",
        "需灰盒注入",
        "???",
    ]
    hits = [item for item in banned if item in raw]
    if hits:
        raise RuntimeError(f"出现禁止文案：{hits}")

    for gap in design.get("gaps") or []:
        file_path = gap.get("file_path")
        if file_path and not (repo / file_path).exists():
            raise RuntimeError(f"引用文件不存在：{file_path}")
        fn = gap.get("function_name")
        if fn and not _function_exists(repo, fn):
            raise RuntimeError(f"引用函数不存在：{fn}")
    return {
        "context_counts": counts,
        "scenario_count": len(scenarios),
        "black_box_ready_count": black_box_ready,
        "gray_box_required_count": gray_box_required,
        "black_box_ready_ratio": black_box_ready / total_scenarios,
        "gray_box_required_ratio": gray_box_required / total_scenarios,
        "artifact_dir": str(artifact_dir),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--backend", default=DEFAULT_BACKEND)
    parser.add_argument("--frontend", default=DEFAULT_FRONTEND)
    parser.add_argument("--repo", default=DEFAULT_REPO)
    parser.add_argument("--out-dir", default="")
    args = parser.parse_args()

    repo = Path(args.repo)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir = Path(args.out_dir or f"data/e2e/coverage-real-{ts}")
    out_dir.mkdir(parents=True, exist_ok=True)
    if not repo.exists():
        return _block(f"目标仓库不存在：{repo}", out_dir)

    with httpx.Client(timeout=60.0, trust_env=False) as client:
        try:
            service_probe = _ensure_services(client, args.backend, args.frontend, out_dir)
            ws = _find_or_create_workspace(client, args.backend, str(repo), out_dir)
            index_status = _wait_indexed(client, args.backend, ws["id"], out_dir)
            analysis = _run_workspace_analysis(client, args.backend, ws["id"], out_dir)
            coverage_path = _make_simulated_coverage(repo, out_dir)
            coverage = _upload_and_analyze_coverage(
                client, args.backend, ws["id"], coverage_path, out_dir
            )
            validation = _validate_design(
                repo,
                coverage["upload"]["id"],
                coverage["detail"],
                out_dir,
            )
        except RuntimeError as exc:
            reason = str(exc)
            if any(token in reason for token in ("不可用", "不健康", "没有 active LLM", "索引超时")):
                return _block(reason, out_dir)
            return _fail(reason, out_dir)
        except Exception as exc:
            return _fail(f"未预期异常：{type(exc).__name__}: {exc}", out_dir)

    return _pass(
        out_dir,
        {
            "workspace_id": ws["id"],
            "task_id": analysis["task_id"],
            "coverage_analysis_id": coverage["upload"]["id"],
            "service_probe": service_probe,
            "index_status": index_status,
            "validation": validation,
            "out_dir": str(out_dir),
        },
    )


if __name__ == "__main__":
    sys.exit(main())
