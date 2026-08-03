from __future__ import annotations

import errno
import hashlib
import importlib
import json
import time
from pathlib import Path

import pytest

from app.services.quality_evaluation_contract import AxisStatus, EvaluationScope
from tests.test_quality_evaluator import _snapshot


def _runner():
    try:
        return importlib.import_module("app.services.quality_benchmark_runner")
    except ModuleNotFoundError:
        pytest.fail("quality_benchmark_runner is not implemented; expected P3 RED")


class _IndependentAcceptingVerdictAdapter:
    """Test evaluator oracle; candidate-authored status fields are intentionally ignored."""

    def claim_verdict(self, *, candidate, truth):
        return "supports"

    def breadth_verdict(self, *, candidate, truth):
        return "supports"

    def depth_verdict(self, *, candidate, truth, binding):
        return "supports"


class _DeterministicBatchSemanticJudge:
    """Batch-level test double for the production judge boundary."""

    def __init__(self) -> None:
        self.calls = []

    def judge(self, *, judgments, snapshot_label, **kwargs):
        from app.services.quality_benchmark_semantic_judge import SemanticJudgeResult

        self.calls.append(
            {
                "judgments": tuple(judgments),
                "snapshot_label": snapshot_label,
                **kwargs,
            }
        )
        verdicts = {}
        for judgment in judgments:
            candidate = judgment.candidate_statement.lower()
            verdicts[judgment.judgment_id] = (
                "contradicts"
                if any(
                    marker in candidate
                    for marker in (
                        "the following statement is false",
                        "does not",
                        "never",
                        "opposite",
                    )
                )
                else "supports"
            )
        return SemanticJudgeResult(
            verdicts=verdicts,
            metadata={
                "schema_version": "quality-semantic-judge-audit-v1",
                "snapshot": snapshot_label,
                "status": "completed",
                "judge_version": "quality-semantic-judge-v1",
                "judge": {
                    "provider": "fixture",
                    "runtime_id": "fixture-runtime",
                    "model": "fixture-independent-judge",
                    "reasoning_effort": "deterministic",
                    "independent": True,
                },
                "request_sha256": "a" * 64,
                "result_sha256": "b" * 64,
            },
            limitations=(),
        )


def _write_json(path: Path, payload: object) -> str:
    encoded = json.dumps(payload, sort_keys=True).encode()
    path.write_bytes(encoded)
    return hashlib.sha256(encoded).hexdigest()


def _case_tree(tmp_path: Path) -> Path:
    from app.services.quality_depth_evaluator import (
        DepthEvidenceCatalog,
        depth_evidence_catalog_sha256,
    )
    from tests.test_quality_depth_evaluator import (
        _catalog as depth_catalog,
        _truth as depth_truth,
    )

    case_dir = tmp_path / "case"
    case_dir.mkdir()
    truth = depth_truth()
    truth["case_id"] = "case-1"
    catalog_payload = depth_catalog(truth)
    catalog_payload["case_id"] = "case-1"
    catalog = DepthEvidenceCatalog.model_validate(catalog_payload)
    truth["evidence_catalog_sha256"] = depth_evidence_catalog_sha256(catalog)
    descriptors = {}
    for field, filename, payload in (
        ("gold_claims", "gold_claims.json", [{"gold_id": "G-1"}]),
        ("coverage_universe", "coverage_universe.json", [{"item_id": "U-1"}]),
        ("critical_chains", "critical_chains.json", truth),
        (
            "execution_oracles",
            "execution_oracles.json",
            {
                "evidence_catalog": catalog.model_dump(mode="json"),
                "execution_plan": {
                    "schema_version": "quality-depth-execution-v1",
                    "case_id": "case-1",
                    "execution_tier": "S",
                    "policy": "disabled",
                    "oracles": [],
                    "limitations": [],
                },
                "tier_dispositions": [],
            },
        ),
    ):
        descriptors[field] = {
            "path": filename,
            "sha256": _write_json(case_dir / filename, payload),
        }
    _write_json(
        case_dir / "case.json",
        {
            "schema_version": "quality-benchmark-case-v1",
            "case_id": "case-1",
            "project_id": "project-1",
            "truth_package_version": "1",
            "tier": "S",
            "truth_package": descriptors,
            "test_execution": {"policy": "disabled", "commands": []},
        },
    )
    return case_dir / "case.json"


class _RegistryProject:
    id = "project-1"
    commit = "c" * 40
    tiers = ["S"]


class _Registry:
    truth_package_version = "1"
    projects = [_RegistryProject()]


def test_runner_writes_report_and_reproducibility_manifest_atomically(tmp_path, monkeypatch) -> None:
    module = _runner()
    snapshots = [_snapshot(), _snapshot()]
    monkeypatch.setattr(module, "evaluate_artifact_snapshot", lambda **_: snapshots.pop(0))
    case_path = _case_tree(tmp_path)
    first = tmp_path / "first"
    final = tmp_path / "final"
    first.mkdir()
    final.mkdir()
    output = tmp_path / "output"

    result = module.run_quality_benchmark_case(
        case_path=case_path,
        registry=_Registry(),
        first_pass_artifacts=first,
        final_artifacts=final,
        output_dir=output,
        run_ref="run-1",
        repair_summary={
            "attempt_count": 0,
            "elapsed_seconds": 0,
            "terminal_block_reason": None,
        },
        versions={"model": "fixture", "codetalk": "deadbeef", "evaluator": "v1"},
    )

    report = json.loads((output / "quality_evaluation_report.json").read_text())
    manifest = json.loads((output / "quality_evaluation_manifest.json").read_text())
    human_report = (output / "quality_evaluation_report.md").read_text()
    assert report["scope"] == EvaluationScope.INDEPENDENT_BENCHMARK.value
    assert report["benchmark_identity"]["source_revision"] == "c" * 40
    assert manifest["case_id"] == "case-1"
    assert manifest["versions"]["model"] == "fixture"
    assert manifest["environment"]["python"]
    assert manifest["environment"]["platform"]
    assert manifest["report_sha256"] == result.report_sha256
    assert manifest["human_report_sha256"] == hashlib.sha256(
        human_report.encode()
    ).hexdigest()
    assert "| Accuracy |" in human_report
    assert "| Breadth |" in human_report
    assert "| Depth |" in human_report
    assert "aggregate" not in human_report.lower()
    assert not list(output.glob("*.tmp"))

    with pytest.raises(FileExistsError, match="immutable"):
        module.run_quality_benchmark_case(
            case_path=case_path,
            registry=_Registry(),
            first_pass_artifacts=first,
            final_artifacts=final,
            output_dir=output,
            run_ref="run-1",
            repair_summary={
                "attempt_count": 0,
                "elapsed_seconds": 0,
                "terminal_block_reason": None,
            },
            versions={"model": "fixture", "codetalk": "deadbeef", "evaluator": "v1"},
        )


