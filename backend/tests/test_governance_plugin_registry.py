from __future__ import annotations

import ast
import hashlib
import json
import subprocess
import sys
import textwrap
from dataclasses import replace
from pathlib import Path

import pytest


_PROFESSIONAL_LEGACY_MODULES = (
    "app.services.ai_staged_execution",
    "app.services.artifact_contract_v3",
    "app.services.behavior_claim_validator",
    "app.services.flow_evidence",
    "app.services.governance_plugins",
    "app.services.regular_stage_governance",
    "app.services.source_driven_test_design",
    "app.services.test_activity_contract",
    "app.services.test_activity_stage_specs",
)


def _governance_request(tmp_path, **overrides):
    from app.services.governance_plugins.contracts import (
        DeclaredGovernanceOutput,
        GovernanceOutputEdge,
        GovernancePluginRequest,
    )

    request = GovernancePluginRequest(
        handler_id="storage_test_design",
        node_id="governance_01",
        node_kind="governance",
        artifact_dir=str(tmp_path),
        requested_output_ids=("test_activity_contract",),
        declared_outputs=(
            DeclaredGovernanceOutput(
                artifact_id="test_activity_contract",
                path="test_activity_contract.json",
                producer_node_id="governance_01",
                producer_port_id="contract",
            ),
        ),
        output_edges=(
            GovernanceOutputEdge(
                edge_id="edge-contract",
                source_node_id="governance_01",
                source_port_id="contract",
                target_artifact_id="test_activity_contract",
            ),
        ),
        inputs={
            "target": "SPDK iSCSI login",
            "repo_path": str(tmp_path),
            "user_requirements": "输出完整存储测试设计",
        },
    )
    return replace(request, **overrides)


def _validator_request(tmp_path, *, handler_id="sfmea", **overrides):
    from app.services.governance_plugins.contracts import (
        DeclaredGovernanceOutput,
        GovernancePluginRequest,
    )

    request = GovernancePluginRequest(
        handler_id=handler_id,
        node_id=f"validator-{handler_id}",
        node_kind="validator",
        artifact_dir=str(tmp_path),
        required_output_ids=("sfmea",),
        declared_outputs=(
            DeclaredGovernanceOutput(
                artifact_id="sfmea",
                path="sfmea.json",
                producer_node_id="agent-01",
                producer_port_id="sfmea",
            ),
        ),
        inputs={"repo_path": str(tmp_path)},
    )
    return replace(request, **overrides)


def _professional_validator_request(
    tmp_path,
    *,
    handler_id: str,
    artifact_id: str,
    artifact_path: str,
    inputs: dict | None = None,
):
    from app.services.governance_plugins.contracts import (
        DeclaredGovernanceOutput,
        GovernancePluginRequest,
    )

    return GovernancePluginRequest(
        handler_id=handler_id,
        node_id=f"validator-{handler_id}",
        node_kind="validator",
        artifact_dir=str(tmp_path),
        required_output_ids=(artifact_id,),
        declared_outputs=(
            DeclaredGovernanceOutput(
                artifact_id=artifact_id,
                path=artifact_path,
                producer_node_id="agent-01",
                producer_port_id=artifact_id,
            ),
        ),
        inputs=inputs or {"repo_path": str(tmp_path), "artifact_spec": {}},
    )


def _tree(root: Path) -> dict[str, tuple[str, bytes | str]]:
    snapshot: dict[str, tuple[str, bytes | str]] = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            snapshot[relative] = ("symlink", path.readlink().as_posix())
        elif path.is_file():
            snapshot[relative] = ("file", path.read_bytes())
        elif path.is_dir():
            snapshot[relative] = ("directory", "")
    return snapshot


def test_default_registry_exposes_explicit_handler_availability_without_loading_domains():
    from app.services.governance_plugins.registry import (
        create_governance_plugin_registry,
        governance_handler_availability_snapshot,
    )

    domain_modules = {
        "app.services.governance_plugins.storage_test_design",
        "app.services.governance_plugins.sfmea",
        "app.services.governance_plugins.black_box",
        "app.services.governance_plugins.independent_review",
        "app.services.test_activity_contract",
        "app.services.test_activity_stage_specs",
        "app.services.artifact_contract_v3",
    }
    for module_name in domain_modules:
        sys.modules.pop(module_name, None)

    registry = create_governance_plugin_registry()
    snapshot = registry.availability_snapshot()

    assert snapshot == [
        {
            "handler_id": "black_box",
            "handler_version": 1,
            "node_kind": "validator",
            "available": True,
            "capabilities": [
                "black_box_validation",
                "read_only",
                "single_declared_output",
            ],
        },
        {
            "handler_id": "independent_review",
            "handler_version": 1,
            "node_kind": "validator",
            "available": True,
            "capabilities": ["independent_review", "read_only"],
        },
        {
            "handler_id": "sfmea",
            "handler_version": 1,
            "node_kind": "validator",
            "available": True,
            "capabilities": [
                "read_only",
                "sfmea_validation",
                "single_declared_output",
            ],
        },
        {
            "handler_id": "storage_test_design",
            "handler_version": 1,
            "node_kind": "governance",
            "available": True,
            "capabilities": ["declared_artifact_generation", "storage_test_design"],
            "input_ports": [
                {
                    "key": "source_evidence",
                    "label": "源码证据",
                    "type": "artifact",
                    "required": True,
                    "collection": False,
                }
            ],
            "output_ports": [
                {
                    "key": "sfmea",
                    "label": "SFMEA 风险清单",
                    "type": "artifact",
                    "required": True,
                    "collection": False,
                },
                {
                    "key": "black_box_cases",
                    "label": "黑盒测试用例",
                    "type": "artifact",
                    "required": True,
                    "collection": False,
                },
            ],
        },
    ]
    assert governance_handler_availability_snapshot() == snapshot
    assert domain_modules.isdisjoint(sys.modules)


def test_snapshot_reports_broken_lazy_loader_as_unavailable_without_importing_it():
    from app.services.governance_plugins.contracts import GovernancePluginDescriptor
    from app.services.governance_plugins.registry import GovernancePluginRegistry

    registry = GovernancePluginRegistry()
    registry.register(
        GovernancePluginDescriptor(
            handler_id="missing_handler",
            handler_version=1,
            node_kind="validator",
        ),
        loader="app.services.governance_plugins.does_not_exist:create_plugin",
    )

    assert registry.availability_snapshot() == [
        {
            "handler_id": "missing_handler",
            "handler_version": 1,
            "node_kind": "validator",
            "available": False,
            "capabilities": [],
        }
    ]


