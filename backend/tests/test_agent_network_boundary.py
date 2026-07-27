import socket
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from app.services.agent_cli_bridge import AgentRuntimeError, probe_agent_runtime, stream_agent_runtime
from app.services.agent_run_harness import AgentRunHarness
from app.services.agent_sandbox import prepare_agent_sandbox
from app.services.external_agent_discovery import (
    _agent_process_env_with_network_context,
    _sandbox_external_agent_argv,
)
from app.services.network_policy import resolve_agent_network_context


def _configure_intranet_without_boundary(monkeypatch) -> None:
    from app.services import network_policy

    monkeypatch.setattr(network_policy.settings, "network_policy_v2_enabled", True)
    monkeypatch.setattr(network_policy.settings, "network_mode", "intranet")
    monkeypatch.setattr(network_policy.settings, "egress_boundary", "none")
    monkeypatch.setattr(network_policy.settings, "intranet_agent_egress_enforced_by_host", False)


@pytest.mark.asyncio
async def test_probe_and_stream_share_intranet_network_block_and_snapshot(monkeypatch, tmp_path):
    _configure_intranet_without_boundary(monkeypatch)
    marker = tmp_path / "started"
    runtime = {
        "command": sys.executable,
        "args": ["-c", f"from pathlib import Path; Path({str(marker)!r}).write_text('started')"],
        "requires_network": True,
    }

    probe = await probe_agent_runtime(runtime)

    assert probe["success"] is False
    assert "网络策略拒绝" in probe["message"]
    assert "管理员" in probe["message"]
    assert probe["network_policy"]["allowed"] is False
    assert marker.exists() is False

    with pytest.raises(AgentRuntimeError, match="网络策略拒绝"):
        async for _ in stream_agent_runtime(runtime=runtime, prompt="offline", cwd=str(tmp_path)):
            pass

    assert marker.exists() is False


@pytest.mark.asyncio
async def test_probe_process_failure_still_returns_network_snapshot(monkeypatch):
    from app.services import network_policy

    monkeypatch.setattr(network_policy.settings, "network_policy_v2_enabled", True)
    monkeypatch.setattr(network_policy.settings, "network_mode", "developer")
    runtime = {
        "command": sys.executable,
        "args": ["-c", "raise SystemExit(7)"],
        "requires_network": True,
        "sandbox_mode": "off",
    }

    probe = await probe_agent_runtime(runtime)

    assert probe["success"] is False
    assert probe["network_policy"]["mode"] == "developer"
    assert probe["network_policy"]["allowed"] is True


def test_harness_records_credential_free_network_snapshot_before_blocking(monkeypatch, tmp_path):
    _configure_intranet_without_boundary(monkeypatch)
    marker = tmp_path / "harness-started"
    harness = AgentRunHarness(tmp_path / "artifacts")
    harness.create_run(
        run_id="network-blocked",
        provider="custom",
        command=[sys.executable, "-c", f"from pathlib import Path; Path({str(marker)!r}).write_text('started')"],
        cwd=str(tmp_path),
        workflow_snapshot={},
        task_bundle={},
        requires_network=True,
    )

    with pytest.raises(RuntimeError, match="网络策略拒绝"):
        harness.execute_run("network-blocked")

    snapshot = (tmp_path / "artifacts" / "network_policy.json").read_text(encoding="utf-8")
    assert '"allowed": false' in snapshot
    assert "http" not in snapshot
    assert marker.exists() is False


