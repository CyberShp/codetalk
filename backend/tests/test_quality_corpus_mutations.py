from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

import pytest

from app.services.quality_benchmark_corpus import (
    QualityCorpusError,
    load_quality_case,
    load_quality_registry,
    validate_truth_isolation,
)
from app.services.quality_benchmark_runner import (
    _align_breadth_evidence_refs,
    _align_depth_candidate_from_evidence,
    _axis_oracle_statement,
    _normalized_evidence_ref,
    evaluate_artifact_snapshot,
)
from app.services.quality_benchmark_semantic_judge import (
    SemanticJudgment,
    _build_validation_request,
)
from app.services.quality_breadth_evaluator import evaluate_breadth
from app.services.quality_depth_evaluator import DepthEvidenceCatalog, evaluate_depth


REPO_ROOT = Path(__file__).parents[2]
REGISTRY_PATH = REPO_ROOT / "benchmarks" / "quality" / "registry.json"
PROJECTS_ROOT = REPO_ROOT / "benchmarks" / "quality" / "projects"
EXPECTED_PROJECT_IDS = {
    "spdk",
    "femu",
    "nvme-csd",
    "open-cas-linux",
    "phosphor-nvme",
    "phosphor-state-manager",
    "bmcweb",
    "lmcache",
    "mooncake",
    "rdma-core",
    "ucx",
    "perftest",
}

EXPECTED_APPLICABLE_BREADTH_DIMENSIONS = {
    "spdk": {"mutation"},
    "femu": {"protocol"},
    "nvme-csd": {"protocol"},
    "open-cas-linux": {"mutation"},
    "phosphor-nvme": {"mutation"},
    "phosphor-state-manager": {"mutation"},
    "bmcweb": {"protocol"},
    "lmcache": {"mutation"},
    "mooncake": {"mutation"},
    "rdma-core": {"protocol"},
    "ucx": {"protocol", "historical"},
    "perftest": {"protocol"},
}

SOURCE_ROOT = Path("/Volumes/Media/codetalk-quality-corpus/sources")


def _depth_catalog_payload(execution: dict[str, object]) -> dict[str, object]:
    nested = execution.get("evidence_catalog")
    if isinstance(nested, dict):
        return nested
    return {
        "case_id": execution["case_id"],
        "bindings": execution["bindings"],
    }


def test_all_twelve_pinned_projects_have_a_hash_verified_tier_s_case() -> None:
    registry = load_quality_registry(REGISTRY_PATH)
    case_paths = sorted(PROJECTS_ROOT.glob("*/*/case.json"))

    cases = [load_quality_case(path, registry=registry) for path in case_paths]

    assert {case.project_id for case in cases} == EXPECTED_PROJECT_IDS
    assert all(case.tier == "S" for case in cases)
    assert len(cases) == len(EXPECTED_PROJECT_IDS)


def test_all_registered_cases_expose_a_public_analysis_target_at_truth_version_two() -> None:
    registry = load_quality_registry(REGISTRY_PATH)
    case_paths = sorted(PROJECTS_ROOT.glob("*/*/case.json"))
    cases = [load_quality_case(path, registry=registry) for path in case_paths]

    assert registry.truth_package_version == "2"
    assert all(case.truth_package_version == "2" for case in cases)
    assert all(str(case.analysis_target).strip() for case in cases)
    for case_path in case_paths:
        universe = json.loads(
            (case_path.parent / "coverage_universe.json").read_text(encoding="utf-8")
        )
        assert all(str(item.get("statement") or "").strip() for item in universe["items"])