def test_runner_rejects_same_output_as_input_or_missing_version_metadata(tmp_path) -> None:
    module = _runner()
    case_path = _case_tree(tmp_path)
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()

    with pytest.raises(ValueError, match="output.*artifact"):
        module.run_quality_benchmark_case(
            case_path=case_path,
            registry=_Registry(),
            first_pass_artifacts=artifacts,
            final_artifacts=artifacts,
            output_dir=artifacts,
            run_ref="run",
            repair_summary={"attempt_count": 0, "elapsed_seconds": 0, "terminal_block_reason": None},
            versions={"model": "m", "codetalk": "c", "evaluator": "e"},
        )

    with pytest.raises(ValueError, match="versions"):
        module.validate_version_manifest({"model": "m"})


def test_runner_rejects_nested_truth_candidate_and_output_paths(tmp_path) -> None:
    module = _runner()
    case_path = _case_tree(tmp_path)
    truth_root = case_path.parent
    nested_artifacts = truth_root / "generator"
    nested_artifacts.mkdir()

    with pytest.raises(ValueError, match="truth.*artifact"):
        module.evaluate_artifact_snapshot(
            case_path=case_path,
            registry=_Registry(),
            artifacts_dir=nested_artifacts,
        )

    first = tmp_path / "first"
    final = tmp_path / "final"
    first.mkdir()
    final.mkdir()
    with pytest.raises(ValueError, match="output.*artifact"):
        module.run_quality_benchmark_case(
            case_path=case_path,
            registry=_Registry(),
            first_pass_artifacts=first,
            final_artifacts=final,
            output_dir=first / "nested-output",
            run_ref="run",
            repair_summary={"attempt_count": 0, "elapsed_seconds": 0, "terminal_block_reason": None},
            versions={"model": "m", "codetalk": "c", "evaluator": "e"},
        )


def test_depth_candidate_never_falls_back_to_truth_filename(tmp_path) -> None:
    module = _runner()
    case_path = _case_tree(tmp_path)
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    (artifacts / "critical_chains.json").write_text("{}", encoding="utf-8")

    with pytest.raises(ValueError, match="quality_depth_candidate.json"):
        module.evaluate_artifact_snapshot(
            case_path=case_path,
            registry=_Registry(),
            artifacts_dir=artifacts,
        )


def test_real_snapshot_default_path_runs_batch_judge_for_all_three_axes(tmp_path) -> None:
    from app.services.quality_depth_evaluator import (
        DepthEvidenceCatalog,
        depth_evidence_catalog_sha256,
    )
    from tests.test_quality_accuracy_evaluator import _card, _claim, _gold, _l3
    from tests.test_quality_breadth_evaluator import _generated_for, _universe
    from tests.test_quality_depth_evaluator import (
        _candidate as depth_candidate,
        _catalog as depth_catalog,
        _truth as depth_truth,
    )

    module = _runner()
    case_dir = tmp_path / "case"
    case_dir.mkdir()
    truth = depth_truth()
    truth["case_id"] = "case-1"
    catalog_payload = depth_catalog(truth)
    catalog_payload["case_id"] = "case-1"
    (tmp_path / "fixture.c").write_text(
        "".join(f"fixture {line}\n" for line in range(1, 64)),
        encoding="utf-8",
    )
    for index, binding in enumerate(catalog_payload["bindings"], start=1):
        if binding["category"] == "l3":
            binding["evidence_ref"] = f"oracle://fixture#{index}"
            continue
        scheme = "test" if binding["category"] == "check" else "source"
        binding["evidence_ref"] = (
            f"{scheme}://fixture.c#L{index}-L{index}:binding-{index}"
        )
    catalog = DepthEvidenceCatalog.model_validate(catalog_payload)
    truth["evidence_catalog_sha256"] = depth_evidence_catalog_sha256(catalog)
    universe = _universe()
    descriptors = {}
    for field, filename, payload in (
        (
            "gold_claims",
            "gold_claims.json",
            [{
                **_gold("G-1", "reset-path"),
                "claim": "The reset path reaches recovery.",
                "evidence_refs": ["source://lib/storage.c#L12-L14"],
            }],
        ),
        ("coverage_universe", "coverage_universe.json", universe),
        ("critical_chains", "critical_chains.json", truth),
        (
            "execution_oracles",
            "execution_oracles.json",
            {
                "evidence_catalog": catalog.model_dump(mode="json"),
                "execution_plan": {
                    "schema_version": "quality-depth-execution-v1",
                    "case_id": "case-1",
                    "execution_tier": "S",
                    "policy": "disabled",
                    "oracles": [],
                    "limitations": [],
                },
                "tier_dispositions": [],
            },
        ),
    ):
        descriptors[field] = {
            "path": filename,
            "sha256": _write_json(case_dir / filename, payload),
        }
    _write_json(
        case_dir / "case.json",
        {
            "schema_version": "quality-benchmark-case-v1",
            "case_id": "case-1",
            "project_id": "project-1",
            "truth_package_version": "1",
            "tier": "S",
            "truth_package": descriptors,
            "test_execution": {"policy": "disabled", "commands": []},
        },
    )
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    claim = _claim("C-1", "generator-reset-path")
    claim["claim"] = "The reset path reaches recovery."
    _write_json(
        artifacts / "claim_ledger.json",
        {
            "kind": "claim_evidence_ledger",
            "schema_version": "claim-evidence-ledger-v3",
            "claims": [claim],
        },
    )
    _write_json(artifacts / "evidence_cards.json", [_card("EV-C-1")])
    _write_json(artifacts / "quality_accuracy_policy.json", {"l3_validation": _l3()})
    candidates, scenarios = _generated_for(universe)
    for item in candidates["items"]:
        item["narrative"] = "The cited behavior covers this source-backed obligation."
    for item in scenarios["items"]:
        item["narrative"] = "The scenario realizes this source-backed obligation."
    _write_json(
        artifacts / "quality_breadth.json",
        {
            "scenario_candidates": candidates,
            "scenarios": scenarios,
            "dispositions": [],
        },
    )
    depth_payload = depth_candidate(truth)
    depth_refs = {}
    for binding in catalog.bindings:
        if binding.category.value == "l3":
            continue
        depth_refs.setdefault(
            (binding.chain_id, binding.category.value, binding.obligation_id), []
        ).append(binding.evidence_ref)
    for chain in depth_payload["chains"]:
        for category, field, id_field in (
            ("node", "nodes", "node_id"),
            ("edge", "edges", "edge_id"),
            ("check", "disconfirming_checks", "check_id"),
        ):
            for item in chain[field]:
                item["narrative"] = "The cited behavior closes this obligation."
                item["evidence_refs"] = depth_refs[
                    (chain["chain_id"], category, item[id_field])
                ]
    _write_json(artifacts / "quality_depth_candidate.json", depth_payload)

    semantic_judge = _DeterministicBatchSemanticJudge()
    audit_sink = []

    snapshot = module.evaluate_artifact_snapshot(
        case_path=case_dir / "case.json",
        registry=_Registry(),
        artifacts_dir=artifacts,
        source_dir=tmp_path,
        generator_model="generator-model",
        judge_model="judge-model",
        mode="rapid",
        deadline_monotonic=time.monotonic() + 10,
        semantic_judge=semantic_judge,
        semantic_audit_sink=audit_sink,
        snapshot_label="first_pass",
    )

    assert snapshot.accuracy.status.value == "pass"
    assert snapshot.breadth.status.value == "pass"
    assert snapshot.depth.status.value == "pass"
    assert len(semantic_judge.calls) == 1
    assert {
        judgment.axis for judgment in semantic_judge.calls[0]["judgments"]
    } == {"accuracy", "breadth", "depth"}
    assert audit_sink[0]["judge"]["model"] == "fixture-independent-judge"


