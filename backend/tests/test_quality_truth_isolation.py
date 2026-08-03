from __future__ import annotations

import importlib
import json
import os
import shutil
import socket
import stat
import subprocess
import sys
from pathlib import Path

import pytest

SURFACE_NAMES = (
    "task_input",
    "prompt_capture",
    "retrieval_index",
    "bundle",
    "generator_manifest",
)


def _corpus():
    try:
        return importlib.import_module("app.services.quality_benchmark_corpus")
    except ModuleNotFoundError as exc:
        pytest.fail(f"quality benchmark corpus contract is missing: {exc}")


def _clean_surfaces() -> dict[str, object]:
    return {
        "task_input": {"question": "Trace the storage initialization path."},
        "prompt_capture": ["Repository evidence only."],
        "retrieval_index": {"documents": ["src/controller.c"]},
        "bundle": {"source_files": ["src/controller.c"]},
        "generator_manifest": {"inputs": ["registry.json", "case.json"]},
    }


def _truth_paths(tmp_path: Path) -> list[Path]:
    truth_root = tmp_path / "truth-package"
    return [
        truth_root / "gold_claims.json",
        truth_root / "coverage_universe.json",
        truth_root / "critical_chains.json",
        truth_root / "execution_oracles.json",
    ]


def _nested_percent_encode(value: str, layers: int) -> str:
    encoded = "".join(f"%{byte:02X}" for byte in value.encode("utf-8"))
    for _ in range(layers - 1):
        encoded = encoded.replace("%", "%25")
    return encoded


def test_truth_isolation_accepts_clean_generator_surfaces(tmp_path: Path) -> None:
    corpus = _corpus()

    corpus.validate_truth_isolation(
        generator_surfaces=_clean_surfaces(), truth_paths=_truth_paths(tmp_path)
    )


@pytest.mark.parametrize("surface_name", SURFACE_NAMES)
def test_truth_isolation_rejects_truth_path_in_every_generator_surface(
    tmp_path: Path, surface_name: str
) -> None:
    corpus = _corpus()
    surfaces = _clean_surfaces()
    surfaces[surface_name] = {
        "nested": [str(_truth_paths(tmp_path)[0])]
    }

    with pytest.raises(corpus.TruthIsolationError, match=surface_name):
        corpus.validate_truth_isolation(
            generator_surfaces=surfaces, truth_paths=_truth_paths(tmp_path)
        )


def test_truth_isolation_rejects_truth_basename_and_mapping_key(tmp_path: Path) -> None:
    corpus = _corpus()
    surfaces = _clean_surfaces()
    surfaces["retrieval_index"] = {"gold_claims.json": {"indexed": True}}

    with pytest.raises(corpus.TruthIsolationError, match="retrieval_index"):
        corpus.validate_truth_isolation(
            generator_surfaces=surfaces, truth_paths=_truth_paths(tmp_path)
        )


def test_truth_isolation_rejects_windows_separator_variant(tmp_path: Path) -> None:
    corpus = _corpus()
    surfaces = _clean_surfaces()
    surfaces["bundle"] = {
        "path": str(_truth_paths(tmp_path)[1]).replace("/", "\\")
    }

    with pytest.raises(corpus.TruthIsolationError, match="bundle"):
        corpus.validate_truth_isolation(
            generator_surfaces=surfaces, truth_paths=_truth_paths(tmp_path)
        )


@pytest.mark.parametrize(
    "encoded_path",
    [
        "gold%5Fclaims%2Ejson",
        "gold%255Fclaims%252Ejson",
        "%2Ftruth-package%2Fgold%5fclaims%2ejson",
    ],
)
def test_truth_isolation_rejects_percent_encoded_truth_paths(
    tmp_path: Path, encoded_path: str
) -> None:
    corpus = _corpus()
    surfaces = _clean_surfaces()
    surfaces["prompt_capture"] = encoded_path

    with pytest.raises(corpus.TruthIsolationError, match="prompt_capture"):
        corpus.validate_truth_isolation(
            generator_surfaces=surfaces, truth_paths=_truth_paths(tmp_path)
        )