def test_external_discovery_uses_the_same_sanitized_deployment_boundary(monkeypatch, tmp_path):
    from app.services import network_policy

    monkeypatch.setattr(network_policy.settings, "network_policy_v2_enabled", True)
    monkeypatch.setattr(network_policy.settings, "network_mode", "intranet")
    monkeypatch.setattr(network_policy.settings, "egress_boundary", "deployment_egress_policy")
    monkeypatch.setattr(network_policy.settings, "deployment_egress_policy_id", "egress-prod-1")
    monkeypatch.setenv("HTTPS_PROXY", "https://unknown:secret@unapproved.example")
    monkeypatch.setenv("SSL_CERT_DIR", "/tmp/unapproved-ca-dir")
    artifact_dir = tmp_path / "artifacts"

    env, network_context = _agent_process_env_with_network_context(
        "opencode", tmp_path, artifact_dir=artifact_dir
    )
    _, audit = _sandbox_external_agent_argv(
        [sys.executable, "-c", "print('ok')"],
        env=env,
        network_context=network_context,
        cwd=tmp_path,
    )

    assert "HTTPS_PROXY" not in env
    assert "SSL_CERT_DIR" not in env
    assert audit["network"] == "outbound_allowed"
    assert audit["network_policy"]["mode"] == "intranet"
    assert audit["network_policy"]["boundary"] == "deployment_egress_policy"
    assert audit["network_policy"]["deployment_egress_policy_id"] == "egress-prod-1"


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS Seatbelt enforcement test")
@pytest.mark.parametrize("mode", ["intranet", "strict_compliance"])
def test_macos_proxy_boundary_allows_only_approved_gateway_port(monkeypatch, tmp_path, mode):
    from app.services import agent_sandbox, network_policy

    if not agent_sandbox.shutil.which("sandbox-exec"):
        pytest.skip("sandbox-exec unavailable")

    allowed = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
    denied = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
    allowed.bind(("::1", 0))
    denied.bind(("::1", 0))
    allowed.listen(1)
    denied.listen(1)
    allowed_port = allowed.getsockname()[1]
    denied_port = denied.getsockname()[1]
    accepted: list[socket.socket] = []

    def accept_once() -> None:
        try:
            connection, _ = allowed.accept()
        except OSError:
            return
        accepted.append(connection)

    thread = threading.Thread(target=accept_once, daemon=True)
    thread.start()
    monkeypatch.setattr(network_policy.settings, "network_policy_v2_enabled", True)
    monkeypatch.setattr(network_policy.settings, "network_mode", mode)
    monkeypatch.setattr(network_policy.settings, "egress_boundary", "approved_proxy_gateway")
    monkeypatch.setattr(network_policy.settings, "approved_proxy_url", f"http://localhost:{allowed_port}")
    monkeypatch.setattr(network_policy.settings, "approved_proxy_config_id", "local-test-gateway")
    monkeypatch.setattr(network_policy.settings, "external_agent_sandbox_mode", "auto")
    monkeypatch.setattr(
        network_policy.settings,
        "strict_compliance_os_network_isolation_enabled",
        mode == "strict_compliance",
    )
    context = resolve_agent_network_context(requires_network=True, environment={})
    assert context.allowed is True

    try:
        launch = prepare_agent_sandbox(
            runtime={
                "sandbox_mode": "required",
                "network_context": context,
                "sandbox_command": sys.executable,
                "sandbox_read_paths": [str(Path(sys.executable).parent.parent)],
            },
            cwd=str(tmp_path),
            artifact_dir=tmp_path / "artifacts",
        )
        profile_path = Path(launch.wrapper[2]).resolve()
        assert (tmp_path / "artifacts").resolve() not in profile_path.parents
        assert not (tmp_path / "artifacts" / "sandbox-profile.sb").exists()
        script = (
            "import socket\n"
            f"for name, port in [('allowed', {allowed_port}), ('denied', {denied_port})]:\n"
            "    try:\n"
            "        with socket.create_connection(('localhost', port), timeout=1): pass\n"
            "    except OSError:\n"
            "        print(name + '-blocked')\n"
            "    else:\n"
            "        print(name + '-connected')\n"
        )
        completed = subprocess.run(
            [*launch.wrapper, sys.executable, "-c", script],
            cwd=tmp_path,
            text=True,
            capture_output=True,
            timeout=10,
            check=False,
        )
    finally:
        for connection in accepted:
            connection.close()
        allowed.close()
        denied.close()

    assert completed.returncode == 0, completed.stderr
    assert "allowed-connected" in completed.stdout
    assert "denied-blocked" in completed.stdout


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS Seatbelt enforcement test")
def test_external_discovery_process_cannot_bypass_approved_proxy_gateway(monkeypatch, tmp_path):
    from app.services import agent_sandbox, network_policy

    if not agent_sandbox.shutil.which("sandbox-exec"):
        pytest.skip("sandbox-exec unavailable")

    allowed = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
    denied = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
    allowed.bind(("::1", 0))
    denied.bind(("::1", 0))
    allowed.listen(1)
    denied.listen(1)
    allowed_port = allowed.getsockname()[1]
    denied_port = denied.getsockname()[1]
    accepted: list[socket.socket] = []

    def accept_once() -> None:
        try:
            connection, _ = allowed.accept()
        except OSError:
            return
        accepted.append(connection)

    thread = threading.Thread(target=accept_once, daemon=True)
    thread.start()
    monkeypatch.setattr(network_policy.settings, "network_policy_v2_enabled", True)
    monkeypatch.setattr(network_policy.settings, "network_mode", "intranet")
    monkeypatch.setattr(network_policy.settings, "egress_boundary", "approved_proxy_gateway")
    monkeypatch.setattr(network_policy.settings, "approved_proxy_url", f"http://localhost:{allowed_port}")
    monkeypatch.setattr(network_policy.settings, "approved_proxy_config_id", "local-test-gateway")
    monkeypatch.setattr(network_policy.settings, "external_agent_sandbox_mode", "auto")
    artifact_dir = tmp_path / "discovery-artifacts"
    script = (
        "import socket\n"
        f"for name, port in [('allowed', {allowed_port}), ('denied', {denied_port})]:\n"
        "    try:\n"
        "        with socket.create_connection(('localhost', port), timeout=1): pass\n"
        "    except OSError:\n"
        "        print(name + '-blocked')\n"
        "    else:\n"
        "        print(name + '-connected')\n"
    )

    env, network_context = _agent_process_env_with_network_context(
        "opencode", tmp_path, artifact_dir=artifact_dir
    )
    process_argv, audit = _sandbox_external_agent_argv(
        [sys.executable, "-c", script],
        env=env,
        network_context=network_context,
        cwd=tmp_path,
    )
    try:
        completed = subprocess.run(
            process_argv,
            cwd=tmp_path,
            env=env,
            text=True,
            capture_output=True,
            timeout=10,
            check=False,
        )
    finally:
        for connection in accepted:
            connection.close()
        allowed.close()
        denied.close()

    assert completed.returncode == 0, completed.stderr
    assert "allowed-connected" in completed.stdout
    assert "denied-blocked" in completed.stdout
    assert audit["network_policy"]["boundary"] == "approved_proxy_gateway"
    assert not (artifact_dir / "sandbox-profile.sb").exists()