@pytest.mark.parametrize("project_id", sorted(EXPECTED_PROJECT_IDS))
def test_all_twelve_depth_truth_packages_have_judgeable_statements_and_endpoints(
    project_id: str,
) -> None:
    case_path = next(PROJECTS_ROOT.glob(f"{project_id}/*/case.json"))
    truth = json.loads((case_path.parent / "critical_chains.json").read_text())

    for chain in truth["chains"]:
        for node in chain["nodes"]:
            assert str(node.get("statement") or "").strip()
        for edge in chain["edges"]:
            assert str(edge.get("statement") or "").strip()
            assert str(edge.get("source_node_id") or "").strip()
            assert str(edge.get("target_node_id") or "").strip()
            oracle = _axis_oracle_statement("depth", edge, None)
            assert f"source_node_id={edge['source_node_id']}" in oracle
            assert f"target_node_id={edge['target_node_id']}" in oracle
        for check in chain["disconfirming_checks"]:
            assert str(check.get("statement") or "").strip()


@pytest.mark.parametrize("project_id", sorted(EXPECTED_PROJECT_IDS))
def test_all_twelve_depth_semantic_requests_materialize_against_pinned_source(
    project_id: str,
) -> None:
    case_path = next(PROJECTS_ROOT.glob(f"{project_id}/*/case.json"))
    truth = json.loads((case_path.parent / "critical_chains.json").read_text())
    execution = json.loads((case_path.parent / "execution_oracles.json").read_text())
    catalog = DepthEvidenceCatalog.model_validate(_depth_catalog_payload(execution))
    obligations = {}
    for chain in truth["chains"]:
        for category, field, id_field in (
            ("node", "nodes", "node_id"),
            ("edge", "edges", "edge_id"),
            ("check", "disconfirming_checks", "check_id"),
        ):
            for item in chain[field]:
                obligations[(chain["chain_id"], category, item[id_field])] = item
    grouped = {}
    for binding in catalog.bindings:
        if binding.category.value == "l3":
            continue
        key = (binding.chain_id, binding.category.value, binding.obligation_id)
        grouped.setdefault(key, []).append(binding.evidence_ref)
    judgments = []
    for index, (key, refs) in enumerate(sorted(grouped.items()), start=1):
        truth_item = obligations[key]
        judgments.append(
            SemanticJudgment(
                judgment_id=f"depth-{project_id}-{index}",
                axis="depth",
                candidate_statement="A source-backed public observation closes this obligation.",
                oracle_statement=(
                    str(truth_item.get("statement") or "").strip()
                    or f"The source closes {key[1]} obligation {key[2]}."
                ),
                observed_evidence_refs=tuple(refs),
                required_evidence_refs=tuple(refs),
            )
        )

    request = _build_validation_request(judgments, SOURCE_ROOT / project_id)

    assert request["requested_count"] == len(grouped)
    assert request["contexts"]


def test_applicable_protocol_history_and_mutation_obligations_are_committed() -> None:
    for project_id, expected_dimensions in EXPECTED_APPLICABLE_BREADTH_DIMENSIONS.items():
        case_path = next(PROJECTS_ROOT.glob(f"{project_id}/*/case.json"))
        universe = json.loads(
            (case_path.parent / "coverage_universe.json").read_text(encoding="utf-8")
        )
        applicable = [
            item
            for item in universe["items"]
            if item["dimension"] in expected_dimensions
        ]

        assert {item["dimension"] for item in applicable} == expected_dimensions
        assert all(item["applicability"] == "required" for item in applicable)
        assert all(item["critical"] is True for item in applicable)
        assert all(str(item.get("description") or "").strip() for item in applicable)
        assert all(item["evidence_refs"] for item in applicable)

    bmcweb = json.loads(
        next(PROJECTS_ROOT.glob("bmcweb/*/coverage_universe.json")).read_text()
    )
    assert any(
        item["dimension"] == "protocol" and "Redfish" in item["description"]
        for item in bmcweb["items"]
    )
    for project_id in ("ucx", "perftest"):
        universe = json.loads(
            next(PROJECTS_ROOT.glob(f"{project_id}/*/coverage_universe.json")).read_text()
        )
        assert any(
            item["dimension"] == "protocol" and "RoCE" in item["description"]
            for item in universe["items"]
        )
    rdma_core = json.loads(
        next(PROJECTS_ROOT.glob("rdma-core/*/coverage_universe.json")).read_text()
    )
    assert all(
        "RoCE" not in str(item.get("description") or "")
        for item in rdma_core["items"]
    )


