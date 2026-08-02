"""Red tests for the Gate 1 workflow contract."""

from pathlib import Path
from types import SimpleNamespace

from app.services.workflow_dsl import validate_workflow_definition
from app.services.workflow_presets import (
    active_builtin_workflow_presets,
    builtin_workflow_presets,
    builtin_workflow_presets_for_bootstrap,
)
from app.services.workflow_presets import CORE_WORKFLOW_PRESET_IDS
from app.services.coverage_workflow import coverage_report_payload, parse_coverage_inputs


def test_gate1_presets_expose_input_guidance_and_required_workflow_variants():
    presets = {
        str(item["definition"]["id"]): item["definition"]
        for item in builtin_workflow_presets()
    }

    assert {"coverage_gap", "defect_retest", "module_risk_report"}.issubset(presets)
    for workflow_id in ("coverage_gap", "defect_retest", "module_risk_report"):
        inputs = presets[workflow_id]["inputs"]
        assert all(item.get("label") for item in inputs)
        assert all(item.get("example") for item in inputs if item.get("required"))
        assert all(item.get("missing_guidance") for item in inputs if item.get("required"))


def test_gate1_presets_are_published_on_startup_and_visible_in_phase2():
    expected = {"coverage_gap", "defect_retest", "module_risk_report"}

    assert expected.issubset(
        {str(item["id"]) for item in active_builtin_workflow_presets()}
    )
    assert expected.issubset(
        {str(item["id"]) for item in builtin_workflow_presets_for_bootstrap()}
    )


def test_all_core_workflow_inputs_have_product_metadata():
    presets = {
        str(item["definition"]["id"]): item["definition"]
        for item in builtin_workflow_presets()
    }

    for workflow_id in CORE_WORKFLOW_PRESET_IDS:
        for item in presets[workflow_id]["inputs"]:
            assert item.get("label"), f"{workflow_id}:{item.get('id')} missing label"
            if item.get("required"):
                assert item.get("example"), f"{workflow_id}:{item.get('id')} missing example"
                assert item.get("missing_guidance"), (
                    f"{workflow_id}:{item.get('id')} missing guidance"
                )


def test_workflow_input_keeps_typed_display_metadata():
    workflow = validate_workflow_definition(
        {
            "id": "typed_input_metadata",
            "name": "Typed input metadata",
            "version": 1,
            "inputs": [
                {
                    "id": "coverage_report",
                    "type": "coverage_report",
                    "required": True,
                    "label": "覆盖率文件",
                    "example": "coverage.xml 或 coverage.lcov",
                    "missing_guidance": "请上传本次测试产生的覆盖率文件。",
                }
            ],
            "steps": [],
            "outputs": [],
        }
    )

    item = workflow.inputs[0]
    assert getattr(item, "label") == "覆盖率文件"
    assert getattr(item, "example") == "coverage.xml 或 coverage.lcov"
    assert getattr(item, "missing_guidance") == "请上传本次测试产生的覆盖率文件。"


