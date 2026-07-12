from __future__ import annotations

import json

import pytest

from app.services.agent_sandbox import AgentSandboxError, prepare_agent_sandbox


def test_macos_sandbox_wraps_command_and_persists_audit(tmp_path):
    artifact_dir = tmp_path / "artifacts"
    launch = prepare_agent_sandbox(
        runtime={"sandbox_mode": "required", "sandbox_allow_network": True},
        cwd=str(tmp_path / "repo"),
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
    assert "(allow file-read*)" in profile
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