@pytest.mark.parametrize("profile_id", ["artifact_only", "source_evidence"])
def test_ordinary_profiles_do_not_select_or_load_professional_handlers(profile_id):
    from app.services.governance_plugins.registry import create_governance_plugin_registry

    registry = create_governance_plugin_registry()

    assert registry.explicit_handlers_for_profile(profile_id) == ()
    assert not registry.loaded_handler_ids()


def test_compiling_artifact_only_keeps_professional_modules_cold():
    from app.services.workflow_contract_v3 import compile_workflow_contract_v3

    professional_modules = {
        "app.services.governance_plugins.storage_test_design",
        "app.services.governance_plugins.sfmea",
        "app.services.governance_plugins.black_box",
        "app.services.governance_plugins.independent_review",
        "app.services.test_activity_contract",
        "app.services.test_activity_stage_specs",
        "app.services.behavior_claim_validator",
    }
    for module_name in professional_modules:
        sys.modules.pop(module_name, None)
    graph = {
        "schema_version": 3,
        "workflow_id": "ordinary-report",
        "name": "Ordinary report mentioning SFMEA and black-box",
        "settings": {"validation_profile": "artifact_only"},
        "nodes": [
            {
                "id": "agent",
                "kind": "agent",
                "ports": {
                    "inputs": [],
                    "outputs": [
                        {"id": "report", "type": "artifact", "required": True}
                    ],
                },
                "config": {"handler_id": "agent", "handler_version": 1},
            },
            {
                "id": "report-output",
                "kind": "output",
                "ports": {
                    "inputs": [
                        {"id": "value", "type": "artifact", "required": True}
                    ],
                    "outputs": [],
                },
                "config": {
                    "output_id": "report",
                    "artifact": "report.md",
                    "required": True,
                },
            },
        ],
        "edges": [
            {
                "id": "agent-report",
                "kind": "data",
                "source": {"node_id": "agent", "port_id": "report"},
                "target": {"node_id": "report-output", "port_id": "value"},
            }
        ],
    }

    compiled = compile_workflow_contract_v3(
        graph,
        capabilities={
            "handlers": {
                "agent": {"versions": [1], "kind": "agent"},
                "artifact_exists": {"versions": [1], "kind": "validator"},
            }
        },
        workflow_version_id="wfv-ordinary-report",
    )

    assert compiled["validation_result"]["valid"] is True
    assert [
        node["handler_id"]
        for node in compiled["compiled_plan"]["nodes"]
        if node.get("generated_by_validation_profile")
    ] == ["artifact_exists"]
    assert professional_modules.isdisjoint(sys.modules)


def test_professional_profiles_expand_only_read_only_rules_not_the_generator():
    from app.services.governance_plugins.registry import create_governance_plugin_registry

    registry = create_governance_plugin_registry()

    assert registry.explicit_handlers_for_profile("storage_test_design") == (
        "sfmea",
        "black_box",
    )
    assert registry.explicit_handlers_for_profile("formal_release") == (
        "sfmea",
        "black_box",
        "independent_review",
    )
    assert not registry.loaded_handler_ids()


def test_governance_generation_requires_declared_connected_unique_producer(tmp_path):
    from app.services.governance_plugins.contracts import GovernanceOutputEdge
    from app.services.governance_plugins.registry import create_governance_plugin_registry

    registry = create_governance_plugin_registry()
    base = _governance_request(tmp_path)

    missing_declaration = registry.invoke(replace(base, declared_outputs=()))
    missing_edge = registry.invoke(replace(base, output_edges=()))
    duplicate_edge = registry.invoke(
        replace(
            base,
            output_edges=base.output_edges
            + (
                GovernanceOutputEdge(
                    edge_id="edge-contract-duplicate",
                    source_node_id="other-node",
                    source_port_id="report",
                    target_artifact_id="test_activity_contract",
                ),
            ),
        )
    )
    wrong_producer = registry.invoke(
        replace(
            base,
            declared_outputs=(
                replace(base.declared_outputs[0], producer_node_id="other-node"),
            ),
        )
    )
    duplicate_declaration = registry.invoke(
        replace(
            base,
            declared_outputs=base.declared_outputs + base.declared_outputs,
        )
    )
    unrelated_outgoing_edge = registry.invoke(
        replace(
            base,
            output_edges=base.output_edges
            + (
                GovernanceOutputEdge(
                    edge_id="edge-ghost",
                    source_node_id="governance_01",
                    source_port_id="ghost",
                    target_artifact_id="ghost-output",
                ),
            ),
        )
    )

    assert missing_declaration.error_code == "undeclared_governance_output"
    assert missing_edge.error_code == "unconnected_governance_output"
    assert duplicate_edge.error_code == "multiple_producers_for_governance_output"
    assert wrong_producer.error_code == "governance_output_producer_mismatch"
    assert duplicate_declaration.error_code == "undeclared_governance_output"
    assert unrelated_outgoing_edge.error_code == "undeclared_governance_output_edge"
    for result in (
        missing_declaration,
        missing_edge,
        duplicate_edge,
        wrong_producer,
        duplicate_declaration,
        unrelated_outgoing_edge,
    ):
        assert result.governance_status == "failed"
        assert result.delivery_status == "blocked"
        assert result.produced_artifacts == ()


def test_governance_generation_rejects_duplicate_requested_output_ids(tmp_path):
    from app.services.governance_plugins.registry import create_governance_plugin_registry

    base = _governance_request(tmp_path)
    result = create_governance_plugin_registry().invoke(
        replace(
            base,
            requested_output_ids=(
                "test_activity_contract",
                "test_activity_contract",
            ),
        )
    )

    assert result.error_code == "duplicate_governance_output_request"
    assert result.produced_artifacts == ()


def test_explicit_storage_plugin_reuses_legacy_contract_without_inferred_outputs(
    tmp_path,
):
    from app.services.governance_plugins.registry import create_governance_plugin_registry

    legacy_module = "app.services.test_activity_contract"
    sys.modules.pop(legacy_module, None)
    registry = create_governance_plugin_registry()
    assert legacy_module not in sys.modules

    result = registry.invoke(_governance_request(tmp_path))

    assert result.governance_status == "passed"
    assert result.delivery_status == "ready"
    assert [item.artifact_id for item in result.produced_artifacts] == [
        "test_activity_contract"
    ]
    assert legacy_module in sys.modules
    payload = json.loads(result.produced_artifacts[0].content)

    from app.services.test_activity_contract import build_test_activity_contract

    expected = build_test_activity_contract(
        target="SPDK iSCSI login",
        repo_path=str(tmp_path),
        workflow_outputs=[
            {"artifact": "test_activity_contract.json", "required": True}
        ],
        user_requirements="输出完整存储测试设计",
    )
    expected["required_outputs"] = ["test_activity_contract.json"]
    expected["artifact_contract"] = {
        "test_activity_contract.json": {
            "preview": "json",
            "schema": {"type": "object"},
        }
    }
    assert payload == expected