@pytest.mark.parametrize(
    "normalised_variant",
    [
        "GOLD_CLAIMS.JSON",
        "\uff47\uff4f\uff4c\uff44\uff3f\uff43\uff4c\uff41\uff49\uff4d\uff53\uff0e\uff4a\uff53\uff4f\uff4e",
    ],
)
def test_truth_isolation_rejects_case_and_unicode_normalised_variants(
    tmp_path: Path, normalised_variant: str
) -> None:
    corpus = _corpus()
    surfaces = _clean_surfaces()
    surfaces["retrieval_index"] = normalised_variant

    with pytest.raises(corpus.TruthIsolationError, match="retrieval_index"):
        corpus.validate_truth_isolation(
            generator_surfaces=surfaces, truth_paths=_truth_paths(tmp_path)
        )


def test_truth_isolation_rejects_malformed_encoding_near_truth_token(
    tmp_path: Path,
) -> None:
    corpus = _corpus()
    surfaces = _clean_surfaces()
    surfaces["generator_manifest"] = "gold_claims%2Gjson"

    with pytest.raises(corpus.TruthIsolationError, match="malformed percent encoding"):
        corpus.validate_truth_isolation(
            generator_surfaces=surfaces, truth_paths=_truth_paths(tmp_path)
        )


def test_truth_isolation_does_not_blanket_reject_unrelated_urls(tmp_path: Path) -> None:
    corpus = _corpus()
    surfaces = _clean_surfaces()
    surfaces["task_input"] = [
        "https://example.invalid/source%20map.json",
        "https://example.invalid/source%2520map.json",
        "https://example.invalid/source%2Gmap.json",
    ]

    corpus.validate_truth_isolation(
        generator_surfaces=surfaces, truth_paths=_truth_paths(tmp_path)
    )


@pytest.mark.parametrize("layers", [5, 6])
@pytest.mark.parametrize("path_form", ["basename", "full_path"])
@pytest.mark.parametrize("truth_index", range(4))
def test_truth_isolation_rejects_residual_encoding_beyond_decode_bound(
    tmp_path: Path, layers: int, path_form: str, truth_index: int
) -> None:
    corpus = _corpus()
    truth_path = _truth_paths(tmp_path)[truth_index]
    value = truth_path.name if path_form == "basename" else str(truth_path)
    surfaces = _clean_surfaces()
    surfaces["bundle"] = _nested_percent_encode(value, layers)

    with pytest.raises(corpus.TruthIsolationError, match="bundle"):
        corpus.validate_truth_isolation(
            generator_surfaces=surfaces, truth_paths=_truth_paths(tmp_path)
        )


@pytest.mark.parametrize("missing_surface", SURFACE_NAMES)
def test_truth_isolation_requires_all_generator_surfaces(
    tmp_path: Path, missing_surface: str
) -> None:
    corpus = _corpus()
    surfaces = _clean_surfaces()
    del surfaces[missing_surface]

    with pytest.raises(corpus.TruthIsolationError, match=missing_surface):
        corpus.validate_truth_isolation(
            generator_surfaces=surfaces, truth_paths=_truth_paths(tmp_path)
        )


def test_truth_isolation_rejects_unknown_generator_surface(tmp_path: Path) -> None:
    corpus = _corpus()
    surfaces = _clean_surfaces()
    surfaces["debug_dump"] = "clean"

    with pytest.raises(corpus.TruthIsolationError, match="debug_dump"):
        corpus.validate_truth_isolation(
            generator_surfaces=surfaces, truth_paths=_truth_paths(tmp_path)
        )


def test_truth_isolation_rejects_empty_truth_path_set(tmp_path: Path) -> None:
    corpus = _corpus()

    with pytest.raises(corpus.TruthIsolationError, match="truth_paths"):
        corpus.validate_truth_isolation(
            generator_surfaces=_clean_surfaces(), truth_paths=[]
        )


def test_ordinary_agent_sandbox_behavior_is_unchanged_without_benchmark_opt_in(
    tmp_path: Path,
) -> None:
    from app.services.agent_sandbox import prepare_agent_sandbox

    source = tmp_path / "source"
    source.mkdir()
    artifacts = tmp_path / "artifacts"
    launch = prepare_agent_sandbox(
        runtime={"sandbox_mode": "required", "sandbox_command": "/bin/sh"},
        cwd=str(source),
        artifact_dir=artifacts,
    )

    assert launch.status == "disabled"
    assert launch.wrapper == []


