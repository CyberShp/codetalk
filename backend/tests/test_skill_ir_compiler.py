"""RED contract for F014 Task 4 deterministic Skill IR compilation."""

from __future__ import annotations

import copy
import hashlib
import importlib
import json
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource


CONTRACT_FIXTURES = Path(__file__).parent / "fixtures" / "skills" / "contracts" / "positive"
V24_FIXTURES = Path(__file__).parent / "fixtures" / "skills" / "codetalks-v2.4"
SCHEMA_DIR = Path(__file__).parents[1] / "app" / "schemas" / "skills"


def _compiler() -> ModuleType:
    try:
        return importlib.import_module("app.services.skill_ir_compiler")
    except ModuleNotFoundError as exc:
        if exc.name == "app.services.skill_ir_compiler":
            pytest.fail("RED: app.services.skill_ir_compiler has not been implemented")
        raise


def _skill_document() -> dict[str, Any]:
    return json.loads((CONTRACT_FIXTURES / "codetalk-skill-v1.json").read_text(encoding="utf-8"))


def _write_source_tree(root: Path, document: dict[str, Any]) -> Path:
    skill_path = root / "skill.json"
    skill_path.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for path in {
        *(step["instruction_path"] for step in document["steps"]),
        *(script["path"] for script in document["scripts"]),
        *(rule["instruction_path"] for rule in document["core_rules"]),
    }:
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f"# {path}\n", encoding="utf-8")
    return skill_path


def _digest(path: Path) -> str:
    return f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"


def _ir_schema_errors(document: dict[str, Any]) -> list[Any]:
    resources = [Resource.from_contents(json.loads(path.read_text(encoding="utf-8"))) for path in SCHEMA_DIR.glob("*.schema.json")]
    registry = Registry().with_resources((resource.id(), resource) for resource in resources)
    schema = json.loads((SCHEMA_DIR / "skill-ir-v1.schema.json").read_text(encoding="utf-8"))
    return list(Draft202012Validator(schema, registry=registry, format_checker=FormatChecker()).iter_errors(document))


def test_compile_skill_ir_rejects_unvalidated_input(tmp_path: Path) -> None:
    compiler = _compiler()
    document = _skill_document()
    document["steps"][1]["depends_on"] = ["step.missing"]
    skill_path = _write_source_tree(tmp_path, document)

    with pytest.raises(compiler.SkillPackageValidationError) as caught:
        compiler.compile_skill_ir(document, source_root=tmp_path, source_path=skill_path)

    assert [(issue.code, issue.path) for issue in caught.value.issues] == [("unknown_step_dependency", "steps[1].depends_on[0]")]


def test_compile_skill_ir_is_deterministic_and_schema_shaped(tmp_path: Path) -> None:
    compiler = _compiler()
    document = _skill_document()
    skill_path = _write_source_tree(tmp_path, document)

    first = compiler.compile_skill_ir(copy.deepcopy(document), source_root=tmp_path, source_path=skill_path)
    second = compiler.compile_skill_ir(copy.deepcopy(document), source_root=tmp_path, source_path=skill_path)

    assert first == second
    assert _ir_schema_errors(first) == []
    assert first["schema_version"] == "skill-ir-v1"
    assert first["skill_id"] == document["skill_id"]
    assert first["topological_order"] == ["step.collect", "step.analyze"]
    assert first["content_digest"].startswith("sha256:")
    assert len(first["content_digest"]) == len("sha256:") + 64
    assert first["source_file_digests"] == [
        {"path": "scripts/analyze.py", "digest": _digest(tmp_path / "scripts/analyze.py")},
        {"path": "skill.json", "digest": _digest(skill_path)},
        {"path": "指引/分析.md", "digest": _digest(tmp_path / "指引/分析.md")},
        {"path": "指引/收集.md", "digest": _digest(tmp_path / "指引/收集.md")},
        {"path": "规则/安全.md", "digest": _digest(tmp_path / "规则/安全.md")},
    ]


def test_compile_skill_ir_digest_changes_when_unreferenced_source_changes(tmp_path: Path) -> None:
    compiler = _compiler()
    document = _skill_document()
    skill_path = _write_source_tree(tmp_path, document)
    extra = tmp_path / "references" / "extra.md"
    extra.parent.mkdir(parents=True, exist_ok=True)
    extra.write_text("# extra\n", encoding="utf-8")

    before = compiler.compile_skill_ir(copy.deepcopy(document), source_root=tmp_path, source_path=skill_path)
    extra.write_text("# changed extra\n", encoding="utf-8")
    after = compiler.compile_skill_ir(copy.deepcopy(document), source_root=tmp_path, source_path=skill_path)

    assert "references/extra.md" in {row["path"] for row in before["source_file_digests"]}
    assert before["content_digest"] != after["content_digest"]