def test_storage_test_design_generates_role_correct_professional_outputs_from_explicit_evidence(
    tmp_path,
):
    from app.services.governance_plugins.contracts import (
        DeclaredGovernanceOutput,
        GovernanceOutputEdge,
    )
    from app.services.governance_plugins.registry import create_governance_plugin_registry
    from app.services.test_activity_contract import (
        ARTIFACT_TEMPLATES,
        BLACK_BOX_REQUIRED_DIMENSIONS,
        _audit_json_artifact,
    )

    source = tmp_path / "lib" / "iscsi" / "login.c"
    source.parent.mkdir(parents=True)
    source.write_text(
        "int login_step(int valid) {\n"
        "    if (!valid) return -1;\n"
        "    return 0;\n"
        "}\n",
        encoding="utf-8",
    )
    (tmp_path / "test" / "iscsi_tgt").mkdir(parents=True)
    base = _governance_request(tmp_path)
    declarations = (
        DeclaredGovernanceOutput(
            artifact_id="sfmea",
            path="sfmea.json",
            producer_node_id=base.node_id,
            producer_port_id="sfmea",
        ),
        DeclaredGovernanceOutput(
            artifact_id="black_box_cases",
            path="black_box_cases.json",
            producer_node_id=base.node_id,
            producer_port_id="black_box_cases",
        ),
    )
    request = replace(
        base,
        requested_output_ids=("sfmea", "black_box_cases"),
        declared_outputs=declarations,
        output_edges=tuple(
            GovernanceOutputEdge(
                edge_id=f"edge-{output.artifact_id}",
                source_node_id=base.node_id,
                source_port_id=output.producer_port_id,
                target_artifact_id=output.artifact_id,
            )
            for output in declarations
        ),
        inputs={
            "target": "SPDK iSCSI login error and recovery behavior",
            "repo_path": str(tmp_path),
            "source_evidence": [
                {
                    "file_path": "lib/iscsi/login.c",
                    "start_line": 1,
                    "end_line": 3,
                    "excerpt": (
                        "int login_step(int valid) {\n"
                        "    if (!valid) return -1;\n"
                        "    return 0;"
                    ),
                    "symbols": ["login_step"],
                    "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                }
            ],
        },
    )

    result = create_governance_plugin_registry().invoke(request)

    assert result.governance_status == "passed", (
        result.validation.issues[0].details if result.validation else result
    )
    assert result.delivery_status == "ready"
    assert [item.artifact_id for item in result.produced_artifacts] == [
        "sfmea",
        "black_box_cases",
    ]
    payloads = {
        item.artifact_id: json.loads(item.content)
        for item in result.produced_artifacts
    }
    sfmea = payloads["sfmea"]
    cases = payloads["black_box_cases"]
    assert sfmea != cases
    assert sfmea[0]["sfmea_id"] == "storage_sfmea_001"
    assert sfmea[0]["source_evidence"] == ["lib/iscsi/login.c"]
    assert sfmea[0]["rpn"] == (
        sfmea[0]["severity"]
        * sfmea[0]["occurrence"]
        * sfmea[0]["detection_score"]
    )
    assert {item["test_dimension"] for item in cases} == set(
        BLACK_BOX_REQUIRED_DIMENSIONS
    )
    assert {item["case_type"] for item in cases} == {"black_box_ready"}
    assert all("public workflow" in item["inputs"] for item in cases)
    assert all(
        "login_step" not in step
        for item in cases
        for step in item["steps"]
    )
    assert {tuple(item["risk_ids"]) for item in cases} == {
        ("storage_sfmea_001",)
    }
    assert all(
        item["source_or_test_evidence"] == ["lib/iscsi/login.c"]
        for item in cases
    )
    assert _audit_json_artifact(
        artifact="sfmea.json",
        payload=sfmea,
        spec=ARTIFACT_TEMPLATES["sfmea.json"],
        repo=tmp_path,
    ) == []
    assert _audit_json_artifact(
        artifact="black_box_cases.json",
        payload=cases,
        spec=ARTIFACT_TEMPLATES["black_box_cases.json"],
        repo=tmp_path,
    ) == []


def test_storage_test_design_skips_test_harness_evidence_for_product_sfmea(
    tmp_path,
):
    from app.services.governance_plugins.contracts import (
        DeclaredGovernanceOutput,
        GovernanceOutputEdge,
    )
    from app.services.governance_plugins.registry import create_governance_plugin_registry
    from app.services.test_activity_contract import BLACK_BOX_REQUIRED_DIMENSIONS

    product = tmp_path / "lib" / "iscsi" / "login.c"
    product.parent.mkdir(parents=True)
    product.write_text(
        "int login_step(int valid) {\n"
        "    if (!valid) return -1;\n"
        "    return 0;\n"
        "}\n",
        encoding="utf-8",
    )
    product_cleanup = tmp_path / "lib" / "iscsi" / "cleanup.c"
    product_cleanup.write_text(
        "int cleanup_step(int active) {\n"
        "    if (active) return -1;\n"
        "    return 0;\n"
        "}\n",
        encoding="utf-8",
    )
    harness = tmp_path / "test" / "app" / "fuzz" / "iscsi_fuzz.c"
    harness.parent.mkdir(parents=True)
    harness.write_text(
        "void iscsi_put_pdu(void *pdu) {\n"
        "    if (!pdu) return;\n"
        "}\n",
        encoding="utf-8",
    )
    base = _governance_request(tmp_path)
    declarations = (
        DeclaredGovernanceOutput(
            artifact_id="sfmea",
            path="sfmea.json",
            producer_node_id=base.node_id,
            producer_port_id="sfmea",
        ),
        DeclaredGovernanceOutput(
            artifact_id="black_box_cases",
            path="black_box_cases.json",
            producer_node_id=base.node_id,
            producer_port_id="black_box_cases",
        ),
    )
    request = replace(
        base,
        requested_output_ids=("sfmea", "black_box_cases"),
        declared_outputs=declarations,
        output_edges=tuple(
            GovernanceOutputEdge(
                edge_id=f"edge-{output.artifact_id}",
                source_node_id=base.node_id,
                source_port_id=output.producer_port_id,
                target_artifact_id=output.artifact_id,
            )
            for output in declarations
        ),
        inputs={
            "target": "SPDK iSCSI login error and recovery behavior",
            "repo_path": str(tmp_path),
            "source_evidence": [
                {
                    "file_path": "lib/iscsi/login.c",
                    "start_line": 1,
                    "end_line": 3,
                    "excerpt": (
                        "int login_step(int valid) {\n"
                        "    if (!valid) return -1;\n"
                        "    return 0;"
                    ),
                    "symbols": ["login_step"],
                    "sha256": hashlib.sha256(product.read_bytes()).hexdigest(),
                },
                {
                    "file_path": "lib/iscsi/cleanup.c",
                    "start_line": 1,
                    "end_line": 3,
                    "excerpt": (
                        "int cleanup_step(int active) {\n"
                        "    if (active) return -1;\n"
                        "    return 0;"
                    ),
                    "symbols": ["cleanup_step"],
                    "sha256": hashlib.sha256(product_cleanup.read_bytes()).hexdigest(),
                },
                {
                    "file_path": "test/app/fuzz/iscsi_fuzz.c",
                    "start_line": 1,
                    "end_line": 3,
                    "excerpt": (
                        "void iscsi_put_pdu(void *pdu) {\n"
                        "    if (!pdu) return;\n"
                        "}"
                    ),
                    "symbols": ["iscsi_put_pdu"],
                    "sha256": hashlib.sha256(harness.read_bytes()).hexdigest(),
                },
            ],
        },
    )

    result = create_governance_plugin_registry().invoke(request)

    assert result.governance_status == "passed", (
        result.validation.issues[0].details if result.validation else result
    )
    payloads = {
        item.artifact_id: json.loads(item.content)
        for item in result.produced_artifacts
    }
    assert {row["source_evidence"][0] for row in payloads["sfmea"]} == {
        "lib/iscsi/cleanup.c",
        "lib/iscsi/login.c",
    }
    assert {
        case["source_or_test_evidence"][0]
        for case in payloads["black_box_cases"]
    } == {"lib/iscsi/cleanup.c", "lib/iscsi/login.c"}
    assert len(payloads["black_box_cases"]) == 2 * len(BLACK_BOX_REQUIRED_DIMENSIONS)
    assert len({
        case["scenario_name"]
        for case in payloads["black_box_cases"]
    }) == len(payloads["black_box_cases"])


def test_storage_test_design_uses_frozen_port_keys_with_random_ids_and_custom_paths(
    tmp_path,
):
    from app.services.governance_plugins.contracts import (
        DeclaredGovernanceOutput,
        GovernanceOutputEdge,
    )
    from app.services.governance_plugins.registry import create_governance_plugin_registry

    source = tmp_path / "lib" / "iscsi" / "login.c"
    source.parent.mkdir(parents=True)
    source.write_text(
        "int login_step(int valid) {\n"
        "    if (!valid) return -1;\n"
        "    return 0;\n"
        "}\n",
        encoding="utf-8",
    )
    (tmp_path / "test" / "iscsi_tgt").mkdir(parents=True)
    base = _governance_request(tmp_path)
    declarations = (
        DeclaredGovernanceOutput(
            artifact_id="output_01",
            path="risk-register.json",
            producer_node_id=base.node_id,
            producer_port_id="port-a91f",
            producer_port_key="sfmea",
        ),
        DeclaredGovernanceOutput(
            artifact_id="output_02",
            path="test-matrix.json",
            producer_node_id=base.node_id,
            producer_port_id="port-b72d",
            producer_port_key="black_box_cases",
        ),
    )
    request = replace(
        base,
        requested_output_ids=("output_01", "output_02"),
        declared_outputs=declarations,
        output_edges=tuple(
            GovernanceOutputEdge(
                edge_id=f"edge-{output.artifact_id}",
                source_node_id=base.node_id,
                source_port_id=output.producer_port_id,
                target_artifact_id=output.artifact_id,
            )
            for output in declarations
        ),
        inputs={
            "target": "SPDK iSCSI login error and recovery behavior",
            "repo_path": str(tmp_path),
            "source_evidence": [
                {
                    "file_path": "lib/iscsi/login.c",
                    "start_line": 1,
                    "end_line": 3,
                    "excerpt": (
                        "int login_step(int valid) {\n"
                        "    if (!valid) return -1;\n"
                        "    return 0;"
                    ),
                    "symbols": ["login_step"],
                    "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                }
            ],
        },
    )

    result = create_governance_plugin_registry().invoke(request)

    assert result.governance_status == "passed", result
    assert [item.artifact_id for item in result.produced_artifacts] == [
        "output_01",
        "output_02",
    ]
    assert [item.metadata["professional_role"] for item in result.produced_artifacts] == [
        "sfmea",
        "black_box_cases",
    ]
    assert "sfmea_id" in json.loads(result.produced_artifacts[0].content)[0]
    assert "case_id" in json.loads(result.produced_artifacts[1].content)[0]


def test_storage_test_design_never_guesses_role_from_misleading_output_names(tmp_path):
    from app.services.governance_plugins.contracts import (
        DeclaredGovernanceOutput,
        GovernanceOutputEdge,
    )
    from app.services.governance_plugins.registry import create_governance_plugin_registry

    base = _governance_request(tmp_path)
    declaration = DeclaredGovernanceOutput(
        artifact_id="sfmea",
        path="sfmea.json",
        producer_node_id=base.node_id,
        producer_port_id="port-untyped",
        producer_port_key="unrecognized_semantic_role",
    )
    request = replace(
        base,
        requested_output_ids=("sfmea",),
        declared_outputs=(declaration,),
        output_edges=(
            GovernanceOutputEdge(
                edge_id="edge-misleading-name",
                source_node_id=base.node_id,
                source_port_id=declaration.producer_port_id,
                target_artifact_id=declaration.artifact_id,
            ),
        ),
    )

    result = create_governance_plugin_registry().invoke(request)

    assert result.error_code == "storage_test_design_generation_unavailable"
    assert result.governance_status == "failed"
    assert result.produced_artifacts == ()


def test_storage_test_design_port_key_overrides_conflicting_artifact_name(tmp_path):
    from app.services.governance_plugins.contracts import (
        DeclaredGovernanceOutput,
        GovernanceOutputEdge,
    )
    from app.services.governance_plugins.registry import create_governance_plugin_registry

    source = tmp_path / "lib" / "iscsi" / "login.c"
    source.parent.mkdir(parents=True)
    source.write_text(
        "int login_step(int valid) {\n"
        "    if (!valid) return -1;\n"
        "    return 0;\n"
        "}\n",
        encoding="utf-8",
    )
    (tmp_path / "test" / "iscsi_tgt").mkdir(parents=True)
    base = _governance_request(tmp_path)
    declaration = DeclaredGovernanceOutput(
        artifact_id="sfmea",
        path="sfmea.json",
        producer_node_id=base.node_id,
        producer_port_id="port-random",
        producer_port_key="black_box_cases",
    )
    request = replace(
        base,
        requested_output_ids=("sfmea",),
        declared_outputs=(declaration,),
        output_edges=(
            GovernanceOutputEdge(
                edge_id="edge-conflicting-name",
                source_node_id=base.node_id,
                source_port_id=declaration.producer_port_id,
                target_artifact_id=declaration.artifact_id,
            ),
        ),
        inputs={
            "target": "SPDK iSCSI login error and recovery behavior",
            "repo_path": str(tmp_path),
            "source_evidence": [
                {
                    "file_path": "lib/iscsi/login.c",
                    "start_line": 1,
                    "end_line": 3,
                    "excerpt": (
                        "int login_step(int valid) {\n"
                        "    if (!valid) return -1;\n"
                        "    return 0;"
                    ),
                    "symbols": ["login_step"],
                    "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                }
            ],
        },
    )

    result = create_governance_plugin_registry().invoke(request)

    assert result.governance_status == "passed", result
    assert result.produced_artifacts[0].metadata["professional_role"] == (
        "black_box_cases"
    )
    assert "case_id" in json.loads(result.produced_artifacts[0].content)[0]
    assert "sfmea_id" not in json.loads(result.produced_artifacts[0].content)[0]


def test_storage_test_design_requires_explicit_evidence_for_professional_generation(
    tmp_path,
):
    from app.services.governance_plugins.contracts import (
        DeclaredGovernanceOutput,
        GovernanceOutputEdge,
    )
    from app.services.governance_plugins.registry import create_governance_plugin_registry

    base = _governance_request(tmp_path)
    request = replace(
        base,
        requested_output_ids=("sfmea",),
        declared_outputs=(
            DeclaredGovernanceOutput(
                artifact_id="sfmea",
                path="sfmea.json",
                producer_node_id=base.node_id,
                producer_port_id="sfmea",
            ),
        ),
        output_edges=(
            GovernanceOutputEdge(
                edge_id="edge-sfmea",
                source_node_id=base.node_id,
                source_port_id="sfmea",
                target_artifact_id="sfmea",
            ),
        ),
        inputs={"target": "SPDK iSCSI login", "repo_path": str(tmp_path)},
    )

    result = create_governance_plugin_registry().invoke(request)

    assert result.error_code == "storage_test_design_source_evidence_required"
    assert result.produced_artifacts == ()


@pytest.mark.parametrize(
    ("artifact_id", "artifact_path"),
    [
        ("test_design", "test_design.md"),
        ("contract", "sfmea.json"),
        ("sfmea", "contract.json"),
    ],
)
def test_storage_test_design_refuses_to_disguise_contract_json_as_professional_deliverable(
    tmp_path,
    artifact_id,
    artifact_path,
):
    from app.services.governance_plugins.contracts import (
        DeclaredGovernanceOutput,
        GovernanceOutputEdge,
    )
    from app.services.governance_plugins.registry import create_governance_plugin_registry

    base = _governance_request(tmp_path)
    request = replace(
        base,
        requested_output_ids=(artifact_id,),
        declared_outputs=(
            DeclaredGovernanceOutput(
                artifact_id=artifact_id,
                path=artifact_path,
                producer_node_id=base.node_id,
                producer_port_id="deliverable",
            ),
        ),
        output_edges=(
            GovernanceOutputEdge(
                edge_id="edge-professional-deliverable",
                source_node_id=base.node_id,
                source_port_id="deliverable",
                target_artifact_id=artifact_id,
            ),
        ),
    )

    result = create_governance_plugin_registry().invoke(request)

    assert result.error_code == "storage_test_design_generation_unavailable"
    assert result.governance_status == "failed"
    assert result.delivery_status == "blocked"
    assert result.produced_artifacts == ()
    assert result.validation is not None
    assert result.validation.issues[0].artifact_id == artifact_id


def test_storage_test_design_does_not_import_workbench_runner(tmp_path):
    runner_module = "app.services.workbench_workflow_runner"
    script = textwrap.dedent(
        """
        import sys
        from pathlib import Path

        from app.services.governance_plugins.contracts import (
            DeclaredGovernanceOutput,
            GovernanceOutputEdge,
            GovernancePluginRequest,
        )
        from app.services.governance_plugins.registry import (
            create_governance_plugin_registry,
        )

        root = Path(sys.argv[1])
        request = GovernancePluginRequest(
            handler_id="storage_test_design",
            node_id="governance_01",
            node_kind="governance",
            artifact_dir=str(root),
            requested_output_ids=("test_activity_contract",),
            declared_outputs=(
                DeclaredGovernanceOutput(
                    artifact_id="test_activity_contract",
                    path="test_activity_contract.json",
                    producer_node_id="governance_01",
                    producer_port_id="contract",
                ),
            ),
            output_edges=(
                GovernanceOutputEdge(
                    edge_id="edge-contract",
                    source_node_id="governance_01",
                    source_port_id="contract",
                    target_artifact_id="test_activity_contract",
                ),
            ),
            inputs={
                "target": "SPDK iSCSI login",
                "repo_path": str(root),
                "user_requirements": "输出完整存储测试设计",
            },
        )
        result = create_governance_plugin_registry().invoke(request)
        assert result.governance_status == "passed"
        assert "app.services.workbench_workflow_runner" not in sys.modules
        """
    )
    completed = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path)],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    governance_root = (
        Path(__file__).parents[1] / "app" / "services" / "governance_plugins"
    )
    assert [
        path.name
        for path in governance_root.glob("*.py")
        if "workbench_workflow_runner" in path.read_text(encoding="utf-8")
    ] == []


def test_storage_test_design_refuses_to_duplicate_one_contract_blob_across_outputs(
    tmp_path,
):
    from app.services.governance_plugins.contracts import (
        DeclaredGovernanceOutput,
        GovernanceOutputEdge,
    )
    from app.services.governance_plugins.registry import create_governance_plugin_registry

    base = _governance_request(tmp_path)
    declarations = (
        base.declared_outputs[0],
        DeclaredGovernanceOutput(
            artifact_id="storage_test_contract",
            path="storage_test_contract.json",
            producer_node_id=base.node_id,
            producer_port_id="storage_test_contract",
        ),
    )
    edges = (
        base.output_edges[0],
        GovernanceOutputEdge(
            edge_id="edge-second-contract",
            source_node_id=base.node_id,
            source_port_id="storage_test_contract",
            target_artifact_id="storage_test_contract",
        ),
    )

    result = create_governance_plugin_registry().invoke(
        replace(
            base,
                requested_output_ids=(
                    "test_activity_contract",
                    "storage_test_contract",
                ),
            declared_outputs=declarations,
            output_edges=edges,
        )
    )

    assert result.error_code == "storage_test_design_output_role_ambiguous"
    assert result.produced_artifacts == ()


def test_validator_is_read_only_and_cannot_add_delivery_artifacts(tmp_path):
    from app.services.governance_plugins.contracts import (
        GeneratedGovernanceArtifact,
        GovernancePluginDescriptor,
        GovernancePluginExecution,
    )
    from app.services.governance_plugins.registry import GovernancePluginRegistry

    class InvalidValidator:
        def execute(self, _request):
            return GovernancePluginExecution(
                status="passed",
                produced_artifacts=(
                    GeneratedGovernanceArtifact(
                        artifact_id="ghost_report",
                        content="not allowed",
                        media_type="text/markdown",
                    ),
                ),
            )

    registry = GovernancePluginRegistry()
    registry.register(
        GovernancePluginDescriptor(
            handler_id="read_only_check",
            handler_version=1,
            node_kind="validator",
            capabilities=("read_only",),
        ),
        factory=InvalidValidator,
    )
    request = replace(
        _validator_request(tmp_path),
        handler_id="read_only_check",
        node_id="validator-read-only",
    )

    result = registry.invoke(request)

    assert result.error_code == "validator_generated_artifact"
    assert result.governance_status == "failed"
    assert result.delivery_status == "blocked"
    assert result.produced_artifacts == ()


def test_validator_request_cannot_declare_or_connect_delivery_outputs(tmp_path):
    from app.services.governance_plugins.contracts import GovernanceOutputEdge
    from app.services.governance_plugins.registry import create_governance_plugin_registry

    base = _validator_request(tmp_path)
    requested = create_governance_plugin_registry().invoke(
        replace(base, requested_output_ids=("review",))
    )
    connected = create_governance_plugin_registry().invoke(
        replace(
            base,
            output_edges=(
                GovernanceOutputEdge(
                    edge_id="validator-review",
                    source_node_id=base.node_id,
                    source_port_id="review",
                    target_artifact_id="review",
                ),
            ),
        )
    )

    assert requested.error_code == "validator_output_forbidden"
    assert connected.error_code == "validator_output_forbidden"
    assert requested.produced_artifacts == connected.produced_artifacts == ()


@pytest.mark.parametrize("required_output_ids", [(), ("sfmea", "sfmea")])
def test_professional_validator_binding_fails_closed_unless_exactly_one_output_is_bound(
    tmp_path,
    required_output_ids,
):
    from app.services.governance_plugins.registry import create_governance_plugin_registry

    result = create_governance_plugin_registry().invoke(
        replace(
            _validator_request(tmp_path),
            required_output_ids=required_output_ids,
        )
    )

    assert result.error_code == "validator_required_output_binding_invalid"
    assert result.governance_status == "failed"
    assert result.produced_artifacts == ()


def test_governance_handler_cannot_return_an_undeclared_artifact(tmp_path):
    from app.services.governance_plugins.contracts import (
        GeneratedGovernanceArtifact,
        GovernancePluginDescriptor,
        GovernancePluginExecution,
    )
    from app.services.governance_plugins.registry import GovernancePluginRegistry

    class GhostOutputGenerator:
        def execute(self, _request):
            return GovernancePluginExecution(
                status="passed",
                produced_artifacts=(
                    GeneratedGovernanceArtifact(
                        artifact_id="ghost_report",
                        content="hidden delivery",
                        media_type="text/markdown",
                    ),
                ),
            )

    registry = GovernancePluginRegistry()
    registry.register(
        GovernancePluginDescriptor(
            handler_id="bounded_generator",
            handler_version=1,
            node_kind="governance",
        ),
        factory=GhostOutputGenerator,
    )
    request = replace(_governance_request(tmp_path), handler_id="bounded_generator")

    result = registry.invoke(request)

    assert result.error_code == "governance_output_set_mismatch"
    assert result.governance_status == "failed"
    assert result.delivery_status == "blocked"
    assert result.produced_artifacts == ()


def test_plugin_exception_changes_governance_axis_not_provider_execution(tmp_path):
    from app.services.governance_plugins.contracts import GovernancePluginDescriptor
    from app.services.governance_plugins.registry import GovernancePluginRegistry

    class FailingValidator:
        def execute(self, _request):
            raise RuntimeError("professional rule failed")

    registry = GovernancePluginRegistry()
    registry.register(
        GovernancePluginDescriptor(
            handler_id="failing_review",
            handler_version=1,
            node_kind="validator",
            capabilities=("read_only",),
        ),
        factory=FailingValidator,
    )
    request = replace(
        _validator_request(tmp_path),
        handler_id="failing_review",
        node_id="validator-failure",
    )

    result = registry.invoke(request)

    assert result.error_code == "governance_plugin_failed"
    assert result.governance_status == "failed"
    assert result.delivery_status == "blocked"
    assert not hasattr(result, "provider_status")
    assert not hasattr(result, "execution_status")


def test_non_blocking_plugin_failure_warns_without_blocking_delivery(tmp_path):
    from app.services.governance_plugins.contracts import GovernancePluginDescriptor
    from app.services.governance_plugins.registry import GovernancePluginRegistry

    class FailingValidator:
        def execute(self, _request):
            raise RuntimeError("review unavailable")

    registry = GovernancePluginRegistry()
    registry.register(
        GovernancePluginDescriptor(
            handler_id="advisory_review",
            handler_version=1,
            node_kind="validator",
        ),
        factory=FailingValidator,
    )
    request = replace(
        _validator_request(tmp_path),
        handler_id="advisory_review",
        blocking=False,
    )

    result = registry.invoke(request)

    assert result.governance_status == "warning"
    assert result.delivery_status == "ready"
    assert result.error_code == "governance_plugin_failed"


def test_sfmea_professional_rules_are_available_only_through_explicit_validator(tmp_path):
    from app.services.governance_plugins.registry import create_governance_plugin_registry

    (tmp_path / "sfmea.json").write_text(
        json.dumps(
            [
                {
                    "sfmea_id": "SFMEA-01",
                    "failure_mode": "资源泄漏",
                    "cause": "cmd 未归还",
                    "effect": "后续申请失败",
                    "detection": "检查资源池",
                    "severity": 8,
                    "occurrence": 3,
                    "detection_score": 4,
                    "rpn": 1,
                    "mitigation": "继续观察",
                    "source_evidence": [],
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    registry = create_governance_plugin_registry()

    result = registry.invoke(_validator_request(tmp_path))

    assert result.governance_status == "failed"
    assert result.delivery_status == "blocked"
    assert result.validation is not None
    assert any(issue.code == "sfmea_rpn_mismatch" for issue in result.validation.issues)


def test_sfmea_plugin_preserves_legacy_professional_rule_results(tmp_path):
    from app.services.governance_plugins.registry import create_governance_plugin_registry
    from app.services.test_activity_contract import (
        ARTIFACT_TEMPLATES,
        _audit_json_artifact,
    )

    payload = [
        {
            "sfmea_id": "SFMEA-PARITY-01",
            "failure_mode": "login state is not released",
            "cause": "error path skips cleanup",
            "effect": "later login attempts fail",
            "detection": "connection state and target log",
            "severity": 11,
            "occurrence": 2,
            "detection_score": 3,
            "rpn": 12,
            "score_explanation": "security impact",
            "mitigation": "monitor logs",
            "source_evidence": "lib/iscsi/iscsi.c",
            "test_mapping": "test/iscsi_tgt",
        }
    ]
    (tmp_path / "risk-register.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )
    expected = _audit_json_artifact(
        artifact="sfmea.json",
        payload=payload,
        spec=ARTIFACT_TEMPLATES["sfmea.json"],
        repo=tmp_path,
    )

    result = create_governance_plugin_registry().invoke(
        _professional_validator_request(
            tmp_path,
            handler_id="sfmea",
            artifact_id="risk-register",
            artifact_path="risk-register.json",
            inputs={
                "repo_path": str(tmp_path),
                # Runtime input cannot weaken the frozen professional contract.
                "artifact_spec": {"required_fields": []},
            },
        )
    )

    assert result.validation is not None
    assert [issue.code for issue in result.validation.issues] == [
        str(issue["code"]) for issue in expected
    ]
    assert [issue.details for issue in result.validation.issues] == [
        {
            key: value
            for key, value in issue.items()
            if key not in {"code", "message", "artifact"}
        }
        for issue in expected
    ]


def test_black_box_plugin_preserves_legacy_professional_rule_results(tmp_path):
    from app.services.governance_plugins.registry import create_governance_plugin_registry
    from app.services.test_activity_contract import (
        ARTIFACT_TEMPLATES,
        _audit_json_artifact,
    )

    payload = [
        {
            "case_id": "BB-PARITY-01",
            "test_dimension": "normal_path",
            "scenario_name": "valid CHAP login",
            "preconditions": "target requires CHAP",
            "steps": ["call spdk_iscsi_login() directly"],
            "expected_result": "success",
            "observability": "return code",
            "failure_diagnostics": "inspect internal state",
            "mapped_test_dir": "test/iscsi_tgt",
            "source_or_test_evidence": "lib/iscsi/iscsi.c",
        }
    ]
    (tmp_path / "external-cases.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )
    expected = _audit_json_artifact(
        artifact="black_box_cases.json",
        payload=payload,
        spec=ARTIFACT_TEMPLATES["black_box_cases.json"],
        repo=tmp_path,
    )

    result = create_governance_plugin_registry().invoke(
        _professional_validator_request(
            tmp_path,
            handler_id="black_box",
            artifact_id="external-cases",
            artifact_path="external-cases.json",
            inputs={
                "repo_path": str(tmp_path),
                # Runtime input cannot weaken the frozen professional contract.
                "artifact_spec": {"required_fields": []},
            },
        )
    )

    assert result.validation is not None
    assert [issue.code for issue in result.validation.issues] == [
        str(issue["code"]) for issue in expected
    ]
    assert "black_box_boundary_violation" in {
        issue.code for issue in result.validation.issues
    }


def test_independent_review_cannot_consume_an_unbound_legacy_artifact(tmp_path):
    from app.services.governance_plugins.registry import create_governance_plugin_registry

    (tmp_path / "sfmea.json").write_text("[]", encoding="utf-8")
    (tmp_path / "black_box_cases.json").write_text("[]", encoding="utf-8")
    request = replace(
        _validator_request(tmp_path, handler_id="independent_review"),
        node_id="validator-independent-review",
        inputs={
            "repo_path": str(tmp_path),
            "contract": {
                "artifact_contract": {
                    "sfmea.json": {"required_fields": []},
                },
                "quality_gates": {},
            },
        },
    )

    result = create_governance_plugin_registry().invoke(request)

    assert result.validation is not None
    assert all(
        issue.artifact_id != "black_box_cases.json"
        for issue in result.validation.issues
    )


def test_independent_review_is_read_only_and_matches_scoped_legacy_audit(tmp_path):
    from app.services.governance_plugins.registry import create_governance_plugin_registry
    from app.services.test_activity_contract import audit_test_activity_artifacts

    (tmp_path / "risk-register.json").write_text("[]", encoding="utf-8")
    contract = {
        "artifact_contract": {
            "risk-register.json": {"required_fields": []},
        },
        "quality_gates": {},
    }
    request = _professional_validator_request(
        tmp_path,
        handler_id="independent_review",
        artifact_id="risk-register",
        artifact_path="risk-register.json",
        inputs={"repo_path": str(tmp_path), "contract": contract},
    )
    before = _tree(tmp_path)
    expected = audit_test_activity_artifacts(
        artifact_dir=tmp_path,
        contract=contract,
        repo_path=str(tmp_path),
    )

    result = create_governance_plugin_registry().invoke(request)

    assert result.validation is not None
    assert result.validation.status == (
        "passed" if expected["deliverable"] else "failed"
    )
    assert [issue.code for issue in result.validation.issues] == [
        str(issue["code"])
        for issue in expected["issues"]
        if isinstance(issue, dict)
    ]
    assert result.produced_artifacts == ()
    assert _tree(tmp_path) == before


def test_independent_review_loads_model_reviewer_only_on_explicit_invocation(tmp_path):
    from app.services.governance_plugins.registry import create_governance_plugin_registry

    reviewer_module = "app.services.behavior_claim_validator"
    sys.modules.pop(reviewer_module, None)
    (tmp_path / "risk-register.json").write_text("[]", encoding="utf-8")
    request = _professional_validator_request(
        tmp_path,
        handler_id="independent_review",
        artifact_id="risk-register",
        artifact_path="risk-register.json",
        inputs={
            "repo_path": str(tmp_path),
            "contract": {
                "artifact_contract": {
                    "risk-register.json": {"required_fields": []},
                },
                "quality_gates": {},
            },
        },
    )
    registry = create_governance_plugin_registry()

    assert reviewer_module not in sys.modules

    registry.invoke(request)

    assert reviewer_module in sys.modules


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("blocking", "governance_status", "delivery_status"),
    [(True, "failed", "blocked"), (False, "warning", "ready")],
)
async def test_independent_review_uses_configured_model_path_without_artifact_leakage(
    tmp_path,
    monkeypatch,
    blocking,
    governance_status,
    delivery_status,
):
    from app.services import behavior_claim_validator, test_activity_contract
    from app.services.governance_plugins.registry import create_governance_plugin_registry

    (tmp_path / "sfmea.json").write_text("[]", encoding="utf-8")
    (tmp_path / "unbound-secret.json").write_text(
        '{"secret":"must-not-be-reviewed"}',
        encoding="utf-8",
    )
    captured: dict[str, object] = {}
    model_request = {
        "kind": "behavior_claim_validation_request",
        "schema_version": 2,
        "request_sha256": "bound-request",
        "claims": [
            {
                "claim_id": "ROW:sfmea.json:SFMEA-001",
                "binding": "bound-claim",
                "type": "sfmea_row_behavior",
                "artifact": "sfmea.json",
                "context_ids": ["CTX-001"],
            }
        ],
        "contexts": [
            {
                "context_id": "CTX-001",
                "path": "src/login.c",
                "sha256": "verified-source-digest",
                "content": "000010: return -1;",
            }
        ],
    }

    def fake_build_request(*, artifact_dir, repo_path, **_kwargs):
        snapshot = Path(artifact_dir)
        captured["review_files"] = sorted(
            path.relative_to(snapshot).as_posix()
            for path in snapshot.rglob("*")
            if path.is_file()
        )
        captured["repo_path"] = str(repo_path)
        return model_request

    async def fake_materialize(**kwargs):
        captured["generator_identity"] = kwargs["generator_identity"]
        captured["model_request"] = kwargs["request"]
        captured["builtin_audit_loader"] = kwargs["builtin_audit_loader"]
        snapshot = Path(kwargs["artifact_dir"])
        (snapshot / "behavior_claim_audit" / "batch_01").mkdir(parents=True)
        (snapshot / "behavior_claim_audit" / "batch_01" / "raw_output.txt").write_text(
            "model diagnostic must remain private",
            encoding="utf-8",
        )
        validation = {
            "kind": "behavior_claim_validation",
            "schema_version": 2,
            "status": "completed",
            "request_sha256": "bound-request",
            "validator": {
                "provider": "builtin-llm",
                "model": "configured-audit-model",
                "independent": True,
            },
            "raw_verdict_count": 1,
            "claims": [
                {
                    "claim_id": "ROW:sfmea.json:SFMEA-001",
                    "binding": "bound-claim",
                    "status": "contradicts",
                    "reason": "verified excerpt returns an error",
                }
            ],
        }
        (snapshot / "behavior_claim_validation.json").write_text(
            json.dumps(validation),
            encoding="utf-8",
        )
        return validation

    def fake_audit(*, artifact_dir, contract, repo_path):
        snapshot = Path(artifact_dir)
        captured["audit_contract"] = contract
        captured["audit_repo_path"] = repo_path
        validation = json.loads(
            (snapshot / "behavior_claim_validation.json").read_text(encoding="utf-8")
        )
        verdict = validation["claims"][0]
        return {
            "deliverable": False,
            "issues": [
                {
                    "code": "independent_behavior_claim_contradicted",
                    "message": verdict["reason"],
                    "artifact": "sfmea.json",
                    "claim_id": verdict["claim_id"],
                    "verdict_status": verdict["status"],
                }
            ],
        }

    monkeypatch.setattr(
        behavior_claim_validator,
        "build_behavior_claim_validation_request",
        fake_build_request,
    )
    monkeypatch.setattr(
        behavior_claim_validator,
        "materialize_behavior_claim_validation",
        fake_materialize,
    )
    monkeypatch.setattr(
        test_activity_contract,
        "audit_test_activity_artifacts",
        fake_audit,
    )
    request = replace(
        _professional_validator_request(
            tmp_path,
            handler_id="independent_review",
            artifact_id="sfmea",
            artifact_path="sfmea.json",
            inputs={
                "repo_path": str(tmp_path),
                "generator_identity": "builtin-llm:generator-model",
                "contract": {
                    "artifact_contract": {"sfmea.json": {"required_fields": []}},
                    "quality_gates": {},
                },
            },
        ),
        blocking=blocking,
    )
    before = _tree(tmp_path)

    result = create_governance_plugin_registry().invoke(request)

    assert captured["review_files"] == ["sfmea.json"]
    assert captured["repo_path"] == str(tmp_path)
    assert captured["generator_identity"] == "builtin-llm:generator-model"
    assert captured["model_request"] == model_request
    assert callable(captured["builtin_audit_loader"])
    assert captured["audit_contract"]["quality_gates"][
        "require_independent_behavior_validation"
    ] is True
    assert result.error_code == "independent_review_failed"
    assert result.governance_status == governance_status
    assert result.delivery_status == delivery_status
    assert not hasattr(result, "provider_status")
    assert not hasattr(result, "execution_status")
    assert result.produced_artifacts == ()
    assert result.validation is not None
    assert result.validation.issues[0].details == {
        "claim_id": "ROW:sfmea.json:SFMEA-001",
        "verdict_status": "contradicts",
    }
    assert _tree(tmp_path) == before


def test_unknown_handler_is_structured_governance_failure(tmp_path):
    from app.services.governance_plugins.registry import create_governance_plugin_registry

    request = replace(
        _validator_request(tmp_path),
        handler_id="not-registered",
    )

    result = create_governance_plugin_registry().invoke(request)

    assert result.error_code == "governance_handler_unavailable"
    assert result.governance_status == "failed"
    assert result.delivery_status == "blocked"


def test_harness_adapter_and_orchestrator_do_not_import_professional_governance():
    services = Path(__file__).parents[1] / "app" / "services"
    runtime_sources = [
        services / "harness_facade.py",
        services / "legacy_workflow_execution.py",
        services / "workbench_task_run.py",
        services / "workbench_workflow_runner.py",
        *sorted((services / "provider_adapters").glob("*.py")),
    ]

    offenders: list[str] = []
    for path in runtime_sources:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        imported_modules = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        imported_modules.update(
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        )
        offenders.extend(
            f"{path.relative_to(services)} imports {module_name}"
            for module_name in sorted(imported_modules)
            if module_name.startswith(_PROFESSIONAL_LEGACY_MODULES)
        )

    assert offenders == []


def test_importing_runner_does_not_eagerly_load_professional_legacy_modules():
    code = f"""
import sys
import app.services.workbench_workflow_runner

forbidden = {tuple(_PROFESSIONAL_LEGACY_MODULES)!r}
loaded = sorted(name for name in forbidden if name in sys.modules)
assert loaded == [], loaded
"""

    subprocess.run([sys.executable, "-c", code], check=True)


def test_legacy_workflow_execution_facade_delegates_to_canonical_implementations():
    code = """
from app.services import (
    artifact_contract_v3,
    legacy_workflow_execution,
    source_driven_test_design,
    test_activity_contract,
)

assert (
    legacy_workflow_execution.materialize_artifact_contract_v3_outputs
    is artifact_contract_v3.materialize_artifact_contract_v3_outputs
)
assert (
    legacy_workflow_execution.audit_test_activity_artifacts
    is test_activity_contract.audit_test_activity_artifacts
)
assert (
    legacy_workflow_execution.refresh_source_driven_delivery_governance
    is source_driven_test_design.refresh_source_driven_delivery_governance
)
"""

    subprocess.run([sys.executable, "-c", code], check=True)
