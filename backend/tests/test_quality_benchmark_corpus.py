from __future__ import annotations

import copy
import hashlib
import importlib
import json
import subprocess
from itertools import pairwise
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator


REPO_ROOT = Path(__file__).resolve().parents[2]
REGISTRY_PATH = REPO_ROOT / "benchmarks/quality/registry.json"
REGISTRY_SCHEMA_PATH = REPO_ROOT / "benchmarks/quality/schemas/registry.schema.json"
CASE_SCHEMA_PATH = REPO_ROOT / "benchmarks/quality/schemas/case.schema.json"

EXPECTED_PROJECTS = {
    "spdk": {
        "commit": "d64c4fa89233397460e2e4ff55a1c69b8e498598",
        "expected_tree": "b8c41cac12ca9c9cb34a8da6e028d35f826f581b",
        "origin": "https://github.com/spdk/spdk.git",
        "license": "BSD-3-Clause",
        "tiers": ["S", "E"],
    },
    "femu": {
        "commit": "b130c614afbc6e77f88e272533e9d71f8509e234",
        "expected_tree": "012c1d0277ac77a77f90bcb480bb99538382f768",
        "origin": "https://github.com/MoatLab/FEMU.git",
        "license": "GPL-2.0 with mixed file licenses",
        "tiers": ["S", "E"],
    },
    "nvme-csd": {
        "commit": "d906b6a29e559a3d613a1eccf3712611587311ba",
        "expected_tree": "c90d9ef4c47b2c1c7551ef481d0a8978e3503940",
        "origin": "https://github.com/rick-heig/nvme_csd.git",
        "license": "NOASSERTION",
        "tiers": ["S", "E"],
    },
    "open-cas-linux": {
        "commit": "f1befa8dddf810733e720dec07c71de892951e39",
        "expected_tree": "509e5c7987cd3c83aa213ac23b715a9180d65a6b",
        "origin": "https://github.com/Open-CAS/open-cas-linux.git",
        "license": "BSD-3-Clause",
        "tiers": ["S", "E"],
    },
    "phosphor-nvme": {
        "commit": "5ef51383d77fc32f5d5d314e70f860126de623e7",
        "expected_tree": "18fbb0cd8c9a0dd93bbeb54b8f19474a53b7c354",
        "origin": "https://github.com/openbmc/phosphor-nvme.git",
        "license": "Apache-2.0",
        "tiers": ["S"],
    },
    "phosphor-state-manager": {
        "commit": "3f6517cbce44f84f9cea95f3f72b4f6401a52d49",
        "expected_tree": "4fbec101d4be874fed853e0f344d174f977450a0",
        "origin": "https://github.com/openbmc/phosphor-state-manager.git",
        "license": "Apache-2.0",
        "tiers": ["S", "E"],
    },
    "bmcweb": {
        "commit": "9e59f0a176aac9dfa7f029370ea03c25a088d9a2",
        "expected_tree": "4e4e40cb4850fb110375ead0c1cd6d5b05425e05",
        "origin": "https://github.com/openbmc/bmcweb.git",
        "license": "Apache-2.0",
        "tiers": ["S", "E"],
    },
    "lmcache": {
        "commit": "f625b9733ad38c6b1bb3ba3d5083998ab5307ffb",
        "expected_tree": "c970140ccbe796aec9a25a915ea62390223100ec",
        "origin": "https://github.com/LMCache/LMCache.git",
        "license": "Apache-2.0",
        "tiers": ["S", "E"],
    },
    "mooncake": {
        "commit": "131d6addae64c31b340f1909350049eb41fcb790",
        "expected_tree": "0b1a5c2baf5b98ebe1d44f09dd03a256aaa31290",
        "origin": "https://github.com/kvcache-ai/Mooncake.git",
        "license": "Apache-2.0",
        "tiers": ["S", "E", "H"],
    },
    "rdma-core": {
        "commit": "d45834e0fe3ff1248e40d995f2f51c51739e6f1c",
        "expected_tree": "e50a1a4f7eeb90608bbd7dc8042bd5b53dce0eff",
        "origin": "https://github.com/linux-rdma/rdma-core.git",
        "license": "GPL-2.0-or-later OR LGPL-2.1-or-later",
        "tiers": ["S", "E", "H"],
    },
    "ucx": {
        "commit": "1ce08f6ed89caa0bc2dcef5c2e9ad837455da168",
        "expected_tree": "62435473638ce3f1f392d7e4fa43333f759e7f4b",
        "origin": "https://github.com/openucx/ucx.git",
        "license": "BSD-3-Clause",
        "tiers": ["S", "E", "H"],
    },
    "perftest": {
        "commit": "00b55b6660d0170dabe2c1b49193e8fbe265086e",
        "expected_tree": "0fa1b3385f5193d09915c247d1bb7266efa5c7aa",
        "origin": "https://github.com/linux-rdma/perftest.git",
        "license": "GPL-2.0",
        "tiers": ["S", "E", "H"],
    },
}