def test_coverage_parse_adapts_supported_report_formats(tmp_path: Path):
    cobertura = tmp_path / "coverage-cobertura.xml"
    cobertura.write_text(
        '<coverage line-rate="0.5" branch-rate="0.0">'
        '<packages><package name="iscsi"><classes>'
        '<class filename="iscsi.c" line-rate="0.5" branch-rate="0.0">'
        '<methods><method name="iscsi_login"><lines><line number="10" hits="0" />'
        '</lines></method></methods><lines><line number="10" hits="0" />'
        '<line number="11" hits="1" /></lines></class>'
        '</classes></package></packages></coverage>',
        encoding="utf-8",
    )
    jacoco = tmp_path / "coverage-jacoco.xml"
    jacoco.write_text(
        '<report name="JaCoCo"><counter type="LINE" missed="1" covered="1" />'
        '<counter type="METHOD" missed="1" covered="1" />'
        '<package name="iscsi"><counter type="LINE" missed="1" covered="1" />'
        '<counter type="METHOD" missed="1" covered="1" />'
        '<class name="iscsi/Login" sourcefilename="Login.java">'
        '<counter type="LINE" missed="1" covered="1" />'
        '<method name="login"><counter type="LINE" missed="1" covered="0" />'
        '</method></class></package></report>',
        encoding="utf-8",
    )
    html = tmp_path / "coverage-html.html"
    html.write_text(
        '<table><tr><td><a href="iscsi.c.html">iscsi.c</a></td><td>50%</td></tr></table>',
        encoding="utf-8",
    )
    function_table = tmp_path / "coverage-functions.csv"
    function_table.write_text(
        "function,location,covered,hits\niscsi_login,iscsi.c:10,false,0\n"
        "iscsi_recover,iscsi.c:20,true,2\n",
        encoding="utf-8",
    )
    lcov = tmp_path / "coverage.lcov"
    lcov.write_text(
        "TN:\nSF:iscsi.c\nFN:10,iscsi_login\nFNDA:0,iscsi_login\nend_of_record\n",
        encoding="utf-8",
    )

    payload = parse_coverage_inputs(
        {
            "coverage_report": {
                "kind": "file_set",
                "files": [
                    {"path": str(path), "filename": path.name, "suffix": path.suffix}
                    for path in (cobertura, jacoco, html, function_table, lcov)
                ],
            }
        }
    )

    assert set(payload["summary"]["source_formats"]) >= {
        "cobertura",
        "jacoco",
        "html",
        "internal_function_hits",
        "lcov",
    }
    assert payload["summary"]["files_count"] >= 5
    assert any(item["function_name"] == "iscsi_login" for item in payload["uncovered_functions"])


def test_coverage_keeps_same_named_uncovered_function_in_different_files():
    files = [
        SimpleNamespace(
            filename=filename,
            function_hits=[],
            uncovered_functions=["cleanup"],
            line_rate=0,
            branch_rate=0,
        )
        for filename in ("login.c", "session.c")
    ]
    report = SimpleNamespace(
        source_format="fixture",
        modules=[SimpleNamespace(module_path="iscsi", files=files)],
    )

    payload = coverage_report_payload(report)

    assert [item["file_path"] for item in payload["uncovered_functions"]] == [
        "login.c",
        "session.c",
    ]


def test_v2_run_summary_exposes_waiting_reason_and_typed_recovery_actions():
    from app.api.workbench_v2_tasks import _run_summary

    task_run = SimpleNamespace(
        task_run_id="gate1-run",
        task_id="task-1",
        attempt_number=1,
        parent_task_run_id="",
        workflow_id="gate1",
        workspace_id="workspace-1",
        execution_status="queued",
        quality_status="not_checked",
        delivery_status="pending",
        started_at="",
        completed_at="",
        created_at="2026-08-02T00:00:00Z",
    )
    waiting = _run_summary(task_run)
    assert waiting["waiting_reason"]

    task_run.execution_status = "failed"
    actions = _run_summary(task_run)["recovery_actions"]
    assert actions
    assert all({"id", "kind", "label"}.issubset(item) for item in actions)
    assert any(item["kind"] == "retry" for item in actions)


def test_prior_run_input_reuse_rehydrates_inline_and_file_set_inputs(tmp_path: Path):
    from app.api.workbench_v2_tasks import _inputs_from_parent_snapshot

    inline_patch = tmp_path / "old" / "inputs" / "patch" / "original" / "patch.patch"
    first_file = tmp_path / "old" / "inputs" / "docs" / "one.md"
    inline_patch.parent.mkdir(parents=True)
    first_file.parent.mkdir(parents=True)
    inline_patch.write_text("diff --git a/a.c b/a.c\n", encoding="utf-8")
    first_file.write_text("requirement", encoding="utf-8")

    reusable = _inputs_from_parent_snapshot(
        {
            "patch_diff": {
                "kind": "file",
                "inline_text": True,
                "original_path": "",
                "copied_path": str(inline_patch),
            },
            "materials": {
                "kind": "file_set",
                "files": [
                    {
                        "kind": "file",
                        "original_path": "",
                        "copied_path": str(first_file),
                    }
                ],
            },
            "test_goal": "recovery",
        }
    )

    assert reusable == {
        "patch_diff": str(inline_patch),
        "materials": [str(first_file)],
        "test_goal": "recovery",
    }