@pytest.mark.skipif(
    sys.platform != "darwin",
    reason="macOS must exercise Seatbelt; other platforms use their native integration suite",
)
def test_benchmark_os_sandbox_rejects_absolute_truth_read_on_macos(
    tmp_path: Path,
) -> None:
    from app.services.agent_sandbox import (
        benchmark_agent_sandbox,
        prepare_agent_sandbox,
    )

    sandbox_exec = shutil.which("sandbox-exec")
    assert sandbox_exec, "macOS benchmark truth-isolation requires sandbox-exec"
    source = tmp_path / "source"
    source.mkdir()
    allowed = source / "allowed.txt"
    allowed.write_text("allowed", encoding="utf-8")
    truth = tmp_path / "truth-package" / "gold_claims.json"
    truth.parent.mkdir()
    truth.write_text("hidden-truth", encoding="utf-8")
    artifacts = tmp_path / "task-runs" / "task-1" / "agent_runs" / "analyze"
    artifacts.mkdir(parents=True)
    codex_home = artifacts / ".runtime-codex-home-test"
    codex_home.mkdir()

    with benchmark_agent_sandbox(source_dir=source, model="test-model", mode="rapid"):
        launch = prepare_agent_sandbox(
            runtime={
                "sandbox_mode": "off",
                "sandbox_command": "/bin/sh",
                "sandbox_codex_home": str(codex_home),
                "sandbox_read_paths": [str(tmp_path)],
                "requires_network": False,
            },
            cwd=str(source),
            artifact_dir=artifacts,
        )

    script = (
        'cat "$ALLOWED" > "$OUT"; '
        'if secret=$(cat "$TRUTH"); '
        'then printf "%s" "$secret" > "$LEAK"; exit 91; else exit 0; fi'
    )
    env = {
        **os.environ,
        "ALLOWED": str(allowed),
        "TRUTH": str(truth),
        "OUT": str(artifacts / "allowed.out"),
        "LEAK": str(artifacts / "leak.out"),
    }
    completed = subprocess.run(
        [*launch.wrapper, "/bin/sh", "-c", script],
        cwd=source,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert launch.status == "active"
    assert completed.returncode == 0, completed.stderr
    assert (artifacts / "allowed.out").read_text(encoding="utf-8") == "allowed"
    leak_path = artifacts / "leak.out"
    leaked = leak_path.read_text(encoding="utf-8") if leak_path.exists() else ""
    assert leaked == ""
    assert "hidden-truth" not in leaked
    assert "Operation not permitted" in completed.stderr or "Permission denied" in completed.stderr
    policy = json.loads((artifacts / "sandbox_policy.json").read_text())
    assert policy["read_boundary"] == "benchmark_pinned_source_task_artifact_isolated_codex_home"
    assert str(truth.parent.resolve()) not in policy["read_paths"]


def test_benchmark_network_fails_closed_without_an_approved_target(tmp_path: Path) -> None:
    from app.services.agent_sandbox import (
        AgentSandboxError,
        benchmark_agent_sandbox,
        prepare_agent_sandbox,
    )

    source = tmp_path / "source"
    source.mkdir()
    artifacts = tmp_path / "task-runs" / "task-1" / "agent_runs" / "analyze"
    artifacts.mkdir(parents=True)
    codex_home = artifacts / ".runtime-codex-home-test"
    codex_home.mkdir()

    with benchmark_agent_sandbox(
        source_dir=source,
        model="test-model",
        mode="rapid",
        approved_network_targets=(),
    ):
        with pytest.raises(AgentSandboxError, match="approved network target"):
            prepare_agent_sandbox(
                runtime={
                    "sandbox_command": "/bin/sh",
                    "sandbox_codex_home": str(codex_home),
                    "requires_network": True,
                },
                cwd=str(source),
                artifact_dir=artifacts,
            )


def test_benchmark_isolated_codex_home_keeps_only_minimal_auth_and_sanitized_config(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from app.services.agent_sandbox import (
        benchmark_agent_sandbox,
        prepare_agent_sandbox,
    )

    source = tmp_path / "source"
    source.mkdir()
    artifacts = tmp_path / "task-runs" / "task-1" / "agent_runs" / "analyze"
    artifacts.mkdir(parents=True)
    codex_home = artifacts / ".runtime-codex-home-test"
    codex_home.mkdir()
    canary = f"auth-canary-{os.urandom(24).hex()}"
    host_home = tmp_path / "host-codex-home"
    host_home.mkdir()
    host_auth = host_home / "auth.json"
    host_auth.write_text(
        json.dumps(
            {
                "auth_mode": "chatgpt",
                "last_refresh": "2026-08-03T00:00:00Z",
                "tokens": {
                    "access_token": canary,
                    "account_id": "account-for-feature-gate",
                    "id_token": f"id-{canary}",
                    "refresh_token": f"refresh-{canary}",
                    "unapproved_token_field": "must-not-survive",
                },
                "unapproved_top_level": "must-not-survive",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("CODEX_HOME", str(host_home))
    (codex_home / "auth.json").symlink_to(host_auth)
    (codex_home / "config.toml").write_text(
        f'model_provider = "approved-proxy"\napi_key = "{canary}"\n',
        encoding="utf-8",
    )

    with benchmark_agent_sandbox(
        source_dir=source, model="test-model", mode="rapid"
    ) as security:
        launch = prepare_agent_sandbox(
            runtime={
                "sandbox_command": "/bin/sh",
                "sandbox_codex_home": str(codex_home),
                "requires_network": False,
            },
            cwd=str(source),
            artifact_dir=artifacts,
        )

    isolated_auth_path = codex_home / "auth.json"
    assert isolated_auth_path.is_file()
    assert not isolated_auth_path.is_symlink()
    assert stat.S_IMODE(isolated_auth_path.stat().st_mode) == 0o600
    isolated_auth = json.loads(isolated_auth_path.read_text(encoding="utf-8"))
    assert set(isolated_auth) == {"auth_mode", "last_refresh", "tokens"}
    assert set(isolated_auth["tokens"]) == {
        "access_token",
        "account_id",
        "id_token",
        "refresh_token",
    }
    assert isolated_auth["tokens"]["access_token"] == canary
    assert security.credential_fingerprints
    assert canary not in repr(security.credential_fingerprints)
    config = (codex_home / "config.toml").read_text(encoding="utf-8")
    assert 'model = "test-model"' in config
    assert 'model_provider = "approved-proxy"' in config
    policy = json.loads((artifacts / "sandbox_policy.json").read_text())
    assert policy["codex_home_credentials"] == "isolated_minimal"
    assert str(host_home.resolve()) not in policy["read_paths"]
    assert str(host_auth.resolve()) not in policy["read_paths"]
    assert canary not in json.dumps(launch.audit)


@pytest.mark.skipif(
    sys.platform != "darwin",
    reason="macOS must dynamically prove configured target-only Seatbelt networking",
)
def test_benchmark_network_denies_localhost_and_unapproved_external_probe_on_macos(
    tmp_path: Path,
) -> None:
    from app.services.agent_sandbox import (
        benchmark_agent_sandbox,
        prepare_agent_sandbox,
    )

    source = tmp_path / "source"
    source.mkdir()
    artifacts = tmp_path / "task-runs" / "task-1" / "agent_runs" / "analyze"
    artifacts.mkdir(parents=True)
    codex_home = artifacts / ".runtime-codex-home-test"
    codex_home.mkdir()
    approved_listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    approved_listener.bind(("127.0.0.1", 0))
    approved_listener.listen(1)
    approved_port = approved_listener.getsockname()[1]
    forbidden_listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    forbidden_listener.bind(("127.0.0.1", 0))
    forbidden_listener.listen(1)
    forbidden_port = forbidden_listener.getsockname()[1]
    try:
        with benchmark_agent_sandbox(
            source_dir=source,
            model="test-model",
            mode="rapid",
            approved_network_targets=(f"localhost:{approved_port}",),
        ):
            launch = prepare_agent_sandbox(
                runtime={
                    "sandbox_command": "/bin/sh",
                    "sandbox_codex_home": str(codex_home),
                    "requires_network": True,
                },
                cwd=str(source),
                artifact_dir=artifacts,
            )
        completed = subprocess.run(
            [
                *launch.wrapper,
                "/bin/sh",
                "-c",
                (
                    f"/usr/bin/nc -z -w 1 localhost {approved_port}; approved=$?; "
                    f"/usr/bin/nc -z -w 1 127.0.0.1 {forbidden_port}; local=$?; "
                    "/usr/bin/nc -z -w 1 1.1.1 443; external=$?; "
                    'test "$approved" -eq 0 -a "$local" -ne 0 -a "$external" -ne 0'
                ),
            ],
            cwd=source,
            text=True,
            capture_output=True,
            check=False,
        )
    finally:
        approved_listener.close()
        forbidden_listener.close()

    assert completed.returncode == 0, completed.stderr
    policy = json.loads((artifacts / "sandbox_policy.json").read_text())
    assert policy["network"] == "approved_targets_only"
    assert policy["approved_network_target_count"] == 1
