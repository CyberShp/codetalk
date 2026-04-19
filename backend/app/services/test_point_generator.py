"""Test point generation pipeline.

Orchestrates: Joern (CPG) + Semgrep (rules) + GitNexus (graph) + DeepWiki (LLM)
to produce black-box test point descriptions.

IRON LAW: This service is pure orchestration. Each tool call goes through
its adapter. No analysis logic here — we only assemble tool outputs and
send them to the LLM for test-point synthesis.
"""

import json
import logging
from typing import Any

import httpx

from app.adapters import create_adapter
from app.adapters.base import AnalysisRequest
from app.adapters.gitnexus import GitNexusAdapter
from app.adapters.joern import JoernAdapter
from app.adapters.semgrep import SemgrepAdapter
from app.config import settings

logger = logging.getLogger(__name__)


TEST_POINT_PROMPT = """你是一个资深测试架构师。以下是静态分析工具从代码中提取的分析数据。
请以{perspective}测试的视角，为每个发现生成测试点描述。

要求：
1. 不要提及代码实现细节（函数名、变量名），只描述业务行为
2. 每个测试点必须包含以下字段名（不要用其他名称）：
   - scenario: 场景描述（用户可理解的业务语言）
   - input_conditions: 输入条件（具体的测试数据或操作步骤）
   - expected_behavior: 预期行为（正常情况应该怎样）
   - risk_scenario: 风险场景（异常情况会怎样）
   - boundary_values: 边界值（如果存在数值条件，列出临界值；无则为 null）
   - risk_level: 风险等级，仅限 "high" / "medium" / "low"
   - category: 测试类别（如 "injection", "error_handling", "boundary", "auth", "data_flow" 等）
3. 特别关注：
   - 条件翻转点（什么条件下行为会突变）
   - 级联故障（一个失败如何影响后续流程）
   - 并发/时序问题（如果涉及异步或多步骤）

[控制流分支]
{branches}

[异常处理路径]
{errors}

[边界值条件]
{boundaries}

[安全扫描发现]
{findings}

[调用链上下文]
{call_chain}

[所属业务流程]
{process_flow}

请输出 JSON 数组，每个元素是一个测试点对象。只输出 JSON，不要其他文字。"""


async def generate_test_points(
    repo_path: str,
    target: str | None = None,
    perspective: str = "black_box",
    llm_config: dict | None = None,
) -> list[dict]:
    """
    Pipeline:
    1. Joern: Get branches, throws, catches, boundary values for target
    2. Semgrep: Get relevant findings for target files
    3. GitNexus: Get call chain and process context
    4. Assemble structured analysis data
    5. Send to DeepWiki Chat with specialized prompt
    6. Parse LLM output into structured test points
    """
    joern: JoernAdapter = create_adapter("joern")  # type: ignore[assignment]
    semgrep: SemgrepAdapter = create_adapter("semgrep")  # type: ignore[assignment]

    # Step 1: Joern analysis
    branches: Any = []
    errors: Any = []
    boundaries: Any = []

    try:
        await joern.prepare(AnalysisRequest(repo_local_path=repo_path))

        if target:
            branches = await joern.function_branches(target)
            errors = await joern.error_paths(target)
            boundaries = await joern.boundary_values(target)
        else:
            analysis = await joern.analyze(
                AnalysisRequest(repo_local_path=repo_path)
            )
            cpg = analysis.data.get("cpg_analysis", {})
            branches = cpg.get("control_structures", [])
            errors = cpg.get("throw_points", [])
            boundaries = cpg.get("comparison_operators", [])
    except Exception as exc:
        logger.warning("Joern analysis failed (non-fatal): %s", exc)
        branches = [{"error": f"Joern unavailable: {exc}"}]
    finally:
        try:
            await joern.cleanup(AnalysisRequest(repo_local_path=repo_path))
        except Exception:
            pass

    # Step 2: Semgrep findings
    relevant_findings: list[dict] = []
    try:
        semgrep_result = await semgrep.analyze(
            AnalysisRequest(repo_local_path=repo_path)
        )
        all_findings = semgrep_result.data.get("findings", [])
        if target:
            relevant_findings = [
                f for f in all_findings
                if target.lower() in (f.get("path", "") + f.get("check_id", "")).lower()
            ]
        else:
            relevant_findings = all_findings
    except Exception as exc:
        logger.warning("Semgrep analysis failed (non-fatal): %s", exc)
        relevant_findings = [{"error": f"Semgrep unavailable: {exc}"}]

    # Step 3: GitNexus context (best-effort)
    gitnexus_context = await _get_gitnexus_context(repo_path, target)

    # Step 4: Assemble prompt
    prompt = TEST_POINT_PROMPT.format(
        perspective=perspective,
        branches=_format_for_prompt(branches, max_items=30),
        errors=_format_for_prompt(errors, max_items=20),
        boundaries=_format_for_prompt(boundaries, max_items=20),
        findings=_format_for_prompt(relevant_findings, max_items=20),
        call_chain=gitnexus_context.get("call_chain", "N/A"),
        process_flow=gitnexus_context.get("process", "N/A"),
    )

    # Step 5: Call DeepWiki LLM
    test_points_raw = await _call_deepwiki_chat(prompt, repo_path, llm_config)

    # Step 6: Parse into structured format and normalize field names
    return _parse_test_points(test_points_raw, target)


