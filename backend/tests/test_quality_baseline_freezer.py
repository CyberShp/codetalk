from __future__ import annotations

import hashlib
import inspect
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from app.services.quality_baseline import (
    EVALUATOR_SOURCE_PATHS,
    BaselineError,
    compare_historical_replay,
    load_clean_evaluation_identity,
)
from app.services.quality_baseline_freezer import freeze_baseline_output, main
from app.services.quality_benchmark_corpus import QualityCorpusError
from tests.test_quality_baseline_policy import (
    CORPUS,
    REGISTRY_PATH,
    _audit,
    _thresholds,
    _twelve_runs,
    _write_run,
)


def _run_git(root: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _clean_repository(root: Path) -> tuple[Path, dict[str, str]]:
    root.mkdir(parents=True)
    for index, relative in enumerate(EVALUATOR_SOURCE_PATHS):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"# test evaluator source {index}\n", encoding="utf-8")
    shutil.copytree(
        REGISTRY_PATH.parent,
        root / "benchmarks" / "quality",
    )
    _run_git(root, "init", "-q")
    _run_git(root, "config", "user.name", "Quality Test")
    _run_git(root, "config", "user.email", "quality-test@example.invalid")
    _run_git(root, "add", ".")
    _run_git(root, "-c", "commit.gpgsign=false", "commit", "-qm", "test identity")
    identity = load_clean_evaluation_identity(root)
    return root, {
        "model": "model-1",
        "codetalk": identity.codetalk_revision,
        "evaluator": identity.evaluator_version,
    }


def _write_generator(root: Path, run: Path) -> Path:
    manifest = json.loads((run / "quality_evaluation_manifest.json").read_text())
    report = json.loads((run / "quality_evaluation_report.json").read_text())
    generator = root / str(manifest["case_id"])
    first = generator / "first_pass"
    final = generator / "final_after_auto_repair"
    first.mkdir(parents=True)
    final.mkdir()
    (first / "candidate.json").write_text(
        json.dumps({"phase": "first", "case_id": manifest["case_id"]}) + "\n"
    )
    (final / "candidate.json").write_text(
        json.dumps({"phase": "final", "case_id": manifest["case_id"]}) + "\n"
    )
    (generator / "repair_summary.json").write_text(
        json.dumps(report["repair_summary"], sort_keys=True, separators=(",", ":"))
        + "\n"
    )
    (generator / "versions.json").write_text(
        json.dumps(manifest["versions"], sort_keys=True, separators=(",", ":"))
        + "\n"
    )
    execution = manifest["execution"]
    (generator / "generation_manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "quality-benchmark-generation-v1",
                "case_id": manifest["case_id"],
                "mode": execution["profile"],
                "model": manifest["versions"]["model"],
                "codetalk_revision": manifest["versions"]["codetalk"],
                "elapsed_seconds": execution["generation_wall_clock_seconds"],
                "artifact_hash_manifest": "artifact_hash_manifest.json",
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    )
    (generator / "workbench_audit.json").write_text(
        json.dumps({"schema_version": "quality-benchmark-workbench-audit-v1"})
        + "\n"
    )
    root_sha = _rewrite_generator_hash_manifest(generator)
    manifest["execution"]["generator_artifact_root_sha256"] = root_sha
    (run / "quality_evaluation_manifest.json").write_text(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n"
    )
    return generator