def test_cli_selectors_are_mutually_exclusive() -> None:
    module = _runner()
    parser = module.build_benchmark_argument_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["--case", "a", "--all", "--run-artifacts", "x", "--output", "y"])


def test_cli_uses_explicit_independent_judge_model_default(monkeypatch) -> None:
    module = _runner()
    monkeypatch.delenv("CODETALK_QUALITY_JUDGE_MODEL", raising=False)

    args = module.build_benchmark_argument_parser().parse_args(
        ["--case", "case", "--output", "output"]
    )

    assert args.model == "gpt-5.6-sol"
    assert args.judge_model == "gpt-5.5"


@pytest.mark.parametrize(
    ("domain", "expected_projects"),
    [
        ("rdma", {"rdma-core", "ucx", "perftest"}),
        ("roce", {"ucx", "perftest"}),
        ("rdma-roce", {"rdma-core", "ucx", "perftest"}),
    ],
)
def test_cli_network_domain_selectors_distinguish_explicit_roce_cases(
    tmp_path, domain, expected_projects
) -> None:
    module = _runner()
    registry_path = tmp_path / "registry.json"
    registry_path.write_text("{}", encoding="utf-8")
    projects = []
    for project_id in ("rdma-core", "ucx", "perftest", "spdk"):
        case = tmp_path / "projects" / project_id / f"{project_id}-case" / "case.json"
        case.parent.mkdir(parents=True)
        case.write_text(
            json.dumps({"case_id": f"{project_id}-case", "project_id": project_id}),
            encoding="utf-8",
        )
        projects.append(type("Project", (), {"id": project_id})())
    registry = type("Registry", (), {"projects": projects})()

    selected = module._select_case_paths(
        registry_path=registry_path,
        registry=registry,
        case_selector=None,
        domain_selector=domain,
        select_all=False,
    )

    assert {path.parent.parent.name for path in selected} == expected_projects


def test_cli_rejects_external_copy_of_registered_case(tmp_path) -> None:
    module = _runner()
    registry_path = tmp_path / "registry.json"
    registry_path.write_text("{}", encoding="utf-8")
    registered = tmp_path / "projects" / "spdk" / "case-1" / "case.json"
    registered.parent.mkdir(parents=True)
    registered.write_text(
        json.dumps({"case_id": "case-1", "project_id": "spdk"}),
        encoding="utf-8",
    )
    copied = tmp_path / "external" / "case.json"
    copied.parent.mkdir()
    copied.write_bytes(registered.read_bytes())
    registry = type(
        "Registry",
        (),
        {"projects": [type("Project", (), {"id": "spdk"})()]},
    )()

    with pytest.raises(ValueError, match="registered case"):
        module._select_case_paths(
            registry_path=registry_path,
            registry=registry,
            case_selector=str(copied),
            domain_selector=None,
            select_all=False,
        )


def test_cli_rejects_precomputed_file_that_would_bypass_selector_semantics(
    tmp_path,
) -> None:
    module = _runner()
    report = tmp_path / "report.json"
    report.write_text("{}", encoding="utf-8")

    with pytest.raises(SystemExit, match="directory"):
        module.main(
            [
                "--all",
                "--source-root",
                str(tmp_path),
                "--run-artifacts",
                str(report),
                "--output",
                str(tmp_path / "output"),
            ]
        )


def test_directory_publish_is_atomic_no_replace_even_for_empty_destination(
    tmp_path,
) -> None:
    module = _runner()
    staging = tmp_path / "staging"
    destination = tmp_path / "destination"
    staging.mkdir()
    destination.mkdir()
    (staging / "report.json").write_text("new", encoding="utf-8")

    with pytest.raises(FileExistsError):
        module._rename_directory_noreplace(staging, destination)
    assert (staging / "report.json").read_text(encoding="utf-8") == "new"
    assert list(destination.iterdir()) == []


def test_directory_publish_fails_closed_without_native_no_replace(
    tmp_path, monkeypatch
) -> None:
    module = _runner()
    staging = tmp_path / "staging"
    destination = tmp_path / "destination"
    staging.mkdir()
    monkeypatch.setattr(module.sys, "platform", "unsupported")

    with pytest.raises(OSError) as raised:
        module._rename_directory_noreplace(staging, destination)

    assert raised.value.errno == errno.ENOTSUP
    assert staging.is_dir()
    assert not destination.exists()


def test_evaluator_aligns_public_source_evidence_without_leaking_truth_ids() -> None:
    module = _runner()
    gold = [{
        "gold_id": "hidden-gold",
        "semantic_key": "canonical.semantic.key",
        "claim": "The storage reset appends the pending request to the reset queue.",
        "evidence_refs": ["source://lib/storage.c#L10-L20"],
    }]
    ledger = {
        "claims": [{
            "claim_id": "public-claim",
            "claim": "A pending storage reset appends the request to the reset queue.",
            "semantic_key": "generator-wording",
            "l2_status": "not_checked",
            "evidence_refs": [{"path": "lib/storage.c", "start_line": 10, "end_line": 20}],
        }],
    }

    aligned = module._align_claim_semantics_from_evidence(
        ledger,
        gold,
        semantic_verdict_adapter=_IndependentAcceptingVerdictAdapter(),
    )

    assert aligned["claims"][0]["semantic_key"] == "canonical.semantic.key"
    assert aligned["claims"][0]["l2_status"] == "supports"
    assert "hidden-gold" not in json.dumps(aligned)
    assert ledger["claims"][0]["semantic_key"] == "generator-wording"


def test_evaluator_rejects_a_contradiction_even_when_it_cites_the_exact_gold_range() -> None:
    module = _runner()
    gold = [{
        "gold_id": "hidden-gold",
        "semantic_key": "canonical.semantic.key",
        "claim": "The storage reset appends the pending request to the reset queue.",
        "evidence_refs": ["source://lib/storage.c#L10-L20"],
    }]
    ledger = {
        "claims": [{
            "claim_id": "public-claim",
            "claim": "The storage reset does not append the pending request to the reset queue.",
            "semantic_key": "generator-wording",
            "l2_status": "supports",
            "evidence_refs": [{"path": "lib/storage.c", "start_line": 10, "end_line": 20}],
        }],
    }

    aligned = module._align_claim_semantics_from_evidence(ledger, gold)

    assert aligned["claims"][0]["semantic_key"] == "canonical.semantic.key"
    assert aligned["claims"][0]["l2_status"] == "contradicts"