def test_compile_skill_ir_rejects_document_that_differs_from_source_bytes(tmp_path: Path) -> None:
    compiler = _compiler()
    document = _skill_document()
    skill_path = _write_source_tree(tmp_path, document)
    mutated = copy.deepcopy(document)
    mutated["steps"][0]["title"] = "Changed only in memory"

    with pytest.raises(compiler.SkillPackageValidationError) as caught:
        compiler.compile_skill_ir(mutated, source_root=tmp_path, source_path=skill_path)

    assert [(issue.code, issue.path) for issue in caught.value.issues] == [("source_document_mismatch", "source_path")]


def _v24_manifest() -> dict[str, Any]:
    required_by_step = [
        ["活文档/01-范围与任务契约.md"],
        ["活文档/02-输入材料消费记录.md", "内部索引/运行计划.json", "内部索引/输入材料索引.json", "活文档/覆盖门禁/步骤02-覆盖门禁.md"],
        ["活文档/03-入口清单与说明.md", "活文档/04-流程清单与说明.md", "活文档/05-状态清单与说明.md", "活文档/06-资源清单与说明.md", "活文档/07-分析模型适用性.md", "活文档/覆盖门禁/步骤03-覆盖门禁.md"],
        ["活文档/08-分支处置与解释.md", "活文档/09-状态转换处置与解释.md", "活文档/10-资源生命周期处置与解释.md", "活文档/11-异常传播链与解释.md", "活文档/12-开发讲解覆盖台账.md", "活文档/覆盖门禁/步骤04-覆盖门禁.md"],
        ["活文档/13-场景候选池与推导说明.md", "活文档/14-风险点清单与因果说明.md", "活文档/覆盖门禁/步骤05-覆盖门禁.md"],
        ["活文档/15-SFMEA分析.md", "活文档/16-黑盒控制与观测映射.md", "活文档/17-测试设计依据.md", "活文档/覆盖门禁/步骤06-覆盖门禁.md"],
        ["活文档/18-测试追溯矩阵.md", "活文档/覆盖门禁/步骤07-覆盖门禁.md"],
        ["活文档/19-独立审查报告.md", "活文档/覆盖门禁/最终覆盖门禁.md", "内部索引/独立审查状态.json"],
        [
            "正式输出/开发给测试讲代码.md",
            "正式输出/流程分支状态资源与异常传播.md",
            "正式输出/风险点与SFMEA.md",
            "正式输出/黑盒测试场景.md",
            "正式输出/黑盒测试流程.md",
            "正式输出/黑盒测试用例.md",
            "正式输出/覆盖审计与分析限制.md",
            "正式输出/完整分析报告.md",
        ],
    ]
    steps = []
    for index, required in enumerate(required_by_step, start=1):
        step_id = f"{index:02d}"
        step = {
            "id": step_id,
            "file": f"steps/{step_id}-step.md",
            "required": required,
            "markdown_min_chars": 600 + index,
        }
        if index == 4:
            step["requires_glob"] = ["活文档/流程讲解/流程-*.md"]
            step["flow_narrative_validation"] = True
        steps.append(step)
    return {
        "version": "2.4",
        "required_core_rules": {
            "path-fidelity": "references/path-fidelity.md",
            "evidence-consumption": "references/evidence-consumption.md",
            "narrative-first": "references/markdown-narrative-first.md",
        },
        "evidence_allowed_status": ["parsed", "partially_parsed", "blocked", "out_of_scope", "unreadable"],
        "coverage_allowed_outcomes": ["analyzed", "covered_by_other", "not_applicable", "blocked", "need_verify", "truncated"],
        "flow_required_headings": [
            "## 一、这里是干什么的",
            "## 二、外部怎么触发",
        ],
        "flow_key_narrative_headings": [
            "## 一、这里是干什么的",
        ],
        "steps": steps,
    }