def test_breadth_universe_evidence_ranges_have_single_obligation_owner() -> None:
    for universe_path in sorted(PROJECTS_ROOT.glob("*/*/coverage_universe.json")):
        universe = json.loads(universe_path.read_text(encoding="utf-8"))
        owners: dict[str, list[str]] = {}
        for item in universe["items"]:
            for evidence_ref in item["evidence_refs"]:
                owners.setdefault(evidence_ref, []).append(item["item_id"])

        assert {
            evidence_ref: item_ids
            for evidence_ref, item_ids in owners.items()
            if len(item_ids) > 1
        } == {}, universe_path


def test_phosphor_nvme_power_not_good_truth_preserves_inventory_write_gate() -> None:
    case_dir = (
        PROJECTS_ROOT
        / "phosphor-nvme"
        / "phosphor-nvme-present-power-not-good-001"
    )
    truth = json.loads((case_dir / "gold_claims.json").read_text(encoding="utf-8"))
    claim = next(
        item
        for item in truth["claims"]
        if item["semantic_key"]
        == "nvme.present.power_not_good.requests.fault_on.locate_off.and.inventory_present"
    )

    assert claim["claim"] == (
        "When the present signal reports a drive but power-good is false, "
        "Nvme::read requests the fault LED on, requests the locate LED off, and "
        "calls setNvmeInventoryProperties with Present set to true and default "
        "NVMe data. That helper writes the inventory Present property and asset "
        "fields only when the cached serial number differs from the sampled "
        "serial number; Nvme::read logs and latches the power error only if it "
        "was not already latched."
    )
    assert claim["evidence_refs"] == [
        "source://nvme_manager.cpp#L66-L92",
        "source://nvme_manager.cpp#L697-L722",
    ]


def test_mooncake_truth_uses_only_individually_parseable_line_range_refs() -> None:
    case_dir = (
        PROJECTS_ROOT
        / "mooncake"
        / "mooncake-store-put-commit-readiness-recovery-001"
    )
    for filename in ("coverage_universe.json", "execution_oracles.json"):
        payload = json.loads((case_dir / filename).read_text(encoding="utf-8"))
        serialized_refs = []

        def collect(value) -> None:
            if isinstance(value, dict):
                for key, nested in value.items():
                    if key in {"evidence_ref", "evidence_refs"}:
                        if isinstance(nested, str):
                            serialized_refs.append(nested)
                        elif isinstance(nested, list):
                            serialized_refs.extend(
                                item for item in nested if isinstance(item, str)
                            )
                    collect(nested)
            elif isinstance(value, list):
                for nested in value:
                    collect(nested)

        collect(payload)
        assert serialized_refs
        assert all(",L" not in ref for ref in serialized_refs)
        assert all(
            _normalized_evidence_ref(ref) is not None
            for ref in serialized_refs
            if ref.startswith(("source://", "test://"))
        )


def test_truth_content_mutation_is_rejected_before_any_generator_surface_is_used(
    tmp_path,
) -> None:
    registry = load_quality_registry(REGISTRY_PATH)
    source = next(PROJECTS_ROOT.glob("spdk/*/case.json")).parent
    copied = tmp_path / "spdk-case"
    shutil.copytree(source, copied)
    (copied / "gold_claims.json").write_text('{"tampered":true}\n')

    with pytest.raises(QualityCorpusError, match="sha256 mismatch"):
        load_quality_case(copied / "case.json", registry=registry)

    validate_truth_isolation(
        generator_surfaces={
            "task_input": {"target": "bdev reset"},
            "prompt_capture": "analyze the pinned source",
            "retrieval_index": [],
            "bundle": {"artifacts": ["report.md"]},
            "generator_manifest": {"allowed": ["report.md"]},
        },
        truth_paths=[
            copied / "gold_claims.json",
            copied / "coverage_universe.json",
            copied / "critical_chains.json",
            copied / "execution_oracles.json",
        ],
    )