def test_production_semantic_matcher_accepts_provable_word_order_and_voice_paraphrase() -> None:
    module = _runner()
    gold = [{
        "gold_id": "hidden-gold",
        "semantic_key": "canonical.semantic.key",
        "claim": "The storage reset appends the pending request to the reset queue.",
        "evidence_refs": ["source://lib/storage.c#L10-L20"],
    }]
    ledger = {
        "claims": [{
            "claim_id": "public-claim",
            "claim": "A pending request is appended to the reset queue by the storage reset.",
            "semantic_key": "generator-wording",
            "l2_status": "not_checked",
            "evidence_refs": [{
                "path": "lib/storage.c",
                "start_line": 10,
                "end_line": 20,
            }],
        }],
    }

    aligned = module._align_claim_semantics_from_evidence(ledger, gold)

    assert aligned["claims"][0]["semantic_key"] == "canonical.semantic.key"
    assert aligned["claims"][0]["l2_status"] == "supports"


def test_production_semantic_matcher_reads_kv_statement_field() -> None:
    module = _runner()
    statement = (
        "get_blocking returns the stored MemoryObj for an existing key and increments "
        "its reference count for the caller."
    )
    gold = [{
        "gold_id": "hidden-gold",
        "semantic_key": "lmcache.local_cpu.get.same_object_refcount_increment",
        "statement": statement,
        "evidence_refs": ["source://lmcache/local_cpu_backend.py#L211-L223"],
    }]
    ledger = {
        "claims": [{
            "claim_id": "public-claim",
            "claim": statement,
            "semantic_key": "generator-wording",
            "l2_status": "supports",
            "evidence_refs": [{
                "path": "lmcache/local_cpu_backend.py",
                "start_line": 211,
                "end_line": 223,
            }],
        }],
    }

    aligned = module._align_claim_semantics_from_evidence(ledger, gold)

    assert aligned["claims"][0]["semantic_key"] == gold[0]["semantic_key"]
    assert aligned["claims"][0]["l2_status"] == "supports"


def test_accuracy_prefers_unique_source_evidence_over_shared_test_range() -> None:
    module = _runner()
    shared = "test://tests/master_service_test.cpp#L1730-L1740"
    owner_statement = "PutEnd marks the owning client's replica COMPLETE."
    remove_statement = "Remove erases metadata and releases replica quota."
    gold = [
        {
            "gold_id": "hidden-owner",
            "semantic_key": "mooncake.owner.commit",
            "statement": owner_statement,
            "evidence_refs": [
                "source://src/master_service.cpp#L3768-L3793",
                shared,
            ],
        },
        {
            "gold_id": "hidden-remove",
            "semantic_key": "mooncake.remove.lifecycle",
            "statement": remove_statement,
            "evidence_refs": [
                "source://src/master_service.cpp#L1887-L1954",
                shared,
            ],
        },
    ]
    ledger = {
        "claims": [{
            "claim_id": "public-owner",
            "claim": owner_statement,
            "semantic_key": "generator-wording",
            "l2_status": "not_checked",
            "evidence_refs": [
                {"path": "src/master_service.cpp", "start_line": 3768, "end_line": 3793},
                {"path": "tests/master_service_test.cpp", "start_line": 1730, "end_line": 1740},
            ],
        }],
    }

    aligned = module._align_claim_semantics_from_evidence(ledger, gold)

    assert aligned["claims"][0]["semantic_key"] == "mooncake.owner.commit"
    assert aligned["claims"][0]["l2_status"] == "supports"


def test_accuracy_requires_the_complete_gold_evidence_set_before_support() -> None:
    module = _runner()
    shared = "test://tests/master_service_test.cpp#L1730-L1740"
    gold = [{
        "gold_id": "hidden-owner",
        "semantic_key": "mooncake.owner.commit",
        "statement": "PutEnd marks the owning client's replica COMPLETE.",
        "evidence_refs": [
            "source://src/master_service.cpp#L3768-L3793",
            shared,
        ],
    }]
    shared_only = {
        "claims": [{
            "claim_id": "public-owner",
            "claim": "PutEnd marks the owning client's replica COMPLETE.",
            "semantic_key": "generator-wording",
            "l2_status": "not_checked",
            "evidence_refs": [{
                "path": "tests/master_service_test.cpp",
                "start_line": 1730,
                "end_line": 1740,
            }],
        }],
    }

    aligned = module._align_claim_semantics_from_evidence(
        shared_only,
        gold,
        semantic_verdict_adapter=_IndependentAcceptingVerdictAdapter(),
    )

    assert aligned["claims"][0]["semantic_key"] == "generator-wording"
    assert aligned["claims"][0]["l2_status"] == "insufficient"


def test_accuracy_rejects_candidate_that_cites_unique_evidence_for_two_claims() -> None:
    module = _runner()
    gold = [
        {
            "gold_id": "hidden-owner",
            "semantic_key": "mooncake.owner.commit",
            "statement": "PutEnd marks the owning client's replica COMPLETE.",
            "evidence_refs": ["source://src/master_service.cpp#L3768-L3793"],
        },
        {
            "gold_id": "hidden-remove",
            "semantic_key": "mooncake.remove.lifecycle",
            "statement": "Remove erases metadata and releases replica quota.",
            "evidence_refs": ["source://src/master_service.cpp#L1887-L1954"],
        },
    ]
    ledger = {
        "claims": [{
            "claim_id": "public-ambiguous",
            "claim": "PutEnd marks the replica COMPLETE and Remove releases quota.",
            "semantic_key": "generator-wording",
            "l2_status": "supports",
            "evidence_refs": [
                {"path": "src/master_service.cpp", "start_line": 3768, "end_line": 3793},
                {"path": "src/master_service.cpp", "start_line": 1887, "end_line": 1954},
            ],
        }],
    }

    aligned = module._align_claim_semantics_from_evidence(ledger, gold)

    assert aligned["claims"][0]["semantic_key"] == "generator-wording"
    assert aligned["claims"][0]["l2_status"] == "insufficient"