def _write_v24_tree(root: Path) -> None:
    manifest = _v24_manifest()
    (root / "workflow-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (root / "workflows").mkdir(parents=True)
    for scenario in ["custom", "issue-regression", "module-analysis", "root-cause", "special-risk"]:
        (root / "workflows" / f"{scenario}.md").write_text(f"# {scenario}\n", encoding="utf-8")
    for path in [
        "SKILL.md",
        "scripts/run_guard.py",
        "checklists/judge-checklist.md",
        "references/tool-routing.md",
        "templates/开发给测试讲代码模板.md",
        *manifest["required_core_rules"].values(),
        *(step["file"] for step in manifest["steps"]),
    ]:
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f"# {path}\n", encoding="utf-8")


def test_compile_codetalks_v24_module_analysis_retains_golden_summary(tmp_path: Path) -> None:
    compiler = _compiler()
    _write_v24_tree(tmp_path)
    expected = json.loads((V24_FIXTURES / "expected-ir-summary.json").read_text(encoding="utf-8"))

    ir = compiler.compile_codetalks_v24_skill(tmp_path, source_scenario_id="module-analysis")

    assert _ir_schema_errors(ir) == []
    assert ir["skill_id"] == "skill.codetalks-module-full-analysis"
    assert ir["inputs"][0]["label"] == "Source materials for workflows/module-analysis.md"
    assert ir["steps"][0]["title"] == "workflows/module-analysis.md: Codetalks step 01"
    assert ir["steps"][0]["instruction_path"] == "steps/01-step.md"
    assert ir["selected_workflow_path"] == "workflows/module-analysis.md"
    assert len(ir["steps"]) == expected["module_analysis"]["step_count"]
    assert [step["step_id"] for step in ir["steps"]] == [f"step.step-{step_id}" for step_id in expected["module_analysis"]["step_ids"]]
    assert len(ir["core_rules"]) == expected["module_analysis"]["core_rule_count"]
    assert [rule["rule_id"] for rule in ir["core_rules"]] == [f"rule.{rule_id}" for rule_id in expected["module_analysis"]["core_rule_ids"]]
    assert sum(1 for artifact in ir["artifacts"] if artifact["required"]) == expected["module_analysis"]["required_artifact_count"]
    assert [delivery["delivery_id"] for delivery in ir["deliveries"]] == [
        f"delivery.{output_id}" for output_id in expected["module_analysis"]["formal_output_ids"]
    ]
    assert ir["scripts"] == [{
        "script_id": "script.run_guard",
        "path": "scripts/run_guard.py",
        "timeout_seconds": 60,
        "working_directory": ".",
        "allowed_exit_codes": [0],
        "log_artifact_ids": ["artifact.internal-run-state"],
        "write_scope": ["活文档", "内部索引", "正式输出"],
    }]
    assert ir["judge"] == {
        "required": True,
        "isolated_session": True,
        "artifact_ids": ["artifact.formal-complete-analysis-report"],
    }
    assert ir["topological_order"] == [f"step.step-{step_id}" for step_id in expected["module_analysis"]["step_ids"]]
    assert "references/tool-routing.md" in {row["path"] for row in ir["source_file_digests"]}
    assert "templates/开发给测试讲代码模板.md" in {row["path"] for row in ir["source_file_digests"]}
    step_04_gate = ir["steps"][3]["completion_gate"]
    assert step_04_gate["requires_glob"] == ["活文档/流程讲解/流程-*.md"]
    assert step_04_gate["flow_narrative_validation"] is True
    assert step_04_gate["flow_required_headings"][0] == "## 一、这里是干什么的"
    assert "blocked" in step_04_gate["coverage_allowed_outcomes"]


def test_compile_codetalks_v24_digest_changes_when_unreferenced_source_changes(tmp_path: Path) -> None:
    compiler = _compiler()
    _write_v24_tree(tmp_path)

    before = compiler.compile_codetalks_v24_skill(tmp_path, source_scenario_id="module-analysis")
    (tmp_path / "references" / "tool-routing.md").write_text("# changed routing\n", encoding="utf-8")
    after = compiler.compile_codetalks_v24_skill(tmp_path, source_scenario_id="module-analysis")

    assert before["content_digest"] != after["content_digest"]


def test_compile_codetalks_v24_rejects_non_declared_scenario(tmp_path: Path) -> None:
    compiler = _compiler()
    _write_v24_tree(tmp_path)
    (tmp_path / "workflows" / "module-analysis.md").unlink()

    with pytest.raises(compiler.SkillPackageValidationError) as caught:
        compiler.compile_codetalks_v24_skill(tmp_path, source_scenario_id="module-analysis")

    assert [(issue.code, issue.path) for issue in caught.value.issues] == [("missing_source_file", "workflows/module-analysis.md")]