def _corpus():
    try:
        return importlib.import_module("app.services.quality_benchmark_corpus")
    except ModuleNotFoundError as exc:
        pytest.fail(f"quality benchmark corpus contract is missing: {exc}")


def _git(*args: str, cwd: Path | None = None) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _synthetic_repo(root: Path) -> tuple[Path, str, str, str]:
    project = root / "synthetic"
    project.mkdir(parents=True)
    _git("init", "-q", cwd=project)
    _git("config", "user.email", "quality@example.invalid", cwd=project)
    _git("config", "user.name", "Quality Fixture", cwd=project)
    (project / "README.md").write_text("synthetic corpus\n", encoding="utf-8")
    _git("add", "README.md", cwd=project)
    _git("commit", "-qm", "fixture", cwd=project)
    origin = "https://github.com/example/synthetic.git"
    _git("remote", "add", "origin", origin, cwd=project)
    return project, _git("rev-parse", "HEAD", cwd=project), _git(
        "rev-parse", "HEAD^{tree}", cwd=project
    ), origin


def _project_payload(commit: str, tree: str, origin: str) -> dict[str, Any]:
    return {
        "id": "synthetic",
        "source_dir": "synthetic",
        "origin": origin,
        "commit": commit,
        "expected_tree": tree,
        "license": "Apache-2.0",
        "tiers": ["S"],
        "test_execution": {
            "policy": "case_allowlist_only",
            "loader_execution": "forbidden",
            "network": "disabled",
        },
    }


def _registry_payload(project: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "quality-benchmark-registry-v1",
        "truth_package_version": "1",
        "projects": [project],
    }