def _public_ref(raw_ref: str) -> str:
    normalized = _normalized_evidence_ref(raw_ref)
    assert normalized is not None
    path, start, end = normalized
    return f"source://{path}#L{start}-L{end}"


class _SelectiveBatchSemanticJudge:
    """Deterministic batch double that rejects every explicitly reversed observation."""

    def __init__(self) -> None:
        self.calls = []

    def judge(self, *, judgments, snapshot_label, **kwargs):
        from app.services.quality_benchmark_semantic_judge import SemanticJudgeResult

        frozen = tuple(judgments)
        self.calls.append(
            {"judgments": frozen, "snapshot_label": snapshot_label, **kwargs}
        )
        verdicts = {}
        for judgment in frozen:
            candidate = judgment.candidate_statement.lower()
            oracle = judgment.oracle_statement.lower()
            if "the following statement is false" in candidate:
                verdict = "contradicts"
            elif judgment.axis in {"breadth", "depth"}:
                obligation = candidate.rsplit(" obligation ", 1)[-1].rstrip(".")
                verdict = (
                    "supports"
                    if obligation and obligation in oracle
                    else "insufficient"
                )
            else:
                verdict = "supports"
            verdicts[judgment.judgment_id] = verdict
        return SemanticJudgeResult(
            verdicts=verdicts,
            metadata={
                "schema_version": "quality-semantic-judge-audit-v1",
                "snapshot": snapshot_label,
                "status": "completed",
                "judge_version": "quality-semantic-judge-v1",
                "judge": {
                    "provider": "fixture",
                    "runtime_id": "selective-batch-fixture",
                    "model": "fixture-gpt-5.5",
                    "reasoning_effort": "deterministic",
                    "independent": True,
                },
                "request_sha256": "a" * 64,
                "result_sha256": "b" * 64,
            },
            limitations=(),
        )