def test_compile_codetalks_v24_rejects_manifest_with_duplicate_step_ids(tmp_path: Path) -> None:
    compiler = _compiler()
    _write_v24_tree(tmp_path)
    manifest_path = tmp_path / "workflow-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["steps"][1]["id"] = manifest["steps"][0]["id"]
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    with pytest.raises(compiler.SkillPackageValidationError) as caught:
        compiler.compile_codetalks_v24_skill(tmp_path, source_scenario_id="module-analysis")

    assert [(issue.code, issue.path) for issue in caught.value.issues] == [("duplicate_id", "steps[1].step_id")]


def test_compile_codetalks_v24_rejects_manifest_with_schema_invalid_step_id(tmp_path: Path) -> None:
    compiler = _compiler()
    _write_v24_tree(tmp_path)
    manifest_path = tmp_path / "workflow-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["steps"][0]["id"] = "01/invalid"
    manifest["steps"][0]["file"] = "steps/01-invalid.md"
    (tmp_path / "steps" / "01-invalid.md").write_text("# invalid\n", encoding="utf-8")
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    with pytest.raises(compiler.SkillPackageValidationError) as caught:
        compiler.compile_codetalks_v24_skill(tmp_path, source_scenario_id="module-analysis")

    assert [(issue.code, issue.path) for issue in caught.value.issues] == [("schema_error", "steps.0.step_id")]


def test_compile_codetalks_v24_rejects_module_analysis_extra_step(tmp_path: Path) -> None:
    compiler = _compiler()
    _write_v24_tree(tmp_path)
    manifest_path = tmp_path / "workflow-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["steps"].append({
        "id": "10",
        "file": "steps/10-extra.md",
        "required": [],
        "markdown_min_chars": 1,
    })
    (tmp_path / "steps" / "10-extra.md").write_text("# extra\n", encoding="utf-8")
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    with pytest.raises(compiler.SkillPackageValidationError) as caught:
        compiler.compile_codetalks_v24_skill(tmp_path, source_scenario_id="module-analysis")

    assert [(issue.code, issue.path) for issue in caught.value.issues] == [
        ("codetalks_step_set_mismatch", "workflow-manifest.json.steps")
    ]


def test_compile_codetalks_v24_rejects_module_analysis_missing_core_rule(tmp_path: Path) -> None:
    compiler = _compiler()
    _write_v24_tree(tmp_path)
    manifest_path = tmp_path / "workflow-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    del manifest["required_core_rules"]["narrative-first"]
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    with pytest.raises(compiler.SkillPackageValidationError) as caught:
        compiler.compile_codetalks_v24_skill(tmp_path, source_scenario_id="module-analysis")

    assert [(issue.code, issue.path) for issue in caught.value.issues] == [
        ("codetalks_core_rule_set_mismatch", "workflow-manifest.json.required_core_rules")
    ]


def test_compile_codetalks_v24_rejects_module_analysis_replaced_required_artifact(tmp_path: Path) -> None:
    compiler = _compiler()
    _write_v24_tree(tmp_path)
    manifest_path = tmp_path / "workflow-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["steps"][1]["required"][0] = "活文档/unexpected-replacement.md"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    with pytest.raises(compiler.SkillPackageValidationError) as caught:
        compiler.compile_codetalks_v24_skill(tmp_path, source_scenario_id="module-analysis")

    assert [(issue.code, issue.path) for issue in caught.value.issues] == [
        ("codetalks_required_artifact_set_mismatch", "workflow-manifest.json.steps")
    ]


def test_compile_codetalks_v24_rejects_module_analysis_missing_required_artifact(tmp_path: Path) -> None:
    compiler = _compiler()
    _write_v24_tree(tmp_path)
    manifest_path = tmp_path / "workflow-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["steps"][1]["required"].pop()
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    with pytest.raises(compiler.SkillPackageValidationError) as caught:
        compiler.compile_codetalks_v24_skill(tmp_path, source_scenario_id="module-analysis")

    assert [(issue.code, issue.path) for issue in caught.value.issues] == [("codetalks_required_artifact_set_mismatch", "workflow-manifest.json.steps")]


