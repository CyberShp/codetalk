"""F014 source archive topology is pinned without checking archive contents into Git."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from zipfile import ZipFile

import pytest


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "skills" / "codetalks-v2.4"
SOURCE_INVENTORY = FIXTURE_DIR / "source-inventory.json"
EXPECTED_IR_SUMMARY = FIXTURE_DIR / "expected-ir-summary.json"
PINNED_ARCHIVE_SHA256 = "7369ef35d339bc554610754ceb385b78d15f94fc8e1e5435350c4ebcf2b27325"


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_checked_in_semantic_fixture_preserves_source_topology_and_ir_traceability():
    inventory = _read_json(SOURCE_INVENTORY)
    expected_ir = _read_json(EXPECTED_IR_SUMMARY)

    assert inventory["archive_sha256"] == PINNED_ARCHIVE_SHA256
    assert inventory["file_count"] == 37
    assert len(inventory["files"]) == 37
    assert all(isinstance(name, str) for name in inventory["files"])
    assert {
        "templates/开发给测试讲代码模板.md",
        "templates/流程讲解活文档模板.md",
        "templates/黑盒测试用例Markdown模板.md",
    }.issubset(inventory["files"])
    assert inventory["utf8_filenames"] is True

    assert expected_ir["scenario_count"] == 5
    assert len(expected_ir["scenarios"]) == 5
    assert [scenario["id"] for scenario in expected_ir["scenarios"]] == [
        "custom",
        "issue-regression",
        "module-analysis",
        "root-cause",
        "special-risk",
    ]
    assert {scenario["source_path"] for scenario in expected_ir["scenarios"]}.issubset(
        set(inventory["files"])
    )
    assert expected_ir["module_analysis"]["step_count"] == 9
    assert expected_ir["module_analysis"]["core_rule_count"] == 3
    assert expected_ir["module_analysis"]["required_artifact_count"] == 37
    assert expected_ir["module_analysis"]["formal_output_count"] == 8
    assert expected_ir["module_analysis"]["step_ids"] == [
        "01",
        "02",
        "03",
        "04",
        "05",
        "06",
        "07",
        "08",
        "09",
    ]
    assert expected_ir["module_analysis"]["core_rule_ids"] == [
        "path-fidelity",
        "evidence-consumption",
        "narrative-first",
    ]
    assert expected_ir["module_analysis"]["formal_output_ids"] == [
        "developer-test-code-explanation",
        "flow-branch-state-resource-exception",
        "risk-and-sfmea",
        "black-box-scenarios",
        "black-box-flows",
        "black-box-cases",
        "coverage-audit-and-limitations",
        "complete-analysis-report",
    ]

    trace_entries = expected_ir["source_to_ir_trace"]
    assert trace_entries
    traced_paths = {entry["source_path"] for entry in trace_entries}
    assert traced_paths == set(inventory["files"])
    assert all(entry.get("ir_target") or entry.get("disposition") for entry in trace_entries)
    assert {entry["source_path"] for entry in trace_entries}.issuperset(
        {
            "workflow-manifest.json",
            "scripts/run_guard.py",
            "workflows/module-analysis.md",
        }
    )
    assert {
        entry["source_path"]
        for entry in trace_entries
        if entry["ir_target"].startswith("step.")
    } == {f"steps/{step_id}-{suffix}.md" for step_id, suffix in [
        ("01", "intake-and-scope"),
        ("02", "evidence-consumption"),
        ("03", "breadth-inventory"),
        ("04", "flow-deep-analysis"),
        ("05", "scenario-expansion"),
        ("06", "sfmea-blackbox-translation"),
        ("07", "test-design"),
        ("08", "independent-judge"),
        ("09", "final-delivery"),
    ]}


def test_optional_real_archive_matches_the_pinned_semantic_fixture():
    archive_path = os.environ.get("CODETALKS_V24_ARCHIVE")
    if not archive_path:
        pytest.skip("set CODETALKS_V24_ARCHIVE to run the local source archive golden check")

    archive = Path(archive_path)
    assert archive.is_file(), f"archive does not exist: {archive}"
    assert _sha256(archive) == PINNED_ARCHIVE_SHA256

    inventory = _read_json(SOURCE_INVENTORY)
    with ZipFile(archive) as source_zip:
        archive_infos = source_zip.infolist()
        archive_names = [info.filename for info in archive_infos]
        manifest = json.loads(source_zip.read(f"{inventory['archive_root']}/workflow-manifest.json"))

    prefix = inventory["archive_root"] + "/"
    relative_names = [name.removeprefix(prefix) for name in archive_names]
    assert len(relative_names) == inventory["file_count"]
    assert relative_names == inventory["files"]
    assert all(name.encode("utf-8").decode("utf-8") == name for name in relative_names)
    assert all(info.flag_bits & 0x800 for info in archive_infos if any(ord(c) > 127 for c in info.filename))

    expected_ir = _read_json(EXPECTED_IR_SUMMARY)
    module_analysis = expected_ir["module_analysis"]
    assert list(manifest["required_core_rules"]) == module_analysis["core_rule_ids"]
    assert [step["id"] for step in manifest["steps"]] == module_analysis["step_ids"]
    assert len(manifest["steps"]) == module_analysis["step_count"]
    assert sum(len(step["required"]) for step in manifest["steps"]) == module_analysis["required_artifact_count"]
    assert len(manifest["steps"][-1]["required"]) == module_analysis["formal_output_count"]