@pytest.mark.asyncio
@pytest.mark.skipif(sys.platform != "darwin", reason="macOS Seatbelt enforcement test")
@pytest.mark.parametrize("mode", ["intranet", "strict_compliance"])
async def test_macos_probe_and_stream_use_same_proxy_boundary_wrapper(monkeypatch, tmp_path, mode):
    from app.services import agent_sandbox, network_policy

    if not agent_sandbox.shutil.which("sandbox-exec"):
        pytest.skip("sandbox-exec unavailable")

    allowed = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
    denied = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
    allowed.bind(("::1", 0))
    denied.bind(("::1", 0))
    allowed.listen(8)
    denied.listen(8)
    allowed_port = allowed.getsockname()[1]
    denied_port = denied.getsockname()[1]
    stop = threading.Event()

    def drain_allowed_connections() -> None:
        allowed.settimeout(0.1)
        while not stop.is_set():
            try:
                connection, _ = allowed.accept()
            except TimeoutError:
                continue
            except OSError:
                return
            connection.close()

    thread = threading.Thread(target=drain_allowed_connections, daemon=True)
    thread.start()
    monkeypatch.setattr(network_policy.settings, "network_policy_v2_enabled", True)
    monkeypatch.setattr(network_policy.settings, "network_mode", mode)
    monkeypatch.setattr(network_policy.settings, "egress_boundary", "approved_proxy_gateway")
    monkeypatch.setattr(network_policy.settings, "approved_proxy_url", f"http://localhost:{allowed_port}")
    monkeypatch.setattr(network_policy.settings, "approved_proxy_config_id", "local-test-gateway")
    monkeypatch.setattr(
        network_policy.settings,
        "strict_compliance_os_network_isolation_enabled",
        mode == "strict_compliance",
    )
    script = (
        "import socket\n"
        f"for name, port in [('allowed', {allowed_port}), ('denied', {denied_port})]:\n"
        "    try:\n"
        "        with socket.create_connection(('localhost', port), timeout=1): pass\n"
        "    except OSError:\n"
        "        print(name + '-blocked', flush=True)\n"
        "    else:\n"
        "        print(name + '-connected', flush=True)\n"
    )
    runtime = {
        "command": sys.executable,
        "args": ["-c", script],
        "requires_network": True,
        "sandbox_mode": "required",
        "prompt_transport": "stdin",
        "output_mode": "plain",
    }
    try:
        probe = await probe_agent_runtime(runtime)
        streamed: list[str] = []
        async for chunk in stream_agent_runtime(runtime=runtime, prompt="network check", cwd=str(tmp_path)):
            streamed.append(chunk)
    finally:
        stop.set()
        allowed.close()
        denied.close()
        thread.join(timeout=1)

    assert probe["success"] is True, probe
    assert "allowed-connected" in probe["message"]
    assert "denied-blocked" in probe["message"]
    assert "allowed-connected" in "".join(streamed)
    assert "denied-blocked" in "".join(streamed)
