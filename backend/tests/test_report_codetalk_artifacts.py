from dataclasses import dataclass
from pathlib import Path

import pytest

from app.llm.base import BaseLLMClient, LLMResponse
from app.prompts.templates import MODULE_MAP_PROMPT
from app.schemas.workspace_analysis import (
    AnalysisPlan,
    LLMLimits,
    ReportSpec,
    build_default_plan,
)
from app.services.report_generator import ReportGenerator, build_coverage_test_design_section


class CapturingLLM(BaseLLMClient):
    def __init__(self, content: str) -> None:
        self.content = content
        self.prompts: list[str] = []

    async def complete(
        self,
        messages: list[dict],
        max_tokens: int = 4096,
        temperature: float = 0.3,
    ) -> LLMResponse:
        self.prompts.append(messages[-1]["content"])
        return LLMResponse(
            content=self.content,
            model="fake",
            usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        )

    async def health_check(self) -> tuple[bool, str]:
        return True, "ok"


@dataclass
class FakeCard:
    title: str
    card_id: str = "card-1"
    source: str = "repo_search"
    file_path: str = "lib/log/log.c"
    symbol: str = "spdk_vlog"
    confidence: str = "high"
    snippet: str = "spdk_vlog calls free(ext_buf)"
    object_id: str = "obj-1"

    def to_markdown(self) -> str:
        return f"### {self.title}\n- `{self.file_path}` `{self.symbol}`"

    def to_dict(self) -> dict:
        return {
            "card_id": self.card_id,
            "object_id": self.object_id,
            "title": self.title,
            "source": self.source,
            "confidence": self.confidence,
            "file_path": self.file_path,
            "symbol": self.symbol,
            "snippet": self.snippet,
            "notes": [],
            "needs_verification": False,
        }


def _plan(template_id: str) -> AnalysisPlan:
    return AnalysisPlan(
        reports=[
            ReportSpec(
                id=template_id,
                title="Module Map",
                template_id=template_id,
                enabled=True,
            )
        ],
        llm_limits=LLMLimits(
            max_cards_per_report_section=2,
            max_output_chars_per_section=600,
            retry_empty_output=0,
        ),
    )


def test_default_plan_examples_are_black_gray_box_test_targets() -> None:
    plan = build_default_plan(has_requirements=False, seed_examples=True)
    examples = "\n".join(obj.text for obj in plan.analysis_objects)

    assert "external trigger" in examples
    assert "exception propagation" in examples
    assert "state/resource cleanup" in examples
    assert "boundary/concurrency/timeout" in examples
    assert plan.focus.security_risk is False


def test_plan_blueprints_do_not_delegate_layout_to_ai() -> None:
    instructions = "\n".join(
        section.get("instructions", "")
        for sections in ReportGenerator._section_blueprints().values()
        for section in sections
    )
    assert "Use a Markdown dependency table." not in instructions
    assert "Output SFMEA table:" not in instructions
    assert "map each requirement using columns:" not in instructions
    assert "CodeTalk will render" in instructions


@pytest.mark.asyncio
async def test_codetalk_adds_diagram_and_tables_when_ai_returns_prose(tmp_path: Path) -> None:
    llm = CapturingLLM(
        "AI_CONTENT " * 40
        + "This prose intentionally contains no markdown table and no mermaid block."
    )
    generator = ReportGenerator(llm, tmp_path, "task-artifacts")
    spec = _plan("module_map").reports[0]
    card = FakeCard(title="source evidence")
    analysis_units = [{"title": "Log output path", "object_ids": ["obj-1"], "cards": [card]}]

    entry = await generator._generate_plan_report(
        spec=spec,
        plan=_plan("module_map"),
        common_context={
            "pipeline_mode": "dual",
            "index_coverage": {},
            "active_materials": [],
            "gitnexus_available": True,
            "cgc_available": True,
        },
        analysis_units=analysis_units,
        evidence_cards=[card],
        sem=__import__("asyncio").Semaphore(1),
    )

    output = (tmp_path / entry["filename"]).read_text(encoding="utf-8")
    assert entry["status"] == "completed"
    assert "### CodeTalk Evidence Table" in output
    assert "### CodeTalk Diagram" in output
    assert "```mermaid" in output
    assert "| Evidence | Source | File/Symbol | Confidence |" in output