def test_compile_codetalks_v24_rejects_module_analysis_required_artifact_wrong_step(tmp_path: Path) -> None:
    compiler = _compiler()
    _write_v24_tree(tmp_path)
    manifest_path = tmp_path / "workflow-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    judge_state = "内部索引/独立审查状态.json"
    complete_report = "正式输出/完整分析报告.md"
    manifest["steps"][7]["required"].remove(judge_state)
    manifest["steps"][8]["required"].remove(complete_report)
    manifest["steps"][7]["required"].append(complete_report)
    manifest["steps"][8]["required"].append(judge_state)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    with pytest.raises(compiler.SkillPackageValidationError) as caught:
        compiler.compile_codetalks_v24_skill(tmp_path, source_scenario_id="module-analysis")

    assert [(issue.code, issue.path) for issue in caught.value.issues] == [
        ("codetalks_required_artifact_step_mismatch", "workflow-manifest.json.steps[7].required")
    ]


def test_compile_codetalks_v24_rejects_module_analysis_missing_formal_output(tmp_path: Path) -> None:
    compiler = _compiler()
    _write_v24_tree(tmp_path)
    manifest_path = tmp_path / "workflow-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["steps"][8]["required"].remove("正式输出/黑盒测试用例.md")
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    with pytest.raises(compiler.SkillPackageValidationError) as caught:
        compiler.compile_codetalks_v24_skill(tmp_path, source_scenario_id="module-analysis")

    assert [(issue.code, issue.path) for issue in caught.value.issues] == [
        ("codetalks_formal_output_set_mismatch", "workflow-manifest.json.steps[8].required")
    ]


def test_compile_codetalks_v24_rejects_manifest_missing_required_field(tmp_path: Path) -> None:
    compiler = _compiler()
    _write_v24_tree(tmp_path)
    manifest_path = tmp_path / "workflow-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    del manifest["steps"][0]["markdown_min_chars"]
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    with pytest.raises(compiler.SkillPackageValidationError) as caught:
        compiler.compile_codetalks_v24_skill(tmp_path, source_scenario_id="module-analysis")

    assert [(issue.code, issue.path) for issue in caught.value.issues] == [
        ("missing_manifest_field", "workflow-manifest.json.steps[0].markdown_min_chars")
    ]


@pytest.mark.parametrize(
    ("field", "path"),
    [
        ("evidence_allowed_status", "workflow-manifest.json.evidence_allowed_status"),
        ("coverage_allowed_outcomes", "workflow-manifest.json.coverage_allowed_outcomes"),
        ("flow_required_headings", "workflow-manifest.json.flow_required_headings"),
        ("flow_key_narrative_headings", "workflow-manifest.json.flow_key_narrative_headings"),
    ],
)
def test_compile_codetalks_v24_rejects_manifest_missing_run_guard_field(tmp_path: Path, field: str, path: str) -> None:
    compiler = _compiler()
    _write_v24_tree(tmp_path)
    manifest_path = tmp_path / "workflow-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest[field] = ["ok"]
    del manifest[field]
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    with pytest.raises(compiler.SkillPackageValidationError) as caught:
        compiler.compile_codetalks_v24_skill(tmp_path, source_scenario_id="module-analysis")

    assert [(issue.code, issue.path) for issue in caught.value.issues] == [("missing_manifest_field", path)]


@pytest.mark.parametrize(
    ("field", "path"),
    [
        ("requires_glob", "workflow-manifest.json.steps[3].requires_glob"),
        ("flow_narrative_validation", "workflow-manifest.json.steps[3].flow_narrative_validation"),
    ],
)
def test_compile_codetalks_v24_rejects_manifest_missing_step_04_gate_field(tmp_path: Path, field: str, path: str) -> None:
    compiler = _compiler()
    _write_v24_tree(tmp_path)
    manifest_path = tmp_path / "workflow-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    del manifest["steps"][3][field]
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    with pytest.raises(compiler.SkillPackageValidationError) as caught:
        compiler.compile_codetalks_v24_skill(tmp_path, source_scenario_id="module-analysis")

    assert [(issue.code, issue.path) for issue in caught.value.issues] == [("missing_manifest_field", path)]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("requires_glob", []),
        ("flow_narrative_validation", False),
    ],
)
def test_compile_codetalks_v24_rejects_step_04_false_or_empty_gate_fields(tmp_path: Path, field: str, value: Any) -> None:
    compiler = _compiler()
    _write_v24_tree(tmp_path)
    manifest_path = tmp_path / "workflow-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["steps"][3][field] = value
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    with pytest.raises(compiler.SkillPackageValidationError) as caught:
        compiler.compile_codetalks_v24_skill(tmp_path, source_scenario_id="module-analysis")

    assert caught.value.issues