@pytest.mark.parametrize(
    ("gold_claim", "reversed_claim"),
    [
        (
            "When a second bdev reset reaches the app-thread reset handler while the "
            "controller reset function reports -EBUSY, its bdev I/O is appended to "
            "pending_resets and the handler returns.",
            "The second bdev reset remains outside pending_resets because the append is "
            "bypassed when the controller reports -EBUSY.",
        ),
        (
            "Completing a controller reset drains pending_resets, removes each queued "
            "bdev I/O, and resumes it with success or failure derived from the "
            "controller-reset outcome.",
            "Completing a controller reset leaves pending_resets populated and suppresses "
            "resumption of every queued bdev I/O.",
        ),
        (
            "test_pending_reset covers both successful concurrent reset completion and a "
            "controller-removal failure path for both reset I/Os.",
            "test_pending_reset skips both successful completion and controller-removal "
            "failure paths for the reset I/Os.",
        ),
    ],
)
def test_spdk_reversed_claims_fail_closed_despite_exact_gold_ranges(
    gold_claim, reversed_claim
) -> None:
    module = _runner()
    gold = [{
        "gold_id": "hidden-gold",
        "semantic_key": "canonical.semantic.key",
        "claim": gold_claim,
        "evidence_refs": ["source://module/bdev/nvme/bdev_nvme.c#L10-L20"],
    }]
    ledger = {
        "claims": [{
            "claim_id": "public-claim",
            "claim": reversed_claim,
            "semantic_key": "generator-wording",
            "l2_status": "supports",
            "evidence_refs": [{
                "path": "module/bdev/nvme/bdev_nvme.c",
                "start_line": 10,
                "end_line": 20,
            }],
        }],
    }

    aligned = module._align_claim_semantics_from_evidence(ledger, gold)

    assert aligned["claims"][0]["l2_status"] in {"contradicts", "insufficient"}


def test_evaluator_fails_closed_when_hidden_gold_has_no_semantic_statement() -> None:
    module = _runner()
    gold = [{
        "gold_id": "hidden-gold",
        "semantic_key": "canonical.semantic.key",
        "claim": "",
        "evidence_refs": ["source://lib/storage.c#L10-L20"],
    }]
    ledger = {
        "claims": [{
            "claim_id": "public-claim",
            "claim": "Anything at all.",
            "semantic_key": "generator-wording",
            "l2_status": "supports",
            "evidence_refs": [{"path": "lib/storage.c", "start_line": 10, "end_line": 20}],
        }],
    }

    aligned = module._align_claim_semantics_from_evidence(ledger, gold)

    assert aligned["claims"][0]["l2_status"] == "insufficient"


def test_task_run_redaction_preserves_only_public_critical_aliases() -> None:
    module = _runner()

    projected = module._redact_truth_derived_fields(
        {
            "critical_misses": [{
                "item_id": "hidden-chain-node",
                "reason": "hidden answer",
                "validation_layer": "L3",
                "evidence_refs": ["source://secret.c#L1-L2"],
            }],
        }
    )

    assert projected == {
        "critical_misses": [{
            "item_id": "public-critical-obligation-1",
            "reason": "critical obligation is not satisfied",
            "validation_layer": "L3",
            "evidence_refs": [],
        }],
    }


def test_evaluator_aligns_public_breadth_refs_only_after_independent_semantic_verdict() -> None:
    module = _runner()
    universe = {
        "items": [{
            "item_id": "hidden-universe-item",
            "evidence_refs": ["source://lib/storage.c#branch:L30-L35"],
        }],
    }
    candidates = {
        "items": [{
            "candidate_id": "public-candidate",
            "evidence_refs": ["source://lib/storage.c#L30-L35"],
        }],
    }

    (aligned,) = module._align_breadth_evidence_refs(
        universe,
        candidates,
        semantic_verdict_adapter=_IndependentAcceptingVerdictAdapter(),
    )

    assert aligned["items"][0]["evidence_refs"] == [
        "source://lib/storage.c#branch:L30-L35"
    ]
    assert "hidden-universe-item" not in json.dumps(aligned)


def test_breadth_prefers_unique_evidence_before_judging_shared_ranges() -> None:
    module = _runner()
    shared = "test://tests/storage_test.py#shared:L50-L60"
    universe = {
        "items": [
            {
                "item_id": "hidden-flow",
                "dimension": "flows",
                "evidence_refs": [
                    "source://lib/storage.py#flow:L10-L20",
                    shared,
                ],
            },
            {
                "item_id": "hidden-resource",
                "dimension": "resources",
                "evidence_refs": [
                    "source://lib/storage.py#resource:L30-L40",
                    shared,
                ],
            },
        ],
    }
    candidates = {
        "items": [{
            "candidate_id": "public-flow",
            "narrative": "The request follows the complete storage flow.",
            "evidence_refs": [
                "source://lib/storage.py#L10-L20",
                "test://tests/storage_test.py#L50-L60",
            ],
        }],
    }

    (aligned,) = module._align_breadth_evidence_refs(
        universe,
        candidates,
        semantic_verdict_adapter=_IndependentAcceptingVerdictAdapter(),
    )

    assert aligned["items"][0]["evidence_refs"] == [
        "source://lib/storage.py#flow:L10-L20",
        shared,
    ]


def test_breadth_reversed_narrative_does_not_close_by_source_range_alone() -> None:
    module = _runner()
    universe = {
        "items": [{
            "item_id": "hidden-universe-item",
            "evidence_refs": ["source://lib/storage.c#branch:L30-L35"],
        }],
    }
    candidates = {
        "items": [{
            "candidate_id": "public-candidate",
            "narrative": "The branch does the exact opposite and never reaches recovery.",
            "status": "covered",
            "evidence_refs": ["source://lib/storage.c#L30-L35"],
        }],
    }

    (aligned,) = module._align_breadth_evidence_refs(universe, candidates)

    assert aligned["items"][0]["evidence_refs"] == []


def test_one_public_breadth_observation_cannot_close_two_truth_obligations() -> None:
    module = _runner()
    universe = {
        "items": [
            {
                "item_id": "hidden-trigger",
                "evidence_refs": ["source://lib/storage.c#trigger:L30-L35"],
            },
            {
                "item_id": "hidden-api-entry",
                "evidence_refs": ["source://lib/storage.c#api-entry:L30-L35"],
            },
        ],
    }
    candidates = {
        "items": [{
            "candidate_id": "one-public-observation",
            "narrative": "A single observation cannot establish two typed obligations.",
            "evidence_refs": ["source://lib/storage.c#L30-L35"],
        }],
    }

    (aligned,) = module._align_breadth_evidence_refs(
        universe,
        candidates,
        semantic_verdict_adapter=_IndependentAcceptingVerdictAdapter(),
    )

    assert aligned["items"][0]["evidence_refs"] == []


def test_evaluator_aligns_public_depth_observations_after_independent_semantic_verdict() -> None:
    module = _runner()
    catalog = module.DepthEvidenceCatalog.model_validate({
        "case_id": "case",
        "bindings": [{
            "evidence_ref": "source://lib/storage.c#L40-L45:node-hidden",
            "chain_id": "hidden-chain",
            "category": "node",
            "obligation_id": "hidden-node",
        }],
    })
    truth = {
        "chains": [{
            "chain_id": "hidden-chain",
            "nodes": [{"node_id": "hidden-node"}],
            "edges": [],
            "disconfirming_checks": [],
        }],
    }
    candidate = {
        "chains": [{
            "chain_id": "public-chain",
            "nodes": [{
                "node_id": "public-node",
                "status": "closed",
                "evidence_refs": ["source://lib/storage.c#L40-L45"],
            }],
            "edges": [],
            "disconfirming_checks": [],
        }],
    }

    aligned = module._align_depth_candidate_from_evidence(
        truth,
        candidate,
        catalog,
        semantic_verdict_adapter=_IndependentAcceptingVerdictAdapter(),
    )

    assert aligned["chains"][0]["nodes"] == [{
        "node_id": "hidden-node",
        "status": "closed",
        "evidence_refs": ["source://lib/storage.c#L40-L45:node-hidden"],
    }]
    assert "public-node" not in json.dumps(aligned)