def _write_dynamic_candidate(
    *,
    case_path: Path,
    output: Path,
    reverse_all: bool = False,
    claim_override: tuple[str, str] | None = None,
) -> None:
    gold = json.loads((case_path.parent / "gold_claims.json").read_text())
    universe = json.loads((case_path.parent / "coverage_universe.json").read_text())
    truth = json.loads((case_path.parent / "critical_chains.json").read_text())
    execution = json.loads((case_path.parent / "execution_oracles.json").read_text())
    catalog = DepthEvidenceCatalog.model_validate(_depth_catalog_payload(execution))
    output.mkdir()

    cards = []
    claims = []
    for claim_index, hidden in enumerate(gold["claims"], start=1):
        statement = str(hidden.get("claim") or hidden.get("statement") or "")
        if claim_override and hidden["semantic_key"] == claim_override[0]:
            statement = claim_override[1]
        if reverse_all:
            statement = f"The following statement is false: {statement}"
        evidence = []
        for ref_index, raw_ref in enumerate(hidden["evidence_refs"], start=1):
            normalized = _normalized_evidence_ref(raw_ref)
            assert normalized is not None
            evidence_id = f"EV-{claim_index:03d}-{ref_index:02d}"
            path, start, end = normalized
            evidence.append(
                {
                    "evidence_id": evidence_id,
                    "path": path,
                    "start_line": start,
                    "end_line": end,
                }
            )
            cards.append(
                {
                    "evidence_id": evidence_id,
                    "path": path,
                    "start_line": start,
                    "end_line": end,
                }
            )
        claims.append(
            {
                "claim_id": f"public-claim-{claim_index}",
                "claim": statement,
                "semantic_key": f"public.semantic.{claim_index}",
                "critical": bool(hidden.get("critical")),
                "l1_status": "verified",
                "l2_status": "not_checked",
                "verification_status": "verified",
                "evidence_refs": evidence,
            }
        )
    (output / "claim_ledger.json").write_text(
        json.dumps(
            {
                "kind": "claim_evidence_ledger",
                "schema_version": "claim-evidence-ledger-v3",
                "claims": claims,
            }
        )
    )
    (output / "evidence_cards.json").write_text(json.dumps(cards))
    (output / "quality_accuracy_policy.json").write_text(
        json.dumps(
            {
                "l3_validation": {
                    "status": "pass",
                    "numerator": 1,
                    "denominator": 1,
                    "critical_miss_ids": [],
                    "evidence_refs": ["oracle://accuracy"],
                    "limitations": [],
                }
            }
        )
    )

    breadth_candidates = []
    breadth_scenarios = []
    for index, item in enumerate(universe["items"], start=1):
        statement = str(
            item.get("statement") or item.get("description") or ""
        ).strip() or (
            f"This observation covers the {item['dimension']} obligation "
            f"{str(item['item_id']).rsplit(':', 1)[-1]}."
        )
        if reverse_all:
            statement = f"The following statement is false: {statement}"
        refs = [_public_ref(raw_ref) for raw_ref in item["evidence_refs"]]
        breadth_candidates.append(
            {
                "candidate_id": f"public-candidate-{index}",
                "narrative": statement,
                "evidence_refs": refs,
            }
        )
        breadth_scenarios.append(
            {
                "scenario_id": f"public-scenario-{index}",
                "candidate_ids": [f"public-candidate-{index}"],
                "status": "READY",
                "narrative": statement,
                "evidence_refs": refs,
            }
        )
    (output / "quality_breadth.json").write_text(
        json.dumps(
            {
                "scenario_candidates": {
                    "kind": "scenario_candidates",
                    "items": breadth_candidates,
                },
                "scenarios": {"kind": "test_scenarios", "items": breadth_scenarios},
                "dispositions": [],
            }
        )
    )

    grouped_bindings = {}
    for binding in catalog.bindings:
        category = binding.category.value
        if category not in {"node", "edge", "check"}:
            continue
        grouped_bindings.setdefault(
            (binding.chain_id, category, binding.obligation_id), []
        ).append(binding.evidence_ref)
    depth_chains = []
    for hidden_chain in truth["chains"]:
        public_chain = {
            "chain_id": f"public-{hidden_chain['chain_id']}",
            "narrative": "Public source-backed causal chain.",
            "nodes": [],
            "edges": [],
            "disconfirming_checks": [],
        }
        for category, field, id_field in (
            ("node", "nodes", "node_id"),
            ("edge", "edges", "edge_id"),
            ("check", "disconfirming_checks", "check_id"),
        ):
            for index, obligation in enumerate(hidden_chain[field], start=1):
                obligation_id = str(obligation[id_field])
                statement = str(obligation["statement"])
                if reverse_all:
                    statement = f"The following statement is false: {statement}"
                public_chain[field].append(
                    {
                        id_field: f"public-{category}-{index}",
                        "status": "pass" if category == "check" else "closed",
                        "narrative": statement,
                        "evidence_refs": [
                            _public_ref(raw_ref)
                            for raw_ref in grouped_bindings[
                                (hidden_chain["chain_id"], category, obligation_id)
                            ]
                        ],
                    }
                )
        depth_chains.append(public_chain)
    (output / "quality_depth_candidate.json").write_text(
        json.dumps({"chains": depth_chains})
    )