def test_compile_codetalks_v24_binds_selected_workflow_into_terminal_ir(tmp_path: Path) -> None:
    compiler = _compiler()
    _write_v24_tree(tmp_path)

    module_ir = compiler.compile_codetalks_v24_skill(tmp_path, source_scenario_id="module-analysis")
    root_cause_ir = compiler.compile_codetalks_v24_skill(tmp_path, source_scenario_id="root-cause")

    module_unsigned = {key: value for key, value in module_ir.items() if key not in {"skill_id", "content_digest"}}
    root_cause_unsigned = {key: value for key, value in root_cause_ir.items() if key not in {"skill_id", "content_digest"}}

    assert module_unsigned != root_cause_unsigned
    assert module_ir["inputs"][0]["label"] == "Source materials for workflows/module-analysis.md"
    assert root_cause_ir["inputs"][0]["label"] == "Source materials for workflows/root-cause.md"
    assert module_ir["steps"][0]["instruction_path"] == "steps/01-step.md"
    assert root_cause_ir["steps"][0]["instruction_path"] == "steps/01-step.md"
    assert module_ir["selected_workflow_path"] == "workflows/module-analysis.md"
    assert root_cause_ir["selected_workflow_path"] == "workflows/root-cause.md"


def test_compile_codetalks_v24_issue_regression_declares_mr_input(tmp_path: Path) -> None:
    compiler = _compiler()
    _write_v24_tree(tmp_path)

    ir = compiler.compile_codetalks_v24_skill(tmp_path, source_scenario_id="issue-regression")

    assert {"input_id": "input.mr-link", "label": "MR link", "kind": "url", "required": True} in ir["inputs"]
    assert ir["steps"][0]["instruction_path"] == "steps/01-step.md"
    assert ir["selected_workflow_path"] == "workflows/issue-regression.md"
    assert ir["judge"] == {
        "required": False,
        "isolated_session": False,
        "artifact_ids": [],
    }


def test_compile_codetalks_v24_rejects_invalid_manifest_json(tmp_path: Path) -> None:
    compiler = _compiler()
    _write_v24_tree(tmp_path)
    (tmp_path / "workflow-manifest.json").write_text("{bad json", encoding="utf-8")

    with pytest.raises(compiler.SkillPackageValidationError) as caught:
        compiler.compile_codetalks_v24_skill(tmp_path, source_scenario_id="module-analysis")

    assert [(issue.code, issue.path) for issue in caught.value.issues] == [("invalid_json", "workflow-manifest.json")]


def test_compile_codetalks_v24_rejects_invalid_manifest_utf8(tmp_path: Path) -> None:
    compiler = _compiler()
    _write_v24_tree(tmp_path)
    (tmp_path / "workflow-manifest.json").write_bytes(b"\xff\xfe\xfd")

    with pytest.raises(compiler.SkillPackageValidationError) as caught:
        compiler.compile_codetalks_v24_skill(tmp_path, source_scenario_id="module-analysis")

    assert [(issue.code, issue.path) for issue in caught.value.issues] == [("invalid_json", "workflow-manifest.json")]


def test_compile_skill_ir_rejects_invalid_source_json(tmp_path: Path) -> None:
    compiler = _compiler()
    document = _skill_document()
    skill_path = _write_source_tree(tmp_path, document)
    skill_path.write_text("{bad json", encoding="utf-8")

    with pytest.raises(compiler.SkillPackageValidationError) as caught:
        compiler.compile_skill_ir(document, source_root=tmp_path, source_path=skill_path)

    assert [(issue.code, issue.path) for issue in caught.value.issues] == [("invalid_json", "source_path")]


def test_compile_skill_ir_rejects_invalid_source_utf8(tmp_path: Path) -> None:
    compiler = _compiler()
    document = _skill_document()
    skill_path = _write_source_tree(tmp_path, document)
    skill_path.write_bytes(b"\xff\xfe\xfd")

    with pytest.raises(compiler.SkillPackageValidationError) as caught:
        compiler.compile_skill_ir(document, source_root=tmp_path, source_path=skill_path)

    assert [(issue.code, issue.path) for issue in caught.value.issues] == [("invalid_json", "source_path")]