def test_depth_edge_semantic_oracle_includes_statement_and_typed_endpoints() -> None:
    module = _runner()
    edge = {
        "edge_id": "edge-state-to-error",
        "source_node_id": "state-mutation",
        "target_node_id": "error-propagation",
        "statement": "A failed state mutation propagates the operation error.",
    }

    oracle = module._axis_oracle_statement("depth", edge, None)

    assert "A failed state mutation propagates the operation error." in oracle
    assert "source_node_id=state-mutation" in oracle
    assert "target_node_id=error-propagation" in oracle


def test_depth_obligation_can_require_multiple_individually_valid_ranges() -> None:
    module = _runner()
    catalog = module.DepthEvidenceCatalog.model_validate({
        "case_id": "case",
        "bindings": [
            {
                "evidence_ref": "source://lib/storage.c#first:L10-L20",
                "chain_id": "hidden-chain",
                "category": "node",
                "obligation_id": "hidden-node",
            },
            {
                "evidence_ref": "source://lib/storage.c#second:L30-L40",
                "chain_id": "hidden-chain",
                "category": "node",
                "obligation_id": "hidden-node",
            },
        ],
    })
    truth = {
        "chains": [{
            "chain_id": "hidden-chain",
            "nodes": [{"node_id": "hidden-node", "kind": "trigger"}],
            "edges": [],
            "disconfirming_checks": [],
        }],
    }
    candidate = {
        "chains": [{
            "chain_id": "public-chain",
            "nodes": [{
                "node_id": "public-node",
                "status": "closed",
                "narrative": "Both halves establish the trigger.",
                "evidence_refs": [
                    "source://lib/storage.c#L10-L20",
                    "source://lib/storage.c#L30-L40",
                ],
            }],
            "edges": [],
            "disconfirming_checks": [],
            "narrative": "public chain",
        }],
    }

    aligned = module._align_depth_candidate_from_evidence(
        truth,
        candidate,
        catalog,
        semantic_verdict_adapter=_IndependentAcceptingVerdictAdapter(),
    )

    assert aligned["chains"][0]["nodes"][0]["evidence_refs"] == [
        "source://lib/storage.c#first:L10-L20",
        "source://lib/storage.c#second:L30-L40",
    ]


def test_depth_reversed_narrative_does_not_close_by_source_range_alone() -> None:
    module = _runner()
    catalog = module.DepthEvidenceCatalog.model_validate({
        "case_id": "case",
        "bindings": [{
            "evidence_ref": "source://lib/storage.c#L40-L45:node-hidden",
            "chain_id": "hidden-chain",
            "category": "node",
            "obligation_id": "hidden-node",
        }],
    })
    truth = {
        "chains": [{
            "chain_id": "hidden-chain",
            "nodes": [{"node_id": "hidden-node"}],
            "edges": [],
            "disconfirming_checks": [],
        }],
    }
    candidate = {
        "chains": [{
            "chain_id": "public-chain",
            "narrative": "The trigger never executes and cannot reach the next state.",
            "nodes": [{
                "node_id": "public-node",
                "status": "closed",
                "narrative": "The node is unreachable.",
                "evidence_refs": ["source://lib/storage.c#L40-L45"],
            }],
            "edges": [],
            "disconfirming_checks": [],
        }],
    }

    aligned = module._align_depth_candidate_from_evidence(truth, candidate, catalog)

    assert aligned["chains"][0]["nodes"] == []


def test_one_public_depth_observation_cannot_close_two_truth_obligations() -> None:
    module = _runner()
    catalog = module.DepthEvidenceCatalog.model_validate({
        "case_id": "case",
        "bindings": [
            {
                "evidence_ref": "source://lib/storage.c#L40-L45:first",
                "chain_id": "hidden-chain",
                "category": "node",
                "obligation_id": "hidden-first",
            },
            {
                "evidence_ref": "source://lib/storage.c#L40-L45:second",
                "chain_id": "hidden-chain",
                "category": "node",
                "obligation_id": "hidden-second",
            },
        ],
    })
    truth = {
        "chains": [{
            "chain_id": "hidden-chain",
            "nodes": [
                {"node_id": "hidden-first"},
                {"node_id": "hidden-second"},
            ],
            "edges": [],
            "disconfirming_checks": [],
        }],
    }
    candidate = {
        "chains": [{
            "chain_id": "public-chain",
            "nodes": [{
                "node_id": "one-public-observation",
                "status": "closed",
                "evidence_refs": ["source://lib/storage.c#L40-L45"],
            }],
            "edges": [],
            "disconfirming_checks": [],
        }],
    }

    aligned = module._align_depth_candidate_from_evidence(
        truth,
        candidate,
        catalog,
        semantic_verdict_adapter=_IndependentAcceptingVerdictAdapter(),
    )

    assert aligned["chains"][0]["nodes"] == []


def test_generator_authored_l3_is_discarded_in_favor_of_evaluator_result() -> None:
    module = _runner()
    truth = {
        "chains": [
            {
                "chain_id": "hidden-chain",
                "nodes": [],
                "edges": [],
                "disconfirming_checks": [],
            }
        ]
    }
    candidate = {
        "chains": [],
        "l3": {
            "status": "pass",
            "chain_evidence": [
                {
                    "chain_id": "hidden-chain",
                    "evidence_refs": ["oracle://generator-authored#pass"],
                }
            ],
            "limitations": [],
        },
    }
    catalog = module.DepthEvidenceCatalog.model_validate(
        {
            "case_id": "case",
            "bindings": [
                {
                    "evidence_ref": "oracle://evaluator#not-run",
                    "chain_id": "hidden-chain",
                    "category": "l3",
                    "obligation_id": "execution",
                }
            ],
        }
    )
    evaluator_l3 = {
        "status": "not_run",
        "chain_evidence": [],
        "limitations": ["L3_NOT_RUN:TIER_E", "ENVIRONMENT_UNAVAILABLE"],
    }

    aligned = module._align_depth_candidate_from_evidence(
        truth,
        candidate,
        catalog,
        evaluator_l3=evaluator_l3,
    )

    assert aligned["l3"] == evaluator_l3
    assert "generator-authored" not in json.dumps(aligned)