def _dynamic_snapshot(
    tmp_path: Path,
    project_id: str,
    *,
    reverse_all: bool = False,
    claim_override: tuple[str, str] | None = None,
):
    registry = load_quality_registry(REGISTRY_PATH)
    case_path = next(PROJECTS_ROOT.glob(f"{project_id}/*/case.json"))
    artifacts = tmp_path / f"{project_id}-artifacts"
    _write_dynamic_candidate(
        case_path=case_path,
        output=artifacts,
        reverse_all=reverse_all,
        claim_override=claim_override,
    )
    judge = _SelectiveBatchSemanticJudge()
    audits = []
    snapshot = evaluate_artifact_snapshot(
        case_path=case_path,
        registry=registry,
        artifacts_dir=artifacts,
        source_dir=Path("/Volumes/Media/codetalk-quality-corpus/sources") / project_id,
        generator_model="gpt-5.6-sol",
        judge_model="gpt-5.5",
        mode="rapid",
        deadline_monotonic=time.monotonic() + 30,
        semantic_judge=judge,
        semantic_audit_sink=audits,
        snapshot_label="dynamic",
    )
    assert len(judge.calls) == 1
    assert {item.axis for item in judge.calls[0]["judgments"]} == {
        "accuracy",
        "breadth",
        "depth",
    }
    assert audits[0]["status"] == "completed"
    return snapshot


@pytest.mark.parametrize("project_id", ["spdk", "lmcache", "mooncake", "phosphor-nvme"])
def test_real_corpus_positive_candidate_passes_default_batch_semantic_path(
    tmp_path, project_id
) -> None:
    snapshot = _dynamic_snapshot(tmp_path, project_id)

    assert snapshot.accuracy.status.value == "pass"
    assert snapshot.breadth.status.value == "pass"
    assert snapshot.depth.status.value == "pass"


@pytest.mark.parametrize("project_id", ["spdk", "lmcache", "mooncake", "phosphor-nvme"])
def test_real_corpus_all_reversed_candidate_fails_closed_on_every_axis(
    tmp_path, project_id
) -> None:
    snapshot = _dynamic_snapshot(tmp_path, project_id, reverse_all=True)

    assert snapshot.accuracy.status.value == "fail"
    assert snapshot.breadth.status.value == "fail"
    assert snapshot.depth.status.value == "fail"


@pytest.mark.parametrize(
    ("project_id", "semantic_key", "paraphrase"),
    [
        (
            "spdk",
            "spdk.bdev_nvme.concurrent_reset.pending_queue",
            "When the controller is busy resetting, the app thread queues the concurrent "
            "reset I/O in pending_resets and returns from the handler.",
        ),
        (
            "spdk",
            "spdk.bdev_nvme.concurrent_reset.pending_completion",
            "Once controller reset finishes, each pending reset I/O is dequeued and resumed "
            "according to that controller-reset result.",
        ),
        (
            "lmcache",
            "lmcache.local_cpu.get.same_object_refcount_increment",
            "For an existing cache key, get_blocking hands the caller the same MemoryObj "
            "after taking one additional reference.",
        ),
        (
            "mooncake",
            "mooncake.store.put_end.complete_replica_readiness",
            "The original PutStart client can finish PutEnd, transition its replica to "
            "COMPLETE, and then observe that complete replica through GetReplicaList.",
        ),
        (
            "phosphor-nvme",
            "nvme.inventory.present.write.is.serial_change_gated",
            "setNvmeInventoryProperties updates Present and asset data and refreshes the "
            "cached serial only when the sampled serial has changed.",
        ),
    ],
)
def test_five_manual_paraphrases_pass_the_default_batch_semantic_path(
    tmp_path, project_id, semantic_key, paraphrase
) -> None:
    snapshot = _dynamic_snapshot(
        tmp_path,
        project_id,
        claim_override=(semantic_key, paraphrase),
    )

    assert snapshot.accuracy.status.value == "pass"