def _rewrite_generator_hash_manifest(
    generator: Path, *, legacy: bool = False
) -> str:
    artifacts: dict[str, dict[str, object]] = {}
    paths = (
        [
            path
            for root_name in ("first_pass", "final_after_auto_repair")
            for path in (generator / root_name).rglob("*")
        ]
        if legacy
        else list(generator.rglob("*"))
    )
    for path in sorted(paths):
        relative = path.relative_to(generator).as_posix()
        if not path.is_file() or relative == "artifact_hash_manifest.json":
            continue
        data = path.read_bytes()
        artifacts[relative] = {
            "sha256": hashlib.sha256(data).hexdigest(),
            "size_bytes": len(data),
        }
    canonical = json.dumps(
        artifacts, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode()
    root_sha = hashlib.sha256(canonical).hexdigest()
    (generator / "artifact_hash_manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "quality-benchmark-artifact-hashes-v1",
                "artifacts": artifacts,
                "root_sha256": root_sha,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    )
    return root_sha


def _generators(root: Path, runs: list[Path]) -> list[Path]:
    return [_write_generator(root, run) for run in runs]


def _paired_evidence(
    root: Path, versions: dict[str, str]
) -> tuple[list[Path], list[Path], list[Path], list[Path]]:
    cases = [
        next(case for case in CORPUS.cases if case.domain == domain)
        for domain in ("storage", "bmc", "kv-cache", "rdma-roce")
    ]
    rapid_runs = [
        _write_run(
            root / "rapid-runs",
            case=case,
            profile="rapid",
            wall_seconds=600.0,
            versions=versions,
            run_ref=f"rapid-{case.case_id}",
        )
        for case in cases
    ]
    deep_runs = [
        _write_run(
            root / "deep-runs",
            case=case,
            profile="deep",
            wall_seconds=1200.0,
            versions=versions,
            run_ref=f"deep-{case.case_id}",
        )
        for case in cases
    ]
    return (
        rapid_runs,
        _generators(root / "rapid-generators", rapid_runs),
        deep_runs,
        _generators(root / "deep-generators", deep_runs),
    )


def _evidence_fixture(
    tmp_path: Path,
    *,
    rapid_limit_override: float | None = None,
) -> dict[str, object]:
    repository, versions = _clean_repository(tmp_path / "repository")
    evidence = tmp_path / "evidence"
    runs, work_audit = _twelve_runs(
        evidence / "core-runs",
        versions=versions,
        rapid_limit_override=rapid_limit_override,
    )
    generators = _generators(evidence / "core-generators", runs)
    rapid_runs, rapid_generators, deep_runs, deep_generators = _paired_evidence(
        evidence, versions
    )
    return {
        "repository": repository,
        "registry": repository / "benchmarks" / "quality" / "registry.json",
        "versions": versions,
        "evidence_root": evidence,
        "runs": runs,
        "generators": generators,
        "rapid_runs": rapid_runs,
        "rapid_generators": rapid_generators,
        "deep_runs": deep_runs,
        "deep_generators": deep_generators,
        "work_audit": work_audit,
    }


def _freeze(
    fixture: dict[str, object],
    output: Path,
    *,
    registry_path: Path | None = None,
) -> Path:
    return freeze_baseline_output(
        run_directories=fixture["runs"],
        generator_directories=fixture["generators"],
        registry_path=registry_path or fixture["registry"],
        repository_root=fixture["repository"],
        thresholds=_thresholds(),
        calibration_audit=_audit(),
        work_sufficiency_audit=fixture["work_audit"],
        rapid_run_directories=fixture["rapid_runs"],
        rapid_generator_directories=fixture["rapid_generators"],
        deep_run_directories=fixture["deep_runs"],
        deep_generator_directories=fixture["deep_generators"],
        output_directory=output,
    )


def test_freezer_publishes_complete_read_only_self_contained_bundle(
    tmp_path: Path,
) -> None:
    fixture = _evidence_fixture(tmp_path)
    output = _freeze(fixture, tmp_path / "baseline")

    assert output == (tmp_path / "baseline").resolve()
    assert len(list((output / "runs").glob("*/evaluation/quality_evaluation_report.json"))) == 12
    assert len(list((output / "runs").glob("*/generator/first_pass/candidate.json"))) == 12
    assert len(list((output / "comparisons" / "rapid-deep").glob("*/rapid/evaluation/quality_evaluation_report.json"))) == 4
    assert len(list((output / "comparisons" / "rapid-deep").glob("*/deep/generator/final_after_auto_repair/candidate.json"))) == 4

    manifest = json.loads((output / "baseline_manifest.json").read_text())
    assert manifest["bundle_status"] == "passed"
    assert manifest["registry_sha256"] == CORPUS.registry_sha256
    assert manifest["corpus_sha256"] == CORPUS.corpus_sha256
    assert set(manifest["case_identities"]) == {case.case_id for case in CORPUS.cases}
    actual_hashes = {
        path.relative_to(output).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(output.rglob("*"))
        if path.is_file() and path.name != "baseline_manifest.json"
    }
    assert manifest["artifact_sha256"] == actual_hashes
    assert set(manifest["source_run_sha256"]) == {case.case_id for case in CORPUS.cases}
    encoded = "\n".join(
        path.read_text()
        for path in output.rglob("*")
        if path.is_file()
    ).lower()
    assert "overall_score" not in encoded
    assert "weighted_score" not in encoded
    assert "aggregate_score" not in encoded
    if os.name != "nt":
        assert all(path.stat().st_mode & 0o222 == 0 for path in output.rglob("*"))
        assert output.stat().st_mode & 0o222 == 0


def test_freezer_compares_generator_elapsed_to_generation_phase_time(
    tmp_path: Path,
) -> None:
    fixture = _evidence_fixture(tmp_path)
    run = Path(fixture["runs"][0])
    generator = Path(fixture["generators"][0])
    manifest_path = run / "quality_evaluation_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    whole_chain_seconds = manifest["execution"]["wall_clock_seconds"]
    generation_seconds = whole_chain_seconds - 90.0
    manifest["execution"]["generation_wall_clock_seconds"] = generation_seconds

    generation_path = generator / "generation_manifest.json"
    generation = json.loads(generation_path.read_text())
    generation["elapsed_seconds"] = generation_seconds
    generation_path.write_text(json.dumps(generation) + "\n")
    manifest["execution"]["generator_artifact_root_sha256"] = (
        _rewrite_generator_hash_manifest(generator)
    )
    manifest_path.write_text(json.dumps(manifest) + "\n")

    output = _freeze(fixture, tmp_path / "phase-timing")

    assert output.is_dir()


def test_freezer_rejects_generator_candidate_tamper(tmp_path: Path) -> None:
    fixture = _evidence_fixture(tmp_path)
    generator = Path(fixture["generators"][0])
    (generator / "first_pass" / "candidate.json").write_text("tampered\n")

    with pytest.raises(BaselineError, match="generator artifact hash mismatch"):
        _freeze(fixture, tmp_path / "tampered")


def test_freezer_rejects_recomputed_generator_manifest_without_evaluation_anchor(
    tmp_path: Path,
) -> None:
    fixture = _evidence_fixture(tmp_path)
    generator = Path(fixture["generators"][0])
    (generator / "first_pass" / "candidate.json").write_text("tampered\n")
    _rewrite_generator_hash_manifest(generator)

    with pytest.raises(BaselineError, match="evaluation artifact root authority mismatch"):
        _freeze(fixture, tmp_path / "recomputed")


def test_freezer_rejects_unmanifested_nested_same_name_file(tmp_path: Path) -> None:
    fixture = _evidence_fixture(tmp_path)
    generator = Path(fixture["generators"][0])
    (generator / "first_pass" / "artifact_hash_manifest.json").write_text(
        '{"candidate":"unmanifested"}\n'
    )

    with pytest.raises(BaselineError, match="generator artifact set"):
        _freeze(fixture, tmp_path / "nested-control-name")


def test_freezer_rejects_current_evaluation_anchor_mismatch(tmp_path: Path) -> None:
    fixture = _evidence_fixture(tmp_path)
    run = Path(fixture["runs"][0])
    manifest_path = run / "quality_evaluation_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["execution"]["generator_artifact_root_sha256"] = "0" * 64
    manifest_path.write_text(json.dumps(manifest) + "\n")

    with pytest.raises(BaselineError, match="evaluation artifact root authority mismatch"):
        _freeze(fixture, tmp_path / "wrong-authority")


@pytest.mark.parametrize("valid", [True, False], ids=("valid", "invalid-root"))
def test_freezer_validates_non_circular_legacy_generator_anchor(
    tmp_path: Path, valid: bool
) -> None:
    fixture = _evidence_fixture(tmp_path)
    generator = Path(fixture["generators"][0])
    root_sha = _rewrite_generator_hash_manifest(generator, legacy=True)
    generation_path = generator / "generation_manifest.json"
    generation = json.loads(generation_path.read_text())
    generation.pop("artifact_hash_manifest")
    generation["artifact_root_sha256"] = root_sha if valid else "0" * 64
    generation_path.write_text(json.dumps(generation) + "\n")

    if valid:
        assert _freeze(fixture, tmp_path / "legacy-valid").is_dir()
    else:
        with pytest.raises(BaselineError, match="legacy generator artifact root mismatch"):
            _freeze(fixture, tmp_path / "legacy-invalid")


@pytest.mark.parametrize("anchor_mode", ["both", "neither"])
def test_freezer_rejects_ambiguous_generator_anchor_contract(
    tmp_path: Path, anchor_mode: str
) -> None:
    fixture = _evidence_fixture(tmp_path)
    generator = Path(fixture["generators"][0])
    generation_path = generator / "generation_manifest.json"
    generation = json.loads(generation_path.read_text())
    if anchor_mode == "both":
        generation["artifact_root_sha256"] = "0" * 64
    else:
        generation.pop("artifact_hash_manifest")
    generation_path.write_text(json.dumps(generation) + "\n")

    with pytest.raises(BaselineError, match="anchor contract is ambiguous"):
        _freeze(fixture, tmp_path / f"anchor-{anchor_mode}")


def test_bundle_remains_replayable_after_external_evidence_is_deleted(
    tmp_path: Path,
) -> None:
    fixture = _evidence_fixture(tmp_path)
    output = _freeze(fixture, tmp_path / "baseline")
    shutil.rmtree(fixture["evidence_root"])

    retained = sorted((output / "runs").glob("*/evaluation"))
    result = compare_historical_replay(retained, output)

    assert len(retained) == 12
    assert result["status"] == "compared"
    assert result["regressions"] == []


def test_freezer_reads_only_staged_copy_after_source_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.services.quality_baseline_freezer as freezer

    fixture = _evidence_fixture(tmp_path)
    original = freezer._copy_evaluation_once

    def copy_then_mutate(source: Path, destination: Path) -> None:
        original(source, destination)
        (source / "quality_evaluation_report.json").write_text('{"tampered":true}\n')

    monkeypatch.setattr(freezer, "_copy_evaluation_once", copy_then_mutate)

    output = _freeze(fixture, tmp_path / "baseline")

    assert json.loads((output / "release_gate.json").read_text())["release_gate"] == "pass"
    assert json.loads((output / "baseline_manifest.json").read_text())["bundle_status"] == "passed"


def test_freezer_refuses_partial_corpus_and_existing_destination(tmp_path: Path) -> None:
    fixture = _evidence_fixture(tmp_path)
    existing = tmp_path / "existing"
    existing.mkdir()
    with pytest.raises(FileExistsError):
        _freeze(fixture, existing)

    with pytest.raises(BaselineError, match="complete 12-case corpus"):
        freeze_baseline_output(
            run_directories=fixture["runs"][:-1],
            generator_directories=fixture["generators"][:-1],
            registry_path=fixture["registry"],
            repository_root=fixture["repository"],
            thresholds=_thresholds(),
            calibration_audit=_audit(),
            work_sufficiency_audit=fixture["work_audit"],
            rapid_run_directories=fixture["rapid_runs"],
            rapid_generator_directories=fixture["rapid_generators"],
            deep_run_directories=fixture["deep_runs"],
            deep_generator_directories=fixture["deep_generators"],
            output_directory=tmp_path / "partial",
        )
    assert not (tmp_path / "partial").exists()


def test_freezer_rejects_caller_corpus_mapping_and_synthetic_registry(
    tmp_path: Path,
) -> None:
    assert "corpus_cases" not in inspect.signature(freeze_baseline_output).parameters
    fixture = _evidence_fixture(tmp_path)
    repository = fixture["repository"]
    registry_path = fixture["registry"]
    registry = json.loads(registry_path.read_text())
    registry["projects"][0]["id"] = "project-00"
    registry_path.write_text(json.dumps(registry))
    _run_git(repository, "add", "benchmarks/quality/registry.json")
    _run_git(
        repository,
        "-c",
        "commit.gpgsign=false",
        "commit",
        "-qm",
        "synthetic registry mutation",
    )

    with pytest.raises(QualityCorpusError, match="domain authority"):
        _freeze(fixture, tmp_path / "synthetic-output", registry_path=registry_path)
    assert not (tmp_path / "synthetic-output").exists()


def test_freezer_rejects_registry_outside_the_clean_codetalk_revision(
    tmp_path: Path,
) -> None:
    fixture = _evidence_fixture(tmp_path)
    copied = tmp_path / "external-quality"
    shutil.copytree(REGISTRY_PATH.parent, copied)

    with pytest.raises(BaselineError, match="formal registry"):
        _freeze(
            fixture,
            tmp_path / "external-output",
            registry_path=copied / "registry.json",
        )


def test_freezer_requires_clean_codetalk_and_binds_evaluator_bytes(tmp_path: Path) -> None:
    fixture = _evidence_fixture(tmp_path)
    repository = fixture["repository"]
    identity = load_clean_evaluation_identity(repository)
    assert len(identity.evaluator_sha256) == 64
    (repository / EVALUATOR_SOURCE_PATHS[0]).write_text("# dirty mutation\n")

    with pytest.raises(BaselineError, match="clean CodeTalk worktree"):
        _freeze(fixture, tmp_path / "dirty-output")


def _write_cli_inputs(tmp_path: Path, fixture: dict[str, object]) -> dict[str, Path]:
    values = {
        "thresholds.json": _thresholds(),
        "audit.json": _audit(),
        "work.json": fixture["work_audit"],
    }
    paths: dict[str, Path] = {}
    for name, value in values.items():
        path = tmp_path / name
        path.write_text(json.dumps(value), encoding="utf-8")
        paths[name] = path
    return paths


def _cli_args(
    fixture: dict[str, object], inputs: dict[str, Path], output: Path
) -> list[str]:
    return [
        "--runs-root", str(Path(fixture["runs"][0]).parent),
        "--run-artifacts-root", str(Path(fixture["generators"][0]).parent),
        "--rapid-runs-root", str(Path(fixture["rapid_runs"][0]).parent),
        "--rapid-run-artifacts-root", str(Path(fixture["rapid_generators"][0]).parent),
        "--deep-runs-root", str(Path(fixture["deep_runs"][0]).parent),
        "--deep-run-artifacts-root", str(Path(fixture["deep_generators"][0]).parent),
        "--registry", str(fixture["registry"]),
        "--repository-root", str(fixture["repository"]),
        "--thresholds", str(inputs["thresholds.json"]),
        "--calibration-audit", str(inputs["audit.json"]),
        "--work-sufficiency-audit", str(inputs["work.json"]),
        "--output", str(output),
    ]


def test_freezer_cli_discovers_evidence_and_returns_zero_for_pass(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    fixture = _evidence_fixture(tmp_path)
    inputs = _write_cli_inputs(tmp_path, fixture)
    output = tmp_path / "published"

    assert main(_cli_args(fixture, inputs, output)) == 0
    assert capsys.readouterr().out.strip() == str(output.resolve())
    matrix = json.loads((output / "regression_matrix.json").read_text())
    assert matrix["rapid_vs_deep"]["evidence_kind"] == "paired_immutable_reports"
    assert matrix["core_baseline_blocked"] is False


def test_blocked_release_is_atomically_published_but_cli_returns_nonzero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    fixture = _evidence_fixture(tmp_path, rapid_limit_override=901.0)
    inputs = _write_cli_inputs(tmp_path, fixture)
    output = tmp_path / "blocked"

    assert main(_cli_args(fixture, inputs, output)) == 2
    assert capsys.readouterr().out.strip() == str(output.resolve())
    assert json.loads((output / "release_gate.json").read_text())["release_gate"] == "fail"
    assert json.loads((output / "regression_matrix.json").read_text())["core_baseline_blocked"] is True
    assert json.loads((output / "baseline_manifest.json").read_text())["bundle_status"] == "blocked"
    assert output.is_dir()


def test_cli_has_no_caller_corpus_versions_or_rapid_status_inputs() -> None:
    with pytest.raises(SystemExit):
        main(
            [
                "--corpus-cases", "synthetic.json",
                "--versions", "caller.json",
                "--rapid-deep-result", "status.json",
            ]
        )