def test_cli_case_selector_executes_runner_and_publishes_public_task_report(
    tmp_path, monkeypatch
) -> None:
    module = _runner()
    case_path = tmp_path / "case.json"
    case_path.write_text("{}", encoding="utf-8")
    run_artifacts = tmp_path / "run"
    (run_artifacts / "first_pass").mkdir(parents=True)
    (run_artifacts / "final_after_auto_repair").mkdir()
    (run_artifacts / "repair_summary.json").write_text(
        json.dumps(
            {
                "attempt_count": 0,
                "elapsed_seconds": 0,
                "terminal_block_reason": None,
                "first_provenance": {"attempt": 0},
                "final_provenance": {"attempt": 0},
            }
        ),
        encoding="utf-8",
    )
    repair_summary_bytes = (run_artifacts / "repair_summary.json").read_bytes()
    (run_artifacts / "versions.json").write_text(
        '{"model":"m","codetalk":"c","evaluator":"e"}', encoding="utf-8"
    )
    source_root = tmp_path / "sources"
    source_root.mkdir()
    output = tmp_path / "output"
    calls = []

    monkeypatch.setattr(module, "load_quality_registry", lambda _: _Registry())
    monkeypatch.setattr(module, "_select_case_paths", lambda **_: [case_path])
    monkeypatch.setattr(
        module,
        "run_quality_benchmark_case",
        lambda **kwargs: calls.append(kwargs)
        or module.BenchmarkRunResult(
            output / module.REPORT_FILENAME,
            output / module.MANIFEST_FILENAME,
            "a" * 64,
        ),
    )
    monkeypatch.setattr(module, "_publish_task_run_projection", lambda **kwargs: calls.append(kwargs))

    assert module.main(
        [
            "--case",
            str(case_path),
            "--source-root",
            str(source_root),
            "--run-artifacts",
            str(run_artifacts),
            "--output",
            str(output),
        ]
    ) == 0
    assert calls[0]["case_path"] == case_path
    assert calls[0]["source_root"] == source_root
    assert calls[0]["started_monotonic"] < calls[0]["deadline_monotonic"]
    assert (
        calls[0]["deadline_monotonic"] - calls[0]["started_monotonic"]
        == module._quality_generation_timeout("rapid")
    )
    assert calls[0]["judge_model"] == "gpt-5.5"
    assert calls[0]["repair_summary"] == {
        "attempt_count": 0,
        "elapsed_seconds": 0,
        "terminal_block_reason": None,
    }
    assert (run_artifacts / "repair_summary.json").read_bytes() == repair_summary_bytes
    assert calls[1]["deadline_monotonic"] == calls[0]["deadline_monotonic"]
    assert calls[1]["task_run_dir"] == run_artifacts


def test_cli_generated_single_case_uses_direct_immutable_generator_root(
    tmp_path, monkeypatch
) -> None:
    module = _runner()
    case_path = tmp_path / "case.json"
    case_path.write_text('{"case_id":"case-1","project_id":"project-1"}')
    source_root = tmp_path / "sources"
    source_root.mkdir()
    output = tmp_path / "evaluation"
    generated_roots = []

    monkeypatch.setattr(module, "load_quality_registry", lambda _: _Registry())
    monkeypatch.setattr(module, "_select_case_paths", lambda **_: [case_path])
    monkeypatch.setattr(
        module,
        "resolve_quality_project",
        lambda *_args, **_kwargs: type("Project", (), {"path": source_root})(),
    )
    monkeypatch.setattr(module, "_quality_case_truth_paths", lambda *_args, **_kwargs: ())

    def fake_generate(**kwargs):
        root = Path(kwargs["output_dir"])
        generated_roots.append(root)
        (root / "first_pass").mkdir(parents=True)
        (root / "final_after_auto_repair").mkdir()
        (root / "repair_summary.json").write_text(
            '{"attempt_count":0,"elapsed_seconds":1,"terminal_block_reason":null}',
            encoding="utf-8",
        )
        (root / "versions.json").write_text(
            '{"model":"gpt-5.6-sol","codetalk":"c","evaluator":"e"}',
            encoding="utf-8",
        )

    monkeypatch.setattr(module, "generate_quality_benchmark_artifacts", fake_generate)
    monkeypatch.setattr(
        module,
        "_benchmark_execution_manifest",
        lambda _root: {"status": "completed"},
    )
    monkeypatch.setattr(
        module,
        "run_quality_benchmark_case",
        lambda **_kwargs: module.BenchmarkRunResult(
            output / module.REPORT_FILENAME,
            output / module.MANIFEST_FILENAME,
            "a" * 64,
        ),
    )

    def unexpected_projection(**_kwargs):
        pytest.fail("generated immutable evidence must not receive a public projection")

    monkeypatch.setattr(module, "_publish_task_run_projection", unexpected_projection)

    assert module.main(
        [
            "--case",
            str(case_path),
            "--source-root",
            str(source_root),
            "--output",
            str(output),
        ]
    ) == 0
    assert generated_roots == [Path(f"{output}.run-artifacts")]


def test_cli_multi_case_fresh_and_explicit_replay_keep_roots_separate(
    tmp_path, monkeypatch
) -> None:
    module = _runner()
    case_paths = []
    for case_id in ("case-1", "case-2"):
        case_path = tmp_path / f"{case_id}.json"
        case_path.write_text(
            json.dumps({"case_id": case_id, "project_id": f"project-{case_id}"})
        )
        case_paths.append(case_path)
    source_root = tmp_path / "sources"
    source_root.mkdir()
    generated_roots = []
    evaluation_roots = []
    projection_roots = []

    monkeypatch.setattr(module, "load_quality_registry", lambda _: _Registry())
    monkeypatch.setattr(module, "_select_case_paths", lambda **_: case_paths)
    monkeypatch.setattr(
        module,
        "resolve_quality_project",
        lambda *_args, **_kwargs: type("Project", (), {"path": source_root})(),
    )
    monkeypatch.setattr(module, "_quality_case_truth_paths", lambda *_args, **_kwargs: ())

    def fake_generate(**kwargs):
        root = Path(kwargs["output_dir"])
        generated_roots.append(root)
        (root / "first_pass").mkdir(parents=True)
        (root / "final_after_auto_repair").mkdir()
        (root / "repair_summary.json").write_text(
            '{"attempt_count":0,"elapsed_seconds":1,"terminal_block_reason":null}'
        )
        (root / "versions.json").write_text(
            '{"model":"gpt-5.6-sol","codetalk":"c","evaluator":"e"}'
        )

    def fake_run(**kwargs):
        root = Path(kwargs["output_dir"])
        evaluation_roots.append(root)
        return module.BenchmarkRunResult(
            root / module.REPORT_FILENAME,
            root / module.MANIFEST_FILENAME,
            "a" * 64,
        )

    monkeypatch.setattr(module, "generate_quality_benchmark_artifacts", fake_generate)
    monkeypatch.setattr(module, "_benchmark_execution_manifest", lambda _root: {})
    monkeypatch.setattr(module, "run_quality_benchmark_case", fake_run)
    monkeypatch.setattr(
        module,
        "_publish_task_run_projection",
        lambda **kwargs: projection_roots.append(Path(kwargs["task_run_dir"])),
    )

    fresh_output = tmp_path / "fresh"
    assert module.main(
        ["--all", "--source-root", str(source_root), "--output", str(fresh_output)]
    ) == 0
    artifact_root = Path(f"{fresh_output}.run-artifacts")
    assert generated_roots == [artifact_root / "case-1", artifact_root / "case-2"]
    assert evaluation_roots == [fresh_output / "case-1", fresh_output / "case-2"]
    assert projection_roots == []

    generated_roots.clear()
    evaluation_roots.clear()
    replay_output = tmp_path / "replay"
    assert module.main(
        [
            "--all",
            "--source-root",
            str(source_root),
            "--run-artifacts",
            str(artifact_root),
            "--output",
            str(replay_output),
        ]
    ) == 0
    assert generated_roots == []
    assert evaluation_roots == [replay_output / "case-1", replay_output / "case-2"]
    assert projection_roots == [artifact_root / "case-1", artifact_root / "case-2"]