@pytest.mark.asyncio
async def test_plan_report_contains_traceability_failure_and_branch_appendix(tmp_path: Path) -> None:
    llm = CapturingLLM("AI_CONTENT " * 40)
    generator = ReportGenerator(llm, tmp_path, "task-report-assets")
    plan = _plan("test_design")
    spec = plan.reports[0]
    card = FakeCard(
        title="TLS receive failure branch",
        card_id="card-tls",
        file_path="lib/tls.c",
        symbol="tls_recv",
        snippet=(
            "int tls_recv(struct conn *c) {\n"
            "    int rc = SSL_read(c->ssl, c->buf, sizeof(c->buf));\n"
            "    if (rc <= 0) {\n"
            "        int err = SSL_get_error(c->ssl, rc);\n"
            "        c->state = CONN_CLOSED;\n"
            "        free(c->buf);\n"
            "        return -EIO;\n"
            "    }\n"
            "    return rc;\n"
            "}\n"
        ),
        object_id="obj-tls",
    )

    entry = await generator._generate_plan_report(
        spec=spec,
        plan=plan,
        common_context={
            "pipeline_mode": "llm_direct",
            "index_coverage": {},
            "active_materials": [],
            "gitnexus_available": False,
            "cgc_available": False,
            "analysis_objects": [
                {"id": "obj-tls", "text": "TLS receive failure handling", "kind": "flow", "priority": "high"},
                {"id": "obj-retry", "text": "TLS timeout retry policy", "kind": "flow", "priority": "medium"},
            ],
        },
        analysis_units=[
            {
                "id": "unit_1",
                "title": "TLS receive",
                "object_ids": ["obj-tls"],
                "cards": [card],
            }
        ],
        evidence_cards=[card],
        sem=__import__("asyncio").Semaphore(1),
    )

    output = (tmp_path / entry["filename"]).read_text(encoding="utf-8")
    assert "## 90 CodeTalk Traceability Artifacts" in output
    assert "### CodeTalk Claim-Evidence Map" in output
    assert "claim:obj-tls" in output
    assert "TLS timeout retry policy" in output
    assert "gap" in output
    assert "### CodeTalk Function Failure Matrix" in output
    assert "lib/tls.c::tls_recv" in output
    assert "SSL_get_error" in output
    assert "free(c->buf);" in output
    assert "### CodeTalk Branch Deep Dive" in output
    assert "if (rc <= 0)" in output
    assert "No graph evidence" in output


@pytest.mark.asyncio
async def test_section_prompt_delegates_layout_to_codetalk(tmp_path: Path) -> None:
    llm = CapturingLLM("AI_CONTENT " * 40)
    generator = ReportGenerator(llm, tmp_path, "task-prompt")
    plan = _plan("module_map")
    section = {
        "heading": "Unit grouping",
        "instructions": "Describe units.",
        "requires_mermaid": True,
        "requires_sfmea": True,
        "min_chars": 80,
    }

    await generator._render_section(
        spec=plan.reports[0],
        section=section,
        plan=plan,
        common_context={},
        analysis_units=[{"title": "Unit A", "object_ids": ["obj-1"], "cards": [FakeCard("c")]}],
        evidence_cards=[],
        section_idx=0,
        sem=__import__("asyncio").Semaphore(1),
    )

    assert llm.prompts
    prompt = llm.prompts[0]
    assert "CodeTalk will render Markdown tables, Mermaid diagrams, and SFMEA grids" in prompt
    assert "This section must include at least one Mermaid" not in prompt
    assert "This section must include an SFMEA table" not in prompt


@pytest.mark.asyncio
async def test_test_design_prompt_uses_developer_to_tester_contract(tmp_path: Path) -> None:
    llm = CapturingLLM("AI_CONTENT " * 40)
    generator = ReportGenerator(llm, tmp_path, "task-scenario")
    plan = _plan("test_design")
    section = ReportGenerator._section_blueprints()["test_design"][0]

    await generator._render_section(
        spec=plan.reports[0],
        section=section,
        plan=plan,
        common_context={},
        analysis_units=[{"title": "TLS handshake", "object_ids": ["obj-1"], "cards": [FakeCard("tls card")]}],
        evidence_cards=[],
        section_idx=0,
        sem=__import__("asyncio").Semaphore(1),
    )

    prompt = llm.prompts[0]
    assert "Developer-to-Tester Scenario Mode" in prompt
    assert "black-box trigger" in prompt
    assert "gray-box aid" in prompt
    assert "source evidence chain" in prompt
    assert "function failure matrix" in prompt
    assert "BranchFactCard" in prompt
    assert "ExternalEntryCard" in prompt
    assert "BlackBoxReadinessCard" in prompt
    assert "TestCaseDraft" in prompt
    assert "WhiteBoxLeakCheckResult" in prompt
    assert "black_box_ready" in prompt
    assert "black_box_hypothesis" in prompt
    assert "gray_box_required" in prompt
    assert "Test execution area" in prompt
    assert "Gray-box aid area" in prompt
    assert "Evidence area" in prompt
    assert "branch condition" in prompt
    assert "expected result" in prompt
    assert "observable signal" in prompt
    assert "verification gap" in prompt


