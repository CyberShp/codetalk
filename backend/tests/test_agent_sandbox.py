from __future__ import annotations

import json

import pytest

from app.services.agent_sandbox import AgentSandboxError, prepare_agent_sandbox


def test_macos_sandbox_wraps_command_and_persists_audit(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    artifact_dir = tmp_path / "artifacts"
    launch = prepare_agent_sandbox(
        runtime={
            "sandbox_mode": "required",
            "sandbox_allow_network": True,
            "sandbox_command": "/opt/homebrew/bin/opencode",
        },
        cwd=str(repo),
        artifact_dir=artifact_dir,
        platform_name="darwin",
        which=lambda command: "/usr/bin/sandbox-exec" if command == "sandbox-exec" else None,
    )

    assert launch.status == "active"
    assert launch.wrapper[:2] == ["/usr/bin/sandbox-exec", "-f"]
    profile_path = artifact_dir / "sandbox-profile.sb"
    assert launch.wrapper[2] == str(profile_path)
    profile = profile_path.read_text(encoding="utf-8")
    assert "(deny default)" in profile
    assert "(allow file-read*)\n" not in profile
    assert "(allow ipc-posix-shm*)" in profile
    assert f'(allow file-read* (subpath "{repo.resolve()}"))' in profile
    assert '(allow file-read* (subpath "/opt"))' in profile
    assert str(artifact_dir.resolve()) in profile
    audit = json.loads((artifact_dir / "sandbox_policy.json").read_text(encoding="utf-8"))
    assert audit["status"] == "active"
    assert audit["network"] == "outbound_allowed"
    assert audit["workspace_access"] == "read_only"


def test_linux_bubblewrap_mounts_workspace_readonly_and_artifacts_writable(tmp_path):
    artifact_dir = tmp_path / "artifacts"
    repo = tmp_path / "repo"
    repo.mkdir()
    launch = prepare_agent_sandbox(
        runtime={"sandbox_mode": "required", "sandbox_allow_network": False},
        cwd=str(repo),
        artifact_dir=artifact_dir,
        platform_name="linux",
        which=lambda command: "/usr/bin/bwrap" if command == "bwrap" else None,
    )

    assert launch.status == "active"
    assert launch.wrapper[0] == "/usr/bin/bwrap"
    assert "--ro-bind" in launch.wrapper
    assert "--bind" in launch.wrapper
    assert "--unshare-net" in launch.wrapper
    assert str(repo.resolve()) in launch.wrapper
    assert str(artifact_dir.resolve()) in launch.wrapper
    assert ["--ro-bind", "/", "/"] != launch.wrapper[3:6]
    assert not any(
        launch.wrapper[index : index + 3] == ["--ro-bind", "/", "/"]
        for index in range(len(launch.wrapper) - 2)
    )


def test_opencode_gets_only_provider_specific_writable_state(tmp_path, monkeypatch):
    home = tmp_path / "home"
    opencode_state = home / ".local" / "share" / "opencode"
    opencode_state.mkdir(parents=True)
    (home / ".ssh").mkdir()
    monkeypatch.setenv("HOME", str(home))

    launch = prepare_agent_sandbox(
        runtime={
            "sandbox_mode": "required",
            "sandbox_command": "/opt/homebrew/bin/opencode",
        },
        cwd=str(tmp_path),
        artifact_dir=tmp_path / "artifacts",
        platform_name="darwin",
        which=lambda command: "/usr/bin/sandbox-exec" if command == "sandbox-exec" else None,
    )

    assert str(opencode_state.resolve()) in launch.audit["runtime_state_paths"]
    assert str((home / ".ssh").resolve()) not in launch.audit["read_paths"]
    assert str((home / ".ssh").resolve()) not in launch.audit["write_paths"]


def test_required_sandbox_fails_closed_when_platform_tool_is_missing(tmp_path):
    with pytest.raises(AgentSandboxError, match="隔离"):
        prepare_agent_sandbox(
            runtime={"sandbox_mode": "required"},
            cwd=str(tmp_path),
            artifact_dir=tmp_path / "artifacts",
            platform_name="darwin",
            which=lambda _command: None,
        )


def test_auto_sandbox_records_actionable_degraded_mode(tmp_path):
    artifact_dir = tmp_path / "artifacts"
    launch = prepare_agent_sandbox(
        runtime={"sandbox_mode": "auto"},
        cwd=str(tmp_path),
        artifact_dir=artifact_dir,
        platform_name="freebsd",
        which=lambda _command: None,
    )

    assert launch.status == "degraded"
    assert launch.wrapper == []
    assert "不支持" in launch.message
    audit = json.loads((artifact_dir / "sandbox_policy.json").read_text(encoding="utf-8"))
    assert audit["status"] == "degraded"


def test_sandbox_does_not_follow_workspace_controlled_skill_symlinks(tmp_path):
    workspace = tmp_path / "repo"
    skill_target = tmp_path / "shared-skills" / "storage-test"
    skill_target.mkdir(parents=True)
    (skill_target / "SKILL.md").write_text("storage test skill", encoding="utf-8")
    skill_link = workspace / ".codex" / "skills" / "storage-test"
    skill_link.parent.mkdir(parents=True)
    skill_link.symlink_to(skill_target, target_is_directory=True)

    launch = prepare_agent_sandbox(
        runtime={"sandbox_mode": "auto"},
        cwd=str(workspace),
        artifact_dir=tmp_path / "artifacts",
        platform_name="darwin",
        which=lambda command: "/usr/bin/sandbox-exec" if command == "sandbox-exec" else None,
    )

    assert str(skill_target.resolve()) not in launch.audit["read_paths"]
    assert str(skill_target.resolve()) not in launch.audit["write_paths"]


def test_codex_sandbox_allows_user_skill_roots_and_symlink_targets_read_only(
    tmp_path, monkeypatch
):
    home = tmp_path / "home"
    codex_home = home / ".codex"
    skill_target = tmp_path / "shared-skills" / "storage-test"
    skill_target.mkdir(parents=True)
    (skill_target / "SKILL.md").write_text("storage test skill", encoding="utf-8")
    skill_link = home / ".agents" / "skills" / "storage-test"
    skill_link.parent.mkdir(parents=True)
    skill_link.symlink_to(skill_target, target_is_directory=True)
    (codex_home / "skills").mkdir(parents=True)
    (codex_home / "sessions").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("CODEX_HOME", str(codex_home))

    launch = prepare_agent_sandbox(
        runtime={
            "sandbox_mode": "auto",
            "sandbox_command": "/usr/local/bin/codex",
        },
        cwd=str(tmp_path / "repo"),
        artifact_dir=tmp_path / "artifacts",
        platform_name="darwin",
        which=lambda command: "/usr/bin/sandbox-exec" if command == "sandbox-exec" else None,
    )

    assert str((home / ".agents" / "skills").resolve()) in launch.audit["read_paths"]
    assert str((codex_home / "skills").resolve()) in launch.audit["read_paths"]
    assert str(skill_target.resolve()) in launch.audit["read_paths"]
    assert str(skill_target.resolve()) not in launch.audit["write_paths"]
    assert str(codex_home.resolve()) not in launch.audit["write_paths"]
    assert str((codex_home / "skills").resolve()) not in launch.audit["write_paths"]
    assert str((codex_home / "sessions").resolve()) in launch.audit["write_paths"]


def test_codex_sandbox_uses_isolated_runtime_home_without_writing_real_home(
    tmp_path, monkeypatch
):
    real_home = tmp_path / "real-codex-home"
    runtime_home = tmp_path / "task" / ".runtime-codex-home"
    real_home.mkdir()
    runtime_home.mkdir(parents=True)
    auth_file = real_home / "auth.json"
    auth_file.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("CODEX_HOME", str(real_home))

    launch = prepare_agent_sandbox(
        runtime={
            "sandbox_mode": "required",
            "sandbox_command": "/usr/local/bin/codex",
            "sandbox_codex_home": str(runtime_home),
            "sandbox_read_paths": [str(auth_file)],
        },
        cwd=str(tmp_path / "repo"),
        artifact_dir=tmp_path / "task",
        platform_name="darwin",
        which=lambda command: "/usr/bin/sandbox-exec" if command == "sandbox-exec" else None,
    )

    assert str(runtime_home.resolve()) in launch.audit["read_paths"]
    assert str(real_home.resolve()) not in launch.audit["write_paths"]
    assert not any(
        path.startswith(str(real_home.resolve()))
        for path in launch.audit["runtime_state_paths"]
    )


def test_codex_sandbox_rejects_symlinked_runtime_state_directory(tmp_path):
    runtime_home = tmp_path / "task" / ".runtime-codex-home-safe"
    runtime_home.mkdir(parents=True)
    host_target = tmp_path / "host-state"
    host_target.mkdir()
    (runtime_home / "sessions").symlink_to(host_target, target_is_directory=True)

    with pytest.raises(AgentSandboxError, match="符号链接"):
        prepare_agent_sandbox(
            runtime={
                "sandbox_mode": "required",
                "sandbox_command": "/usr/local/bin/codex",
                "sandbox_codex_home": str(runtime_home),
            },
            cwd=str(tmp_path / "repo"),
            artifact_dir=tmp_path / "task",
            platform_name="darwin",
            which=lambda command: (
                "/usr/bin/sandbox-exec" if command == "sandbox-exec" else None
            ),
        )