@pytest.mark.parametrize(
    "repair_summary",
    [
        [],
        {"elapsed_seconds": 0, "terminal_block_reason": None},
        {"attempt_count": -1, "elapsed_seconds": 0, "terminal_block_reason": None},
        {"attempt_count": 0, "elapsed_seconds": -1, "terminal_block_reason": None},
        {"attempt_count": "0", "elapsed_seconds": 0, "terminal_block_reason": None},
        {"attempt_count": 0, "elapsed_seconds": 0, "terminal_block_reason": ""},
        {"attempt_count": 0, "elapsed_seconds": 0, "terminal_block_reason": 7},
    ],
    ids=(
        "non-object",
        "missing-field",
        "negative-attempt-count",
        "negative-elapsed-seconds",
        "coerced-attempt-count",
        "empty-terminal-reason",
        "invalid-terminal-reason-type",
    ),
)
def test_cli_rejects_invalid_generator_repair_summary_before_evaluation(
    tmp_path, monkeypatch, repair_summary
) -> None:
    module = _runner()
    case_path = tmp_path / "case.json"
    case_path.write_text("{}", encoding="utf-8")
    run_artifacts = tmp_path / "run"
    (run_artifacts / "first_pass").mkdir(parents=True)
    (run_artifacts / "final_after_auto_repair").mkdir()
    (run_artifacts / "repair_summary.json").write_text(
        json.dumps(repair_summary), encoding="utf-8"
    )
    source_root = tmp_path / "sources"
    source_root.mkdir()

    monkeypatch.setattr(module, "load_quality_registry", lambda _: _Registry())
    monkeypatch.setattr(module, "_select_case_paths", lambda **_: [case_path])

    def unexpected_call(**_kwargs):
        pytest.fail("invalid repair metadata reached evaluation or publication")

    monkeypatch.setattr(module, "run_quality_benchmark_case", unexpected_call)
    monkeypatch.setattr(module, "_publish_task_run_projection", unexpected_call)

    with pytest.raises(ValueError):
        module.main(
            [
                "--case",
                str(case_path),
                "--source-root",
                str(source_root),
                "--run-artifacts",
                str(run_artifacts),
                "--output",
                str(tmp_path / "output"),
            ]
        )


def test_manifest_wall_clock_is_measured_from_runner_start_not_generator_elapsed(
    tmp_path, monkeypatch
) -> None:
    module = _runner()
    snapshots = [_snapshot(), _snapshot()]
    monkeypatch.setattr(module, "evaluate_artifact_snapshot", lambda **_: snapshots.pop(0))
    case_path = _case_tree(tmp_path)
    first = tmp_path / "first"
    final = tmp_path / "final"
    first.mkdir()
    final.mkdir()
    output = tmp_path / "output"
    started = time.monotonic() - 0.05

    module.run_quality_benchmark_case(
        case_path=case_path,
        registry=_Registry(),
        first_pass_artifacts=first,
        final_artifacts=final,
        output_dir=output,
        run_ref="run-timing",
        repair_summary={
            "attempt_count": 0,
            "elapsed_seconds": 0,
            "terminal_block_reason": None,
        },
        versions={"model": "fixture", "codetalk": "deadbeef", "evaluator": "v1"},
        execution={"profile": "rapid", "wall_clock_seconds": 777.0},
        started_monotonic=started,
        deadline_monotonic=started + 900,
    )

    manifest = json.loads((output / module.MANIFEST_FILENAME).read_text())
    assert 0.04 <= manifest["execution"]["wall_clock_seconds"] < 10
    assert manifest["execution"]["budget_seconds"] == 900
    assert manifest["execution"]["deadline_exceeded"] is False


def test_execution_manifest_rejects_non_deliverable_workbench_status(
    tmp_path,
) -> None:
    module = _runner()
    run_root = tmp_path / "generator"
    run_root.mkdir()
    (run_root / "generation_manifest.json").write_text(
        json.dumps(
            {
                "mode": "rapid",
                "elapsed_seconds": 20.0,
                "cache_reused": False,
                "workbench_status": "quality_blocked",
                "work_sufficiency": {
                    "status": "insufficient",
                    "auto_continue": False,
                    "reasons": ["claims_below_3"],
                },
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="non-deliverable Workbench status"):
        module._benchmark_execution_manifest(run_root)


def test_execution_manifest_binds_generator_artifact_root(tmp_path) -> None:
    module = _runner()
    run_root = tmp_path / "generator"
    run_root.mkdir()
    (run_root / "generation_manifest.json").write_text(
        json.dumps(
            {
                "mode": "rapid",
                "elapsed_seconds": 20.0,
                "cache_reused": False,
                "workbench_status": "completed",
                "work_sufficiency": {"status": "sufficient"},
            }
        ),
        encoding="utf-8",
    )
    (run_root / "artifact_hash_manifest.json").write_text(
        json.dumps({"root_sha256": "a" * 64}), encoding="utf-8"
    )

    execution = module._benchmark_execution_manifest(run_root)

    assert execution is not None
    assert execution["generator_artifact_root_sha256"] == "a" * 64


def test_task_run_projection_is_contract_valid_and_erases_truth_derived_ids(
    tmp_path,
) -> None:
    from app.services.quality_evaluator import build_quality_report

    module = _runner()
    full_report = build_quality_report(
        scope="independent_benchmark",
        run_ref="run-redacted",
        benchmark_identity={
            "case_id": "hidden-case",
            "source_revision": "a" * 40,
            "truth_package_version": "1",
        },
        first_pass=_snapshot(depth=AxisStatus.FAIL),
        final_after_auto_repair=_snapshot(depth=AxisStatus.FAIL),
        repair_summary={
            "attempt_count": 0,
            "elapsed_seconds": 0,
            "terminal_block_reason": None,
        },
    )
    full_path = tmp_path / "full.json"
    full_path.write_text(full_report.model_dump_json(by_alias=True), encoding="utf-8")
    task_run_dir = tmp_path / "task-run"
    task_run_dir.mkdir()

    public_path = module._publish_task_run_projection(
        report_path=full_path, task_run_dir=task_run_dir
    )
    encoded = public_path.read_text(encoding="utf-8")

    assert "depth:open" not in encoded
    assert "artifact://" not in encoded
    assert json.loads(encoded)["delivery_status"] == "not_ready"