@pytest.mark.asyncio
async def test_test_design_prompt_marks_missing_evidence_as_verification_gap(
    tmp_path: Path,
) -> None:
    llm = CapturingLLM("AI_CONTENT " * 40)
    generator = ReportGenerator(llm, tmp_path, "task-missing-evidence")
    plan = _plan("test_design")
    section = ReportGenerator._section_blueprints()["test_design"][0]

    await generator._render_section(
        spec=plan.reports[0],
        section=section,
        plan=plan,
        common_context={},
        analysis_units=[],
        evidence_cards=[],
        section_idx=0,
        sem=__import__("asyncio").Semaphore(1),
    )

    prompt = llm.prompts[0]
    assert "When evidence is missing" in prompt
    assert "gray-box required" in prompt
    assert "do not invent a confident black-box path" in prompt
    assert "待验证" in prompt


def test_coverage_test_design_section_splits_execution_gray_and_evidence() -> None:
    section = build_coverage_test_design_section({
        "version": "coverage-test-design-v1",
        "summary": {
            "uncovered_function_count": 1,
            "uncovered_branch_count": 0,
            "black_box_ready_count": 1,
            "black_box_hypothesis_count": 0,
            "gray_box_required_count": 0,
            "white_box_lint_failed_count": 0,
            "high_risk_count": 1,
            "workspace_bound": True,
            "tool_status": {"gitnexus": "available"},
        },
        "gaps": [{
            "kind": "function",
            "function_name": "nvme_tcp_generate_tls_credentials",
            "file_path": "lib/nvme/nvme_tcp.c",
            "line_start": 2758,
            "hit_count": 0,
            "risk_level": "high",
            "confidence": "high",
            "gray_box_required": False,
            "branch_fact_card": {
                "uncovered_location": "lib/nvme/nvme_tcp.c:2758-2761",
                "branch_conditions": ["psk_retained_hash == NVME_TCP_HASH_ALGORITHM_NONE"],
                "source_evidence": ["lib/nvme/nvme_tcp.c:2758 hit_count=0"],
                "possible_observable_signals": ["connect result", "logs"],
            },
            "external_entry_card": {
                "has_external_entry": True,
                "entries": [{"entry_kind": "cli", "entry_label": "spdk_nvme_perf --psk-path"}],
                "missing_evidence": [],
            },
            "black_box_readiness": {
                "case_type": "black_box_ready",
                "rationale": "external entry, input construction, and observable signals are all present",
            },
            "white_box_leak_check": {"passed": True, "findings": [], "action": "pass"},
            "gray_box": {
                "required": False,
                "technique": "trace",
                "scheme": "Observe whether retained PSK derivation is skipped.",
                "injection_points": ["nvme_tcp_derive_retained_psk"],
            },
            "test_case_drafts": [{
                "case_type": "black_box_ready",
                "test_execution": {
                    "title": "Use no-hash NVMe TLS PSK to establish a secure channel",
                    "external_trigger": "Drive spdk_nvme_perf --psk-path through its public interface.",
                    "preconditions": "NVMe/TCP target secure channel is enabled.",
                    "inputs": "Valid no-hash PSK file.",
                    "steps": ["Connect", "Run discovery", "Run one simple I/O"],
                    "expected": "Connection and I/O succeed without plaintext downgrade.",
                    "observable_signals": ["exit code", "I/O completion", "logs"],
                },
                "gray_box_aid": {},
                "evidence_section": {},
                "verification_gaps": [],
            }],
            "evidence_gaps": [],
        }],
        "warnings": [],
    })

    assert "Test execution area" in section
    assert "Gray-box aid area" in section
    assert "Evidence area" in section
    assert "black_box_ready" in section
    assert "White-box leak lint: pass" in section
    assert "psk_retained_hash == NVME_TCP_HASH_ALGORITHM_NONE" in section


@pytest.mark.asyncio
async def test_legacy_report_prompts_and_outputs_use_codetalk_layout(tmp_path: Path) -> None:
    llm = CapturingLLM(
        "AI legacy report prose. "
        "This response intentionally has no markdown table and no mermaid block."
    )
    generator = ReportGenerator(llm, tmp_path, "legacy-artifacts")
    prompt = MODULE_MAP_PROMPT.format(
        project_overview="project overview",
        module_summaries="module summaries",
        inter_module_deps="module deps",
    )

    await generator._generate_report(report_type="module_map", prompt=prompt)

    assert llm.prompts
    assert "CodeTalk layout ownership" in llm.prompts[0]
    assert "```mermaid" not in llm.prompts[0]
    assert "| ---" not in llm.prompts[0]

    output = (tmp_path / generator.generated_files[0]).read_text(encoding="utf-8")
    assert "### CodeTalk Evidence Table" in output
    assert "### CodeTalk Diagram" in output
    assert "```mermaid" in output
    assert "Report structure warning" not in output