def _write_json(path: Path, payload: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _valid_fixture(tmp_path: Path) -> tuple[Path, Path, dict[str, Any]]:
    source_root = tmp_path / "sources"
    project_path, commit, tree, origin = _synthetic_repo(source_root)
    payload = _registry_payload(_project_payload(commit, tree, origin))
    registry_path = _write_json(tmp_path / "registry.json", payload)
    return source_root, registry_path, payload


DEPTH_NODE_KINDS = (
    ("trigger", "trigger"),
    ("precondition", "precondition"),
    ("entry", "entry"),
    ("call", "call"),
    ("resource-acquire", "resource_acquisition"),
    ("resource-owner", "resource_ownership"),
    ("state-mutation", "state_mutation"),
    ("downstream-effect", "downstream_effect"),
    ("error-propagation", "error_propagation"),
    ("cleanup", "cleanup"),
    ("resource-release", "resource_release"),
    ("recovery", "recovery"),
    ("external-observation", "external_observation"),
    ("executable-oracle", "executable_oracle"),
)


def _depth_truth_contents() -> dict[str, Any]:
    case_id = "synthetic-static-001"
    chain_id = "chain-1"
    nodes = [
        {
            "node_id": node_id,
            "kind": kind,
            "statement": f"README establishes the {node_id} causal stage.",
            "critical": True,
        }
        for node_id, kind in DEPTH_NODE_KINDS
    ]
    edges = [
        {
            "edge_id": f"edge-{source}-{target}",
            "source_node_id": source,
            "target_node_id": target,
            "statement": f"The {source} stage leads to the {target} stage.",
            "critical": True,
        }
        for (source, _), (target, _) in pairwise(DEPTH_NODE_KINDS)
    ]
    checks = [
        {
            "check_id": "reject-alternative-cause",
            "statement": "README excludes the alternative causal explanation.",
            "critical": True,
        }
    ]
    bindings = [
        {
            "evidence_ref": f"source://README.md#L1-L1:node-{node['node_id']}",
            "chain_id": chain_id,
            "category": "node",
            "obligation_id": node["node_id"],
        }
        for node in nodes
    ]
    bindings.extend(
        {
            "evidence_ref": f"source://README.md#L1-L1:edge-{edge['edge_id']}",
            "chain_id": chain_id,
            "category": "edge",
            "obligation_id": edge["edge_id"],
        }
        for edge in edges
    )
    bindings.extend(
        {
            "evidence_ref": f"test://README.md#L1-L1:check-{check['check_id']}",
            "chain_id": chain_id,
            "category": "check",
            "obligation_id": check["check_id"],
        }
        for check in checks
    )
    bindings.append(
        {
            "evidence_ref": "oracle://synthetic-static#chain-1",
            "chain_id": chain_id,
            "category": "l3",
            "obligation_id": "execution",
        }
    )
    canonical_catalog = {
        "case_id": case_id,
        "bindings": sorted(
            bindings,
            key=lambda binding: (
                binding["chain_id"],
                binding["category"],
                binding["obligation_id"],
                binding["evidence_ref"],
            ),
        ),
    }
    catalog_digest = hashlib.sha256(
        json.dumps(
            canonical_catalog,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return {
        "gold_claims.json": [{"claim_id": "claim-1"}],
        "coverage_universe.json": [{"item_id": "coverage-1"}],
        "critical_chains.json": {
            "case_id": case_id,
            "evidence_catalog_sha256": catalog_digest,
            "execution_tier": "S",
            "chains": [
                {
                    "chain_id": chain_id,
                    "nodes": nodes,
                    "edges": edges,
                    "disconfirming_checks": checks,
                }
            ],
        },
        "execution_oracles.json": {
            "case_id": case_id,
            "bindings": bindings,
            "execution_plan": {
                "schema_version": "quality-depth-execution-v1",
                "case_id": case_id,
                "execution_tier": "S",
                "policy": "disabled",
                "oracles": [],
                "limitations": [],
            },
            "tier_dispositions": [],
        },
    }


TRUTH_CONTENTS = _depth_truth_contents()


def _truth_descriptors(case_dir: Path) -> dict[str, dict[str, str]]:
    descriptors: dict[str, dict[str, str]] = {}
    for filename, payload in TRUTH_CONTENTS.items():
        encoded = json.dumps(payload, sort_keys=True).encode("utf-8")
        (case_dir / filename).write_bytes(encoded)
        descriptors[filename.removesuffix(".json")] = {
            "path": filename,
            "sha256": hashlib.sha256(encoded).hexdigest(),
        }
    return descriptors


def _rewrite_truth(
    case_dir: Path,
    case_payload: dict[str, Any],
    truth_name: str,
    payload: Any,
) -> None:
    descriptor = case_payload["truth_package"][truth_name]
    encoded = json.dumps(payload, sort_keys=True).encode("utf-8")
    (case_dir / descriptor["path"]).write_bytes(encoded)
    descriptor["sha256"] = hashlib.sha256(encoded).hexdigest()


def _case_payload(case_dir: Path | None = None) -> dict[str, Any]:
    descriptors = (
        _truth_descriptors(case_dir)
        if case_dir is not None
        else {
            filename.removesuffix(".json"): {
                "path": filename,
                "sha256": "0" * 64,
            }
            for filename in TRUTH_CONTENTS
        }
    )
    return {
        "schema_version": "quality-benchmark-case-v1",
        "case_id": "synthetic-static-001",
        "project_id": "synthetic",
        "truth_package_version": "1",
        "tier": "S",
        "truth_package": descriptors,
        "test_execution": {"policy": "disabled", "commands": []},
    }


def test_loader_resolves_pinned_synthetic_project_from_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    corpus = _corpus()
    source_root, registry_path, _ = _valid_fixture(tmp_path)
    monkeypatch.setenv("CODETALK_QUALITY_CORPUS_ROOT", str(source_root))

    registry = corpus.load_quality_registry(registry_path)
    resolved = corpus.resolve_quality_project("synthetic", registry=registry)

    assert resolved.path == source_root / "synthetic"
    assert resolved.commit == registry.projects[0].commit
    assert resolved.expected_tree == registry.projects[0].expected_tree


def test_domain_authority_distinguishes_generic_rdma_from_explicit_roce() -> None:
    corpus = _corpus()

    assert corpus.PROJECT_DOMAIN_TAGS["rdma-core"] == frozenset(
        {"rdma", "rdma-roce"}
    )
    assert corpus.PROJECT_DOMAIN_TAGS["ucx"] == frozenset(
        {"rdma", "roce", "rdma-roce"}
    )
    assert corpus.PROJECT_DOMAIN_TAGS["perftest"] == frozenset(
        {"rdma", "roce", "rdma-roce"}
    )
    assert corpus.BASELINE_PROJECT_STRATA["rdma-core"] == "rdma-roce"


def test_loader_invokes_only_read_only_git_metadata_commands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    corpus = _corpus()
    source_root, registry_path, _ = _valid_fixture(tmp_path)
    registry = corpus.load_quality_registry(registry_path)
    real_run = corpus.subprocess.run
    calls: list[tuple[list[str], dict[str, Any]]] = []

    def recording_run(args: list[str], **kwargs: Any):
        calls.append((args, kwargs))
        return real_run(args, **kwargs)

    monkeypatch.setattr(corpus.subprocess, "run", recording_run)
    corpus.resolve_quality_project(
        "synthetic", registry=registry, corpus_root=source_root
    )

    assert [call[0][3:] for call in calls] == [
        ["rev-parse", "HEAD"],
        ["rev-parse", "HEAD^{tree}"],
        ["config", "--get", "remote.origin.url"],
    ]
    assert all(call[0][:2] == ["git", "-C"] for call in calls)
    assert all(call[1].get("shell") is not True for call in calls)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("commit", "d64c4fa"),
        ("commit", "main"),
        ("expected_tree", "b8c41cac"),
        ("origin", "git@github.com:example/synthetic.git"),
        ("origin", "https://github.com/example/synthetic"),
        ("source_dir", "../synthetic"),
        ("source_dir", "/tmp/synthetic"),
        ("source_dir", "nested/synthetic"),
        ("source_dir", "nested\\synthetic"),
        ("tiers", ["X"]),
        ("tiers", ["S", "S"]),
    ],
)
def test_registry_rejects_invalid_project_metadata(
    tmp_path: Path, field: str, value: Any
) -> None:
    corpus = _corpus()
    _, _, payload = _valid_fixture(tmp_path)
    payload["projects"][0][field] = value

    with pytest.raises(corpus.QualityCorpusError):
        corpus.load_quality_registry(_write_json(tmp_path / "invalid.json", payload))


@pytest.mark.parametrize(
    "missing_field", ["origin", "commit", "expected_tree", "license", "tiers", "test_execution"]
)
def test_registry_rejects_missing_required_metadata(
    tmp_path: Path, missing_field: str
) -> None:
    corpus = _corpus()
    _, _, payload = _valid_fixture(tmp_path)
    del payload["projects"][0][missing_field]

    with pytest.raises(corpus.QualityCorpusError):
        corpus.load_quality_registry(_write_json(tmp_path / "missing.json", payload))


def test_registry_rejects_duplicate_project_ids(tmp_path: Path) -> None:
    corpus = _corpus()
    _, _, payload = _valid_fixture(tmp_path)
    duplicate = copy.deepcopy(payload["projects"][0])
    duplicate["source_dir"] = "synthetic-copy"
    payload["projects"].append(duplicate)

    with pytest.raises(corpus.QualityCorpusError, match="duplicate"):
        corpus.load_quality_registry(_write_json(tmp_path / "duplicate.json", payload))


def test_registry_rejects_unknown_fields(tmp_path: Path) -> None:
    corpus = _corpus()
    _, _, payload = _valid_fixture(tmp_path)
    payload["projects"][0]["branch"] = "main"

    with pytest.raises(corpus.QualityCorpusError):
        corpus.load_quality_registry(_write_json(tmp_path / "unknown.json", payload))


@pytest.mark.parametrize("mismatch", ["origin", "commit", "expected_tree"])
def test_resolver_fails_closed_on_repository_identity_mismatch(
    tmp_path: Path, mismatch: str
) -> None:
    corpus = _corpus()
    source_root, _, payload = _valid_fixture(tmp_path)
    replacements = {
        "origin": "https://github.com/example/not-synthetic.git",
        "commit": "0" * 40,
        "expected_tree": "1" * 40,
    }
    payload["projects"][0][mismatch] = replacements[mismatch]
    registry = corpus.load_quality_registry(
        _write_json(tmp_path / f"{mismatch}.json", payload)
    )

    with pytest.raises(corpus.QualityCorpusError, match=mismatch):
        corpus.resolve_quality_project(
            "synthetic", registry=registry, corpus_root=source_root
        )


def test_resolver_requires_corpus_root_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    corpus = _corpus()
    _, registry_path, _ = _valid_fixture(tmp_path)
    monkeypatch.delenv("CODETALK_QUALITY_CORPUS_ROOT", raising=False)

    with pytest.raises(corpus.QualityCorpusError, match="CODETALK_QUALITY_CORPUS_ROOT"):
        corpus.resolve_quality_project(
            "synthetic", registry=corpus.load_quality_registry(registry_path)
        )


def test_resolver_rejects_symlink_escape(tmp_path: Path) -> None:
    corpus = _corpus()
    outside = tmp_path / "outside"
    _, commit, tree, origin = _synthetic_repo(outside)
    source_root = tmp_path / "sources"
    source_root.mkdir()
    (source_root / "synthetic").symlink_to(outside / "synthetic", target_is_directory=True)
    registry = corpus.load_quality_registry(
        _write_json(
            tmp_path / "registry.json",
            _registry_payload(_project_payload(commit, tree, origin)),
        )
    )

    with pytest.raises(corpus.QualityCorpusError, match="escape"):
        corpus.resolve_quality_project(
            "synthetic", registry=registry, corpus_root=source_root
        )


def test_resolver_rejects_external_gitdir_file(tmp_path: Path) -> None:
    corpus = _corpus()
    source_root = tmp_path / "sources"
    project = source_root / "synthetic"
    external_gitdir = tmp_path / "external.git"
    project.mkdir(parents=True)
    _git("init", "-q", f"--separate-git-dir={external_gitdir}", cwd=project)
    _git("config", "user.email", "quality@example.invalid", cwd=project)
    _git("config", "user.name", "Quality Fixture", cwd=project)
    (project / "README.md").write_text("external gitdir\n", encoding="utf-8")
    _git("add", "README.md", cwd=project)
    _git("commit", "-qm", "fixture", cwd=project)
    origin = "https://github.com/example/synthetic.git"
    _git("remote", "add", "origin", origin, cwd=project)
    registry = corpus.load_quality_registry(
        _write_json(
            tmp_path / "registry.json",
            _registry_payload(
                _project_payload(
                    _git("rev-parse", "HEAD", cwd=project),
                    _git("rev-parse", "HEAD^{tree}", cwd=project),
                    origin,
                )
            ),
        )
    )

    assert (project / ".git").is_file()
    with pytest.raises(corpus.QualityCorpusError, match="git directory"):
        corpus.resolve_quality_project(
            "synthetic", registry=registry, corpus_root=source_root
        )


def test_case_loads_when_project_tier_and_truth_version_match(tmp_path: Path) -> None:
    corpus = _corpus()
    source_root, registry_path, _ = _valid_fixture(tmp_path)
    case_path = _write_json(tmp_path / "case.json", _case_payload(tmp_path))

    case = corpus.load_quality_case(
        case_path,
        registry=corpus.load_quality_registry(registry_path),
        source_dir=source_root / "synthetic",
    )

    assert case.project_id == "synthetic"
    assert case.truth_package.gold_claims.path == "gold_claims.json"
    assert len(case.truth_package.gold_claims.sha256) == 64


@pytest.mark.parametrize(
    ("category", "field"),
    [
        ("nodes", "statement"),
        ("edges", "statement"),
        ("disconfirming_checks", "statement"),
        ("edges", "source_node_id"),
        ("edges", "target_node_id"),
    ],
)
def test_case_rejects_unjudgeable_depth_obligation_fields(
    tmp_path: Path,
    category: str,
    field: str,
) -> None:
    corpus = _corpus()
    _, registry_path, _ = _valid_fixture(tmp_path)
    payload = _case_payload(tmp_path)
    critical = copy.deepcopy(TRUTH_CONTENTS["critical_chains.json"])
    del critical["chains"][0][category][0][field]
    _rewrite_truth(tmp_path, payload, "critical_chains", critical)

    with pytest.raises(corpus.QualityCorpusError, match=field):
        corpus.load_quality_case(
            _write_json(tmp_path / "case.json", payload),
            registry=corpus.load_quality_registry(registry_path),
        )


def test_case_rejects_depth_package_without_execution_plan(tmp_path: Path) -> None:
    corpus = _corpus()
    _, registry_path, _ = _valid_fixture(tmp_path)
    payload = _case_payload(tmp_path)
    execution = copy.deepcopy(TRUTH_CONTENTS["execution_oracles.json"])
    del execution["execution_plan"]
    _rewrite_truth(tmp_path, payload, "execution_oracles", execution)

    with pytest.raises(corpus.QualityCorpusError, match="execution_plan"):
        corpus.load_quality_case(
            _write_json(tmp_path / "case.json", payload),
            registry=corpus.load_quality_registry(registry_path),
        )


@pytest.mark.parametrize(
    ("field", "value", "match"),
    [
        ("truth_package_version", "2", "truth_package_version"),
        ("project_id", "missing", "project_id"),
        ("tier", "H", "tier"),
    ],
)
def test_case_rejects_registry_mismatch(
    tmp_path: Path, field: str, value: str, match: str
) -> None:
    corpus = _corpus()
    _, registry_path, _ = _valid_fixture(tmp_path)
    payload = _case_payload(tmp_path)
    payload[field] = value

    with pytest.raises(corpus.QualityCorpusError, match=match):
        corpus.load_quality_case(
            _write_json(tmp_path / "case.json", payload),
            registry=corpus.load_quality_registry(registry_path),
        )


def test_case_rejects_truth_path_traversal(tmp_path: Path) -> None:
    corpus = _corpus()
    _, registry_path, _ = _valid_fixture(tmp_path)
    payload = _case_payload(tmp_path)
    payload["truth_package"]["gold_claims"]["path"] = "../gold_claims.json"

    with pytest.raises(corpus.QualityCorpusError, match="gold_claims"):
        corpus.load_quality_case(
            _write_json(tmp_path / "case.json", payload),
            registry=corpus.load_quality_registry(registry_path),
        )


def test_case_rejects_missing_truth_file(tmp_path: Path) -> None:
    corpus = _corpus()
    _, registry_path, _ = _valid_fixture(tmp_path)
    payload = _case_payload(tmp_path)
    (tmp_path / "gold_claims.json").unlink()

    with pytest.raises(corpus.QualityCorpusError, match="missing.*gold_claims"):
        corpus.load_quality_case(
            _write_json(tmp_path / "case.json", payload),
            registry=corpus.load_quality_registry(registry_path),
        )


def test_case_rejects_tampered_truth_file(tmp_path: Path) -> None:
    corpus = _corpus()
    _, registry_path, _ = _valid_fixture(tmp_path)
    payload = _case_payload(tmp_path)
    (tmp_path / "gold_claims.json").write_text(
        '[{"claim_id":"tampered"}]', encoding="utf-8"
    )

    with pytest.raises(corpus.QualityCorpusError, match="sha256 mismatch.*gold_claims"):
        corpus.load_quality_case(
            _write_json(tmp_path / "case.json", payload),
            registry=corpus.load_quality_registry(registry_path),
        )


def test_case_rejects_wrong_declared_truth_hash(tmp_path: Path) -> None:
    corpus = _corpus()
    _, registry_path, _ = _valid_fixture(tmp_path)
    payload = _case_payload(tmp_path)
    payload["truth_package"]["coverage_universe"]["sha256"] = "f" * 64

    with pytest.raises(
        corpus.QualityCorpusError, match="sha256 mismatch.*coverage_universe"
    ):
        corpus.load_quality_case(
            _write_json(tmp_path / "case.json", payload),
            registry=corpus.load_quality_registry(registry_path),
        )


def test_case_rejects_truth_file_symlink_escape(tmp_path: Path) -> None:
    corpus = _corpus()
    _, registry_path, _ = _valid_fixture(tmp_path)
    case_dir = tmp_path / "case"
    case_dir.mkdir()
    payload = _case_payload(case_dir)
    outside = tmp_path / "outside.json"
    outside.write_text("[]", encoding="utf-8")
    descriptor = payload["truth_package"]["execution_oracles"]
    descriptor["sha256"] = hashlib.sha256(outside.read_bytes()).hexdigest()
    (case_dir / "execution_oracles.json").unlink()
    (case_dir / "execution_oracles.json").symlink_to(outside)

    with pytest.raises(corpus.QualityCorpusError, match="escape.*execution_oracles"):
        corpus.load_quality_case(
            _write_json(case_dir / "case.json", payload),
            registry=corpus.load_quality_registry(registry_path),
        )


def test_case_rejects_non_regular_truth_file(tmp_path: Path) -> None:
    corpus = _corpus()
    _, registry_path, _ = _valid_fixture(tmp_path)
    payload = _case_payload(tmp_path)
    target = tmp_path / "critical_chains.json"
    target.unlink()
    target.mkdir()

    with pytest.raises(corpus.QualityCorpusError, match="regular.*critical_chains"):
        corpus.load_quality_case(
            _write_json(tmp_path / "case.json", payload),
            registry=corpus.load_quality_registry(registry_path),
        )


@pytest.mark.parametrize("content", [b"not-json", b'"scalar"', b"42"])
def test_case_rejects_truth_file_without_json_object_or_list(
    tmp_path: Path, content: bytes
) -> None:
    corpus = _corpus()
    _, registry_path, _ = _valid_fixture(tmp_path)
    payload = _case_payload(tmp_path)
    target = tmp_path / "critical_chains.json"
    target.write_bytes(content)
    payload["truth_package"]["critical_chains"]["sha256"] = hashlib.sha256(
        content
    ).hexdigest()

    with pytest.raises(corpus.QualityCorpusError, match="JSON object or list"):
        corpus.load_quality_case(
            _write_json(tmp_path / "case.json", payload),
            registry=corpus.load_quality_registry(registry_path),
        )


def test_case_rejects_duplicate_truth_descriptors(tmp_path: Path) -> None:
    corpus = _corpus()
    _, registry_path, _ = _valid_fixture(tmp_path)
    payload = _case_payload(tmp_path)
    payload["truth_package"]["coverage_universe"] = copy.deepcopy(
        payload["truth_package"]["gold_claims"]
    )

    with pytest.raises(corpus.QualityCorpusError, match="duplicate truth descriptor"):
        corpus.load_quality_case(
            _write_json(tmp_path / "case.json", payload),
            registry=corpus.load_quality_registry(registry_path),
        )


def test_committed_registry_contains_exact_pinned_project_metadata() -> None:
    corpus = _corpus()
    registry = corpus.load_quality_registry(REGISTRY_PATH)
    actual = {
        project.id: {
            "commit": project.commit,
            "expected_tree": project.expected_tree,
            "origin": project.origin,
            "license": project.license,
            "tiers": project.tiers,
        }
        for project in registry.projects
    }

    assert actual == EXPECTED_PROJECTS
    assert all(
        project.test_execution.policy == "case_allowlist_only"
        and project.test_execution.loader_execution == "forbidden"
        and project.test_execution.network == "disabled"
        for project in registry.projects
    )


def test_committed_schemas_are_draft_2020_12_and_generated_in_sync() -> None:
    corpus = _corpus()
    registry_schema = json.loads(REGISTRY_SCHEMA_PATH.read_text(encoding="utf-8"))
    case_schema = json.loads(CASE_SCHEMA_PATH.read_text(encoding="utf-8"))

    Draft202012Validator.check_schema(registry_schema)
    Draft202012Validator.check_schema(case_schema)
    assert registry_schema == corpus.quality_registry_json_schema()
    assert case_schema == corpus.quality_case_json_schema()


def test_json_schemas_reject_short_hash_unknown_tier_and_truth_traversal(
    tmp_path: Path,
) -> None:
    _, _, registry_payload = _valid_fixture(tmp_path)
    registry_schema = json.loads(REGISTRY_SCHEMA_PATH.read_text(encoding="utf-8"))
    case_schema = json.loads(CASE_SCHEMA_PATH.read_text(encoding="utf-8"))
    registry_payload["projects"][0]["commit"] = "deadbeef"
    registry_payload["projects"][0]["tiers"] = ["X"]
    case_payload = _case_payload()
    case_payload["truth_package"]["gold_claims"]["path"] = "../gold_claims.json"

    assert list(Draft202012Validator(registry_schema).iter_errors(registry_payload))
    assert list(Draft202012Validator(case_schema).iter_errors(case_payload))


def test_case_json_schema_accepts_strict_truth_file_descriptors() -> None:
    case_schema = json.loads(CASE_SCHEMA_PATH.read_text(encoding="utf-8"))

    assert not list(Draft202012Validator(case_schema).iter_errors(_case_payload()))