def test_spdk_reversed_breadth_narratives_fail_the_real_hidden_universe() -> None:
    registry = load_quality_registry(REGISTRY_PATH)
    case_path = next(PROJECTS_ROOT.glob("spdk/*/case.json"))
    case = load_quality_case(case_path, registry=registry)
    universe = json.loads(
        (case_path.parent / case.truth_package.coverage_universe.path).read_text()
    )
    candidates = []
    scenarios = []
    for index, item in enumerate(universe["items"], start=1):
        refs = [_public_ref(ref) for ref in item["evidence_refs"]]
        candidates.append({
            "candidate_id": f"public-candidate-{index}",
            "narrative": "The cited behavior is absent and the opposite path always runs.",
            "status": "covered",
            "evidence_refs": refs,
        })
        scenarios.append({
            "scenario_id": f"public-scenario-{index}",
            "candidate_ids": [f"public-candidate-{index}"],
            "narrative": "This scenario proves the cited behavior can never occur.",
            "status": "ready",
            "evidence_refs": refs,
        })

    aligned_candidates, aligned_scenarios = _align_breadth_evidence_refs(
        universe,
        candidates,
        scenarios,
    )
    result = evaluate_breadth(
        universe,
        scenario_candidates=aligned_candidates,
        scenarios=aligned_scenarios,
        dispositions=[],
    )

    assert result.status.value == "fail"
    assert result.critical_misses


def test_spdk_reversed_depth_narratives_fail_the_real_hidden_chains() -> None:
    registry = load_quality_registry(REGISTRY_PATH)
    case_path = next(PROJECTS_ROOT.glob("spdk/*/case.json"))
    case = load_quality_case(case_path, registry=registry)
    truth = json.loads(
        (case_path.parent / case.truth_package.critical_chains.path).read_text()
    )
    execution = json.loads(
        (case_path.parent / case.truth_package.execution_oracles.path).read_text()
    )
    catalog = DepthEvidenceCatalog.model_validate(_depth_catalog_payload(execution))
    public_chain = {
        "chain_id": "public-reversed-chain",
        "narrative": "Every transition runs in reverse and no recovery can complete.",
        "nodes": [],
        "edges": [],
        "disconfirming_checks": [],
    }
    fields = {"node": "nodes", "edge": "edges", "check": "disconfirming_checks"}
    ids = {"node": "node_id", "edge": "edge_id", "check": "check_id"}
    for index, binding in enumerate(catalog.bindings, start=1):
        if binding.category.value == "l3":
            continue
        category = binding.category.value
        public_chain[fields[category]].append({
            ids[category]: f"public-{category}-{index}",
            "status": "closed",
            "narrative": "The obligation is false and its opposite is guaranteed.",
            "evidence_refs": [_public_ref(binding.evidence_ref)],
        })
    candidate = {"chains": [public_chain]}

    aligned = _align_depth_candidate_from_evidence(truth, candidate, catalog)
    result = evaluate_depth(truth, aligned, catalog)

    assert result.status.value == "fail"
    assert result.critical_misses


@pytest.mark.parametrize("project_id", ["lmcache", "mooncake"])
def test_real_kv_statements_align_by_unique_evidence_without_generator_status(
    project_id,
) -> None:
    from app.services.quality_benchmark_runner import (
        _align_claim_semantics_from_evidence,
    )

    registry = load_quality_registry(REGISTRY_PATH)
    case_path = next(PROJECTS_ROOT.glob(f"{project_id}/*/case.json"))
    case = load_quality_case(case_path, registry=registry)
    gold_payload = json.loads(
        (case_path.parent / case.truth_package.gold_claims.path).read_text()
    )
    public_claims = []
    for index, gold in enumerate(gold_payload["claims"], start=1):
        public_claims.append({
            "claim_id": f"public-{project_id}-{index}",
            "claim": gold["statement"],
            "semantic_key": f"generator-{index}",
            "l2_status": "not_checked",
            "evidence_refs": [
                {
                    "path": normalized[0],
                    "start_line": normalized[1],
                    "end_line": normalized[2],
                }
                for ref in gold["evidence_refs"]
                for normalized in [_normalized_evidence_ref(ref)]
                if normalized is not None
            ],
        })

    aligned = _align_claim_semantics_from_evidence(
        {"claims": public_claims},
        gold_payload["claims"],
    )

    assert [claim["semantic_key"] for claim in aligned["claims"]] == [
        gold["semantic_key"] for gold in gold_payload["claims"]
    ]
    assert {claim["l2_status"] for claim in aligned["claims"]} == {"supports"}
