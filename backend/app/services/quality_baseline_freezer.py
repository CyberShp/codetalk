"""Atomic publication of a reviewed, self-contained F012 baseline bundle."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from app.services.quality_baseline import (
    BUNDLE_SCHEMA_VERSION,
    HUMAN_REPORT_FILENAME,
    MANIFEST_FILENAME,
    REPORT_FILENAME,
    BaselineError,
    EvaluationCodeIdentity,
    build_baseline_summary,
    build_regression_matrix,
    compare_historical_replay,
    compare_rapid_deep_runs,
    evaluate_release_policy,
    freeze_threshold_policy,
    load_clean_evaluation_identity,
    load_immutable_evaluation,
    serialize_baseline_data,
)
from app.services.quality_benchmark_corpus import (
    QualityBaselineCorpusIdentity,
    load_quality_baseline_corpus,
)
from app.services.quality_benchmark_runner import _rename_directory_noreplace


GENERATOR_REQUIRED_FILES = (
    "repair_summary.json",
    "versions.json",
    "generation_manifest.json",
    "artifact_hash_manifest.json",
)
GENERATOR_REQUIRED_DIRECTORIES = ("first_pass", "final_after_auto_repair")
GENERATOR_OPTIONAL_FILES = ("workbench_audit.json",)


def freeze_baseline_output(
    *,
    run_directories: Sequence[str | Path],
    generator_directories: Sequence[str | Path],
    registry_path: str | Path,
    repository_root: str | Path,
    thresholds: Mapping[str, Mapping[str, float]],
    calibration_audit: Mapping[str, Any],
    work_sufficiency_audit: Mapping[str, Any],
    rapid_run_directories: Sequence[str | Path],
    rapid_generator_directories: Sequence[str | Path],
    deep_run_directories: Sequence[str | Path],
    deep_generator_directories: Sequence[str | Path],
    output_directory: str | Path,
    previous_baseline_directory: str | Path | None = None,
) -> Path:
    """Copy once, validate from staging, and atomically publish pass/block evidence."""

    output = Path(output_directory).resolve()
    if output.exists():
        raise FileExistsError(f"immutable baseline output already exists: {output}")
    evaluation_identity = load_clean_evaluation_identity(repository_root)
    formal_registry = evaluation_identity.repository_root / "benchmarks/quality/registry.json"
    try:
        requested_registry = Path(registry_path).resolve(strict=True)
        resolved_formal_registry = formal_registry.resolve(strict=True)
    except OSError as exc:
        raise BaselineError("the formal registry is unavailable") from exc
    if requested_registry != resolved_formal_registry:
        raise BaselineError(
            "registry must be the formal registry in the clean CodeTalk revision"
        )
    corpus = load_quality_baseline_corpus(resolved_formal_registry)
    _require_tracked_corpus(evaluation_identity, corpus)

    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(dir=output.parent, prefix=f".{output.name}."))
    try:
        core = _stage_evidence_group(
            staging,
            group="core",
            run_directories=run_directories,
            generator_directories=generator_directories,
            corpus=corpus,
            evaluation_identity=evaluation_identity,
        )
        rapid = _stage_evidence_group(
            staging,
            group="rapid",
            run_directories=rapid_run_directories,
            generator_directories=rapid_generator_directories,
            corpus=corpus,
            evaluation_identity=evaluation_identity,
        )
        deep = _stage_evidence_group(
            staging,
            group="deep",
            run_directories=deep_run_directories,
            generator_directories=deep_generator_directories,
            corpus=corpus,
            evaluation_identity=evaluation_identity,
        )

        core_runs = _publish_staged_core(staging, core)
        rapid_runs = _publish_staged_comparison(staging, rapid, "rapid")
        deep_runs = _publish_staged_comparison(staging, deep, "deep")
        shutil.rmtree(staging / ".incoming")
        summary = build_baseline_summary(
            core_runs,
            corpus=corpus,
            evaluation_identity=evaluation_identity,
            work_sufficiency_audit=work_sufficiency_audit,
        )
        policy = freeze_threshold_policy(
            summary,
            thresholds=thresholds,
            calibration_audit=calibration_audit,
        )
        release_gate = evaluate_release_policy(summary, policy)
        rapid_deep = compare_rapid_deep_runs(
            rapid_runs,
            deep_runs,
            corpus=corpus,
            evaluation_identity=evaluation_identity,
            work_sufficiency_audit=work_sufficiency_audit,
        )

        previous_staged: Path | None = None
        if previous_baseline_directory is not None:
            previous_staged = staging / ".previous-baseline"
            _copy_tree_once(
                Path(previous_baseline_directory), previous_staged, label="previous baseline"
            )
        history = compare_historical_replay(core_runs, previous_staged)
        regression = build_regression_matrix(
            release_gate=release_gate,
            historical_replay=history,
            rapid_deep_comparison=rapid_deep,
        )
        if previous_staged is not None:
            _make_tree_writable(previous_staged)
            shutil.rmtree(previous_staged)

        environment = _environment_manifest(core_runs, evaluation_identity)
        artifacts: dict[str, bytes] = {
            "baseline_summary.json": serialize_baseline_data(summary).encode(),
            "threshold_policy.json": serialize_baseline_data(policy).encode(),
            "calibration_anomalies.json": serialize_baseline_data(
                policy["calibration_audit"]
            ).encode(),
            "release_gate.json": serialize_baseline_data(release_gate).encode(),
            "regression_matrix.json": serialize_baseline_data(regression).encode(),
            "environment_manifest.json": serialize_baseline_data(environment).encode(),
            "baseline_report.md": _render_baseline_markdown(
                summary, release_gate, regression
            ).encode(),
        }
        for name, payload in artifacts.items():
            (staging / name).write_bytes(payload)

        bundle_status = (
            "passed"
            if release_gate["release_gate"] == "pass"
            and regression["core_baseline_blocked"] is False
            else "blocked"
        )
        source_hashes = _source_run_hashes(core_runs)
        artifact_hashes = _tree_hashes(staging, exclude={"baseline_manifest.json"})
        manifest = {
            "schema_version": BUNDLE_SCHEMA_VERSION,
            "bundle_status": bundle_status,
            "artifact_sha256": artifact_hashes,
            "source_run_sha256": source_hashes,
            "registry_sha256": corpus.registry_sha256,
            "corpus_sha256": corpus.corpus_sha256,
            "case_identities": {
                case.case_id: case.as_dict() for case in corpus.cases
            },
            "evaluation_identity": evaluation_identity.as_dict(),
            "model": summary["identity"]["model"],
        }
        (staging / "baseline_manifest.json").write_text(
            serialize_baseline_data(manifest), encoding="utf-8"
        )
        _make_tree_read_only(staging)
        _rename_directory_noreplace(staging, output)
        output.chmod(0o555)
    finally:
        if staging.exists():
            _make_tree_writable(staging)
            shutil.rmtree(staging)
    return output


def _stage_evidence_group(
    staging: Path,
    *,
    group: str,
    run_directories: Sequence[str | Path],
    generator_directories: Sequence[str | Path],
    corpus: QualityBaselineCorpusIdentity,
    evaluation_identity: EvaluationCodeIdentity,
) -> list[tuple[str, Path]]:
    if len(run_directories) != len(generator_directories):
        raise BaselineError(f"{group} evaluation/generator counts do not match")
    if not run_directories:
        raise BaselineError(f"{group} evidence set must not be empty")
    incoming = staging / ".incoming" / group
    incoming.mkdir(parents=True, exist_ok=True)
    result: list[tuple[str, Path]] = []
    seen: set[str] = set()
    for index, (run_directory, generator_directory) in enumerate(
        zip(run_directories, generator_directories, strict=True)
    ):
        pair_root = incoming / f"{index:03d}"
        evaluation = pair_root / "evaluation"
        generator = pair_root / "generator"
        _copy_evaluation_once(Path(run_directory), evaluation)
        _copy_generator_once(Path(generator_directory), generator)
        loaded = load_immutable_evaluation(
            evaluation,
            expected_identity=evaluation_identity,
            require_execution=True,
        )
        case_id = str(loaded.manifest["case_id"])
        if case_id in seen:
            raise BaselineError(f"duplicate {group} case_id: {case_id}")
        expected_case = corpus.case_map.get(case_id)
        if expected_case is None:
            raise BaselineError(f"{group} case is outside formal corpus: {case_id}")
        load_immutable_evaluation(
            evaluation,
            expected_case=expected_case,
            expected_identity=evaluation_identity,
            require_execution=True,
        )
        _validate_generator_evidence(generator, loaded)
        result.append((case_id, pair_root))
        seen.add(case_id)
    return result


def _require_tracked_corpus(
    identity: EvaluationCodeIdentity, corpus: QualityBaselineCorpusIdentity
) -> None:
    relative_paths = ["benchmarks/quality/registry.json"]
    for case in corpus.cases:
        case_root = Path("benchmarks/quality/projects") / case.project_id / case.case_id
        relative_paths.append((case_root / "case.json").as_posix())
        relative_paths.extend(
            (case_root / truth_path).as_posix()
            for _, truth_path, _ in case.truth_sha256
        )
    completed = subprocess.run(
        [
            "git",
            "-C",
            str(identity.repository_root),
            "ls-files",
            "--error-unmatch",
            "--",
            *relative_paths,
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )
    if completed.returncode != 0:
        raise BaselineError(
            "formal registry, case descriptors, and truth files must be tracked by the clean CodeTalk revision"
        )


def _publish_staged_core(
    staging: Path, pairs: Sequence[tuple[str, Path]]
) -> list[Path]:
    runs_root = staging / "runs"
    runs_root.mkdir()
    result: list[Path] = []
    for case_id, pair_root in sorted(pairs):
        destination = runs_root / case_id
        if destination.exists():
            raise BaselineError(f"duplicate staged core case_id: {case_id}")
        pair_root.rename(destination)
        result.append(destination / "evaluation")
    return result


def _publish_staged_comparison(
    staging: Path,
    pairs: Sequence[tuple[str, Path]],
    profile: str,
) -> list[Path]:
    root = staging / "comparisons" / "rapid-deep"
    root.mkdir(parents=True, exist_ok=True)
    result: list[Path] = []
    for case_id, pair_root in sorted(pairs):
        destination = root / case_id / profile
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            raise BaselineError(f"duplicate {profile} comparison case_id: {case_id}")
        pair_root.rename(destination)
        result.append(destination / "evaluation")
    return result


def _copy_evaluation_once(source: Path, destination: Path) -> None:
    resolved = _safe_source_directory(source, "evaluation run")
    destination.mkdir(parents=True)
    for name in (REPORT_FILENAME, HUMAN_REPORT_FILENAME, MANIFEST_FILENAME):
        path = resolved / name
        if not path.is_file() or path.is_symlink():
            raise BaselineError(f"evaluation evidence is missing or unsafe: {path}")
        shutil.copy2(path, destination / name, follow_symlinks=False)


def _copy_generator_once(source: Path, destination: Path) -> None:
    resolved = _safe_source_directory(source, "generator run")
    destination.mkdir(parents=True)
    for name in GENERATOR_REQUIRED_DIRECTORIES:
        path = resolved / name
        _reject_symlinks(path, label=f"generator {name}")
        if not path.is_dir():
            raise BaselineError(f"generator evidence directory is missing: {path}")
        shutil.copytree(path, destination / name, symlinks=False)
    for name in GENERATOR_REQUIRED_FILES:
        path = resolved / name
        if not path.is_file() or path.is_symlink():
            raise BaselineError(f"generator evidence file is missing or unsafe: {path}")
        shutil.copy2(path, destination / name, follow_symlinks=False)
    for name in GENERATOR_OPTIONAL_FILES:
        path = resolved / name
        if path.exists():
            if not path.is_file() or path.is_symlink():
                raise BaselineError(f"generator evidence file is unsafe: {path}")
            shutil.copy2(path, destination / name, follow_symlinks=False)


def _validate_generator_evidence(
    generator: Path, evaluation: Any
) -> None:
    versions = _read_json_mapping(generator / "versions.json", "generator versions")
    manifest_versions = _mapping(evaluation.manifest.get("versions"), "versions")
    if versions != dict(manifest_versions):
        raise BaselineError("generator/evaluation version identity mismatch")

    generation = _read_json_mapping(
        generator / "generation_manifest.json", "generation manifest"
    )
    execution = _mapping(evaluation.manifest.get("execution"), "execution")
    checks = {
        "case_id": (generation.get("case_id"), evaluation.manifest.get("case_id")),
        "mode": (generation.get("mode"), execution.get("profile")),
        "model": (generation.get("model"), versions.get("model")),
        "codetalk_revision": (
            generation.get("codetalk_revision"),
            versions.get("codetalk"),
        ),
    }
    for label, (observed, expected) in checks.items():
        if observed != expected:
            raise BaselineError(f"generator/evaluation {label} mismatch")
    elapsed = generation.get("elapsed_seconds")
    wall = execution.get("wall_clock_seconds")
    if (
        isinstance(elapsed, bool)
        or not isinstance(elapsed, (int, float))
        or abs(float(elapsed) - float(wall)) > 0.001
    ):
        raise BaselineError("generation elapsed time does not match execution evidence")

    repair = _read_json_mapping(generator / "repair_summary.json", "repair summary")
    expected_repair = evaluation.report.repair_summary.model_dump(mode="json")
    if repair != expected_repair:
        raise BaselineError("generator/evaluation repair summary mismatch")

    hash_manifest = _read_json_mapping(
        generator / "artifact_hash_manifest.json", "generator artifact hash manifest"
    )
    artifacts = _mapping(hash_manifest.get("artifacts"), "generator artifact hashes")
    manifest_ref = generation.get("artifact_hash_manifest")
    legacy_root = generation.get("artifact_root_sha256")
    current_contract = manifest_ref is not None
    legacy_contract = legacy_root is not None
    if current_contract == legacy_contract:
        raise BaselineError("generator artifact anchor contract is ambiguous")
    if current_contract:
        if manifest_ref != "artifact_hash_manifest.json":
            raise BaselineError("generator artifact manifest reference is invalid")
        actual_files = {
            path.relative_to(generator).as_posix(): path
            for path in generator.rglob("*")
            if path.is_file()
            and path.relative_to(generator).as_posix()
            != "artifact_hash_manifest.json"
        }
    else:
        actual_files = {
            path.relative_to(generator).as_posix(): path
            for root_name in GENERATOR_REQUIRED_DIRECTORIES
            for path in (generator / root_name).rglob("*")
            if path.is_file()
        }
    if set(actual_files) != set(artifacts):
        raise BaselineError("generator artifact set does not match hash manifest")
    retained: dict[str, dict[str, Any]] = {}
    for relative, path in sorted(actual_files.items()):
        data = path.read_bytes()
        descriptor = _mapping(artifacts[relative], f"generator hash {relative}")
        digest = hashlib.sha256(data).hexdigest()
        if descriptor.get("sha256") != digest or descriptor.get("size_bytes") != len(data):
            raise BaselineError(f"generator artifact hash mismatch: {relative}")
        retained[relative] = {"sha256": digest, "size_bytes": len(data)}
    canonical = json.dumps(
        retained, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    root_digest = hashlib.sha256(canonical).hexdigest()
    if hash_manifest.get("root_sha256") != root_digest:
        raise BaselineError("generator artifact root hash mismatch")
    if current_contract:
        if execution.get("generator_artifact_root_sha256") != root_digest:
            raise BaselineError("evaluation artifact root authority mismatch")
    elif legacy_root != root_digest:
        raise BaselineError("legacy generator artifact root mismatch")

    human_path = evaluation.directory / HUMAN_REPORT_FILENAME
    if hashlib.sha256(human_path.read_bytes()).hexdigest() != evaluation.manifest.get(
        "human_report_sha256"
    ):
        raise BaselineError("human evaluation report hash mismatch")


def _copy_tree_once(source: Path, destination: Path, *, label: str) -> None:
    resolved = _safe_source_directory(source, label)
    _reject_symlinks(resolved, label=label)
    shutil.copytree(resolved, destination, symlinks=False)


def _safe_source_directory(path: Path, label: str) -> Path:
    if path.is_symlink():
        raise BaselineError(f"{label} must not be a symlink: {path}")
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise BaselineError(f"{label} is unavailable: {path}") from exc
    if not resolved.is_dir():
        raise BaselineError(f"{label} is not a directory: {resolved}")
    return resolved


def _reject_symlinks(root: Path, *, label: str) -> None:
    if root.is_symlink():
        raise BaselineError(f"{label} contains a symlink: {root}")
    if not root.exists():
        return
    for path in root.rglob("*"):
        if path.is_symlink():
            raise BaselineError(f"{label} contains a symlink: {path}")


def _source_run_hashes(
    run_directories: Sequence[str | Path],
) -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    for directory in run_directories:
        root = Path(directory)
        manifest_path = root / MANIFEST_FILENAME
        report_path = root / REPORT_FILENAME
        human_path = root / HUMAN_REPORT_FILENAME
        manifest = _read_json_mapping(manifest_path, "quality evaluation manifest")
        case_id = str(manifest.get("case_id", ""))
        if not case_id or case_id in result:
            raise BaselineError("source run manifests require unique case_id values")
        result[case_id] = {
            "manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
            "report_sha256": hashlib.sha256(report_path.read_bytes()).hexdigest(),
            "human_report_sha256": hashlib.sha256(human_path.read_bytes()).hexdigest(),
        }
    return dict(sorted(result.items()))


def _environment_manifest(
    run_directories: Sequence[str | Path], identity: EvaluationCodeIdentity
) -> dict[str, Any]:
    runs: list[dict[str, Any]] = []
    models: set[str] = set()
    for directory in run_directories:
        root = Path(directory)
        payload = _read_json_mapping(root / MANIFEST_FILENAME, "evaluation manifest")
        versions = _mapping(payload.get("versions"), "versions")
        models.add(str(versions.get("model")))
        runs.append(
            {
                "case_id": payload.get("case_id"),
                "run_ref": payload.get("run_ref"),
                "environment": payload.get("environment"),
                "execution": payload.get("execution"),
            }
        )
    return {
        "schema_version": "quality-baseline-environment-v2",
        "evaluation_identity": identity.as_dict(),
        "model": next(iter(models)) if len(models) == 1 else None,
        "runs": sorted(runs, key=lambda item: str(item["case_id"])),
    }


def _render_baseline_markdown(
    summary: Mapping[str, Any],
    release_gate: Mapping[str, Any],
    regression: Mapping[str, Any],
) -> str:
    coverage = summary["coverage"]
    lines = [
        "# F012 Independent Quality Baseline",
        "",
        f"Bundle status: {'blocked' if regression['core_baseline_blocked'] else 'passed'}",
        f"Corpus coverage: {coverage['observed']}/{coverage['expected']}",
        "",
        "## Per-domain final distributions",
        "",
    ]
    for domain, axes in sorted(summary["domains"].items()):
        lines.extend([f"### {domain}", ""])
        for axis, metrics in sorted(axes.items()):
            lines.append(f"#### {axis.title()}")
            lines.append("")
            lines.append("| Metric | Min | Mean | P50 | P100 |")
            lines.append("|---|---:|---:|---:|---:|")
            for metric, phases in sorted(metrics.items()):
                final = phases["final"]
                lines.append(
                    f"| `{metric}` | {final['minimum']:.3f} | {final['mean']:.3f} | "
                    f"{final['p50']:.3f} | {final['p100']:.3f} |"
                )
            lines.append("")
    lines.extend(["## Independent release gates", ""])
    for axis, result in sorted(release_gate["axes"].items()):
        lines.append(f"- {axis.title()}: `{result['gate']}`")
    lines.extend(
        [
            f"- Delivery: `{release_gate['delivery_gate']}`",
            "",
            "## Timing",
            "",
            f"- Rapid: `{summary['timing']['rapid']['gate']}`",
            f"- Deep: `{summary['timing']['deep']['gate']}`",
            f"- Under-five-minute work sufficiency: `{summary['timing']['work_sufficiency_gate']}`",
            "",
        ]
    )
    return "\n".join(lines)


def _tree_hashes(root: Path, *, exclude: set[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        if relative in exclude:
            continue
        result[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def _make_tree_read_only(root: Path) -> None:
    for path in sorted(root.rglob("*"), key=lambda item: len(item.parts), reverse=True):
        path.chmod(0o555 if path.is_dir() else 0o444)
    # macOS renamex_np requires the staging directory itself to remain writable.
    root.chmod(0o700)


def _make_tree_writable(root: Path) -> None:
    for path in [root, *root.rglob("*")]:
        try:
            if not path.is_symlink():
                path.chmod(0o700 if path.is_dir() else 0o600)
        except OSError:
            pass


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Freeze a reviewed F012 quality baseline bundle."
    )
    parser.add_argument("--runs-root", required=True, type=Path)
    parser.add_argument("--run-artifacts-root", required=True, type=Path)
    parser.add_argument("--rapid-runs-root", required=True, type=Path)
    parser.add_argument("--rapid-run-artifacts-root", required=True, type=Path)
    parser.add_argument("--deep-runs-root", required=True, type=Path)
    parser.add_argument("--deep-run-artifacts-root", required=True, type=Path)
    parser.add_argument(
        "--registry", default=Path("benchmarks/quality/registry.json"), type=Path
    )
    parser.add_argument(
        "--repository-root", default=Path(__file__).resolve().parents[3], type=Path
    )
    parser.add_argument("--thresholds", required=True, type=Path)
    parser.add_argument("--calibration-audit", required=True, type=Path)
    parser.add_argument("--work-sufficiency-audit", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--previous-baseline", type=Path)
    args = parser.parse_args(argv)

    core_runs, core_generators = _discover_evidence_pairs(
        args.runs_root, args.run_artifacts_root
    )
    rapid_runs, rapid_generators = _discover_evidence_pairs(
        args.rapid_runs_root, args.rapid_run_artifacts_root
    )
    deep_runs, deep_generators = _discover_evidence_pairs(
        args.deep_runs_root, args.deep_run_artifacts_root
    )
    output = freeze_baseline_output(
        run_directories=core_runs,
        generator_directories=core_generators,
        registry_path=args.registry,
        repository_root=args.repository_root,
        thresholds=_read_json_mapping(args.thresholds, "thresholds"),
        calibration_audit=_read_json_mapping(
            args.calibration_audit, "calibration audit"
        ),
        work_sufficiency_audit=_read_json_mapping(
            args.work_sufficiency_audit, "work sufficiency audit"
        ),
        rapid_run_directories=rapid_runs,
        rapid_generator_directories=rapid_generators,
        deep_run_directories=deep_runs,
        deep_generator_directories=deep_generators,
        output_directory=args.output,
        previous_baseline_directory=args.previous_baseline,
    )
    print(output)
    release = _read_json_mapping(output / "release_gate.json", "release gate")
    regression = _read_json_mapping(
        output / "regression_matrix.json", "regression matrix"
    )
    return (
        0
        if release.get("release_gate") == "pass"
        and regression.get("core_baseline_blocked") is False
        else 2
    )


def _discover_evidence_pairs(
    runs_root: Path, generator_root: Path
) -> tuple[list[Path], list[Path]]:
    runs = _discover_run_directories(runs_root)
    generators = _discover_generator_directories(generator_root)
    run_map = {_case_id_from_evaluation(path): path for path in runs}
    generator_map = {_case_id_from_generation(path): path for path in generators}
    if len(run_map) != len(runs) or len(generator_map) != len(generators):
        raise BaselineError("evidence roots contain duplicate case_id values")
    if set(run_map) != set(generator_map):
        raise BaselineError("evaluation/generator evidence case sets do not match")
    case_ids = sorted(run_map)
    return (
        [run_map[case_id] for case_id in case_ids],
        [generator_map[case_id] for case_id in case_ids],
    )


def _discover_run_directories(root: Path) -> list[Path]:
    resolved = _safe_source_directory(root, "evaluation root")
    directories = sorted(
        path
        for path in resolved.iterdir()
        if path.is_dir()
        and not path.is_symlink()
        and (path / REPORT_FILENAME).is_file()
        and (path / MANIFEST_FILENAME).is_file()
    )
    if not directories:
        raise BaselineError(f"no quality evaluation runs found under {resolved}")
    return directories


def _discover_generator_directories(root: Path) -> list[Path]:
    resolved = _safe_source_directory(root, "generator root")
    directories = sorted(
        path
        for path in resolved.iterdir()
        if path.is_dir()
        and not path.is_symlink()
        and (path / "generation_manifest.json").is_file()
    )
    if not directories:
        raise BaselineError(f"no generator runs found under {resolved}")
    return directories


def _case_id_from_evaluation(path: Path) -> str:
    return str(
        _read_json_mapping(path / MANIFEST_FILENAME, "evaluation manifest").get(
            "case_id", ""
        )
    )


def _case_id_from_generation(path: Path) -> str:
    return str(
        _read_json_mapping(path / "generation_manifest.json", "generation manifest").get(
            "case_id", ""
        )
    )


def _read_json_mapping(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BaselineError(f"invalid {label}: {exc}") from exc
    if not isinstance(value, dict):
        raise BaselineError(f"{label} must be an object")
    return value


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise BaselineError(f"{label} must be an object")
    return value


if __name__ == "__main__":
    raise SystemExit(main())