async def _get_gitnexus_context(
    repo_path: str, target: str | None
) -> dict[str, str]:
    """Fetch call chain and process context from GitNexus (best-effort)."""
    gitnexus: GitNexusAdapter = create_adapter("gitnexus")  # type: ignore[assignment]
    request = AnalysisRequest(repo_local_path=repo_path)
    try:
        await gitnexus.prepare(request)

        process_flow = "N/A"
        if not target:
            analysis = await gitnexus.analyze(request)
            processes = analysis.data.get("graph", {}).get("processes", [])
            if processes:
                process_flow = json.dumps(processes[:10], ensure_ascii=False)

        async with httpx.AsyncClient(
            base_url=settings.gitnexus_base_url,
            timeout=30,
        ) as client:
            if target:
                resp = await client.post(
                    "/api/search",
                    params={"repo": gitnexus.current_repo_name},
                    json={
                        "query": target,
                        "mode": "hybrid",
                        "limit": 10,
                        "enrich": True,
                    },
                )
                if resp.status_code == 200:
                    data = resp.json()
                    return {
                        "call_chain": json.dumps(
                            data.get("results", [])[:10], ensure_ascii=False
                        ),
                        "process": process_flow,
                    }
            elif process_flow != "N/A":
                return {"call_chain": "N/A", "process": process_flow}
    except Exception as exc:
        logger.debug("GitNexus context unavailable: %s", exc)
    finally:
        try:
            await gitnexus.cleanup(request)
        except Exception:
            pass

    return {"call_chain": "N/A", "process": "N/A"}


async def _call_deepwiki_chat(
    prompt: str,
    repo_path: str,
    llm_config: dict | None = None,
) -> str:
    """Send assembled prompt to DeepWiki for LLM synthesis.

    Uses /chat/completions/stream (DeepWiki's only chat endpoint)
    and collects the full streamed response.
    """
    payload: dict[str, Any] = {
        "repo_url": repo_path,
        "type": "local",
        "messages": [{"role": "user", "content": prompt}],
        "language": "zh",
    }
    if llm_config:
        payload.update(llm_config)

    content = ""
    async with httpx.AsyncClient(
        base_url=settings.deepwiki_base_url,
        timeout=httpx.Timeout(300, connect=10),
    ) as client:
        async with client.stream(
            "POST",
            "/chat/completions/stream",
            json=payload,
            timeout=300,
        ) as response:
            response.raise_for_status()
            async for chunk in response.aiter_text():
                content += chunk
    return content


def _format_for_prompt(data: Any, max_items: int = 20) -> str:
    """Format analysis data for inclusion in the LLM prompt.

    Truncates to avoid exceeding context limits.
    """
    if isinstance(data, list):
        truncated = data[:max_items]
        result = json.dumps(truncated, ensure_ascii=False, indent=2)
        if len(data) > max_items:
            result += f"\n... ({len(data) - max_items} more items truncated)"
        return result
    if isinstance(data, dict):
        return json.dumps(data, ensure_ascii=False, indent=2)
    return str(data)


def _parse_test_points(raw: str, target: str | None = None) -> list[dict]:
    """Parse LLM output into structured test point objects.

    The LLM is prompted to output pure JSON, but may include
    markdown fences or extra text — handle gracefully.
    Normalizes field names to match frontend TestPoint type.
    """
    if not raw:
        return []

    # Strip markdown code fences if present
    text = raw.strip()
    if text.startswith("```"):
        # Remove opening fence (```json or ```)
        first_newline = text.index("\n")
        text = text[first_newline + 1 :]
    if text.endswith("```"):
        text = text[: -3]
    text = text.strip()

    try:
        parsed = json.loads(text)
        items = parsed if isinstance(parsed, list) else [parsed] if isinstance(parsed, dict) else []
        return [_normalize_test_point(tp, i, target) for i, tp in enumerate(items)]
    except json.JSONDecodeError:
        logger.warning("Failed to parse LLM output as JSON, returning raw")
        return [{"raw_output": raw, "parse_error": True}]


# Field name aliases: LLM output → frontend TestPoint type
_FIELD_MAP = {
    "input": "input_conditions",
    "expected": "expected_behavior",
    "risk": "risk_scenario",
    "severity": "risk_level",
}


def _normalize_test_point(tp: dict, index: int, target: str | None) -> dict:
    """Normalize LLM-generated test point to match frontend TestPoint schema.

    Handles field name variations (input→input_conditions, etc.) and
    ensures every required field is present. Pure format conversion.
    """
    normalized: dict = {}

    # Apply field aliases
    for key, val in tp.items():
        canonical = _FIELD_MAP.get(key, key)
        normalized[canonical] = val

    # Ensure required fields with defaults
    normalized.setdefault("id", f"tp-{index + 1:03d}")
    normalized.setdefault("scenario", "")
    normalized.setdefault("input_conditions", "")
    normalized.setdefault("expected_behavior", "")
    normalized.setdefault("risk_scenario", "")
    normalized.setdefault("boundary_values", None)
    normalized.setdefault("risk_level", "medium")
    normalized.setdefault("source_location", target)
    normalized.setdefault("category", "general")

    # Validate risk_level
    if normalized["risk_level"] not in ("high", "medium", "low"):
        normalized["risk_level"] = "medium"

    return normalized
