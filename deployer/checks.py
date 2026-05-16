"""Prerequisite checks for Docker Compose, Kubernetes, and Native deployment modes."""

import asyncio
import re
import shutil
import socket
import subprocess
import sys
from typing import Optional

import psutil


def _make_result(
    name: str,
    status: str,
    message: str,
    fix: Optional[str] = None,
) -> dict:
    return {"name": name, "status": status, "message": message, "fix": fix}


async def _run_cmd(*args: str) -> tuple[int, str, str]:
    """Run a command asynchronously and return (returncode, stdout, stderr)."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=15)
        return proc.returncode, stdout.decode().strip(), stderr.decode().strip()
    except asyncio.TimeoutError:
        return 1, "", "Command timed out"
    except FileNotFoundError:
        return 1, "", f"Command not found: {args[0]}"


def _check_port_free(port: int) -> bool:
    """Return True if the port can be bound (i.e. is not in use)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("0.0.0.0", port))
            return True
        except OSError:
            return False


async def _check_docker() -> dict:
    code, _, stderr = await _run_cmd("docker", "info")
    if code == 0:
        return _make_result("Docker Engine", "pass", "Docker daemon is running")
    return _make_result(
        "Docker Engine",
        "fail",
        f"Docker daemon is not running: {stderr}",
        fix="Start Docker Desktop or run 'sudo systemctl start docker'",
    )


async def _check_docker_compose() -> dict:
    code, out, _ = await _run_cmd("docker", "compose", "version")
    if code == 0:
        version = out.splitlines()[0] if out else "unknown"
        return _make_result("Docker Compose", "pass", version)
    return _make_result(
        "Docker Compose",
        "fail",
        "docker compose plugin not found",
        fix="Update Docker Desktop or install the compose plugin: https://docs.docker.com/compose/install/",
    )


async def _check_kubectl() -> dict:
    code, out, _ = await _run_cmd("kubectl", "version", "--client", "--short")
    if code == 0:
        version = out.splitlines()[0] if out else "unknown"
        return _make_result("kubectl", "pass", version)
    return _make_result(
        "kubectl",
        "fail",
        "kubectl not found",
        fix="Install kubectl: https://kubernetes.io/docs/tasks/tools/",
    )


async def _check_helm() -> dict:
    code, out, _ = await _run_cmd("helm", "version", "--short")
    if code == 0:
        return _make_result("Helm", "pass", out.splitlines()[0] if out else "found")
    return _make_result(
        "Helm",
        "fail",
        "helm not found",
        fix="Install Helm: https://helm.sh/docs/intro/install/",
    )


async def _check_k8s_cluster() -> dict:
    # Check for kind first
    code_kind, _, _ = await _run_cmd("kind", "get", "clusters")
    if code_kind == 0:
        return _make_result("Kubernetes Cluster", "pass", "kind cluster available")

    # Fall back to kubectl cluster-info
    code, out, _ = await _run_cmd("kubectl", "cluster-info")
    if code == 0:
        return _make_result("Kubernetes Cluster", "pass", "Cluster reachable via kubectl")

    return _make_result(
        "Kubernetes Cluster",
        "fail",
        "No Kubernetes cluster found (tried kind and kubectl cluster-info)",
        fix="Install kind: https://kind.sigs.k8s.io/docs/user/quick-start/ or configure kubectl to point to an existing cluster",
    )


async def _check_python() -> dict:
    code, out, _ = await _run_cmd(sys.executable, "--version")
    if code == 0 and out:
        m = re.search(r"(\d+)\.(\d+)", out)
        if m and (int(m.group(1)), int(m.group(2))) >= (3, 10):
            return _make_result("Python 3.10+", "pass", out.strip())
        return _make_result(
            "Python 3.10+",
            "fail",
            f"Found {out.strip()} but 3.10+ is required",
            fix="Install Python 3.10 or newer: https://www.python.org/downloads/",
        )
    return _make_result(
        "Python 3.10+",
        "fail",
        "Python not found on PATH",
        fix="Install Python 3.10 or newer and ensure it is on PATH",
    )


async def _check_node() -> dict:
    code, out, _ = await _run_cmd("node", "--version")
    if code == 0 and out:
        m = re.search(r"(\d+)", out)
        if m and int(m.group(1)) >= 18:
            return _make_result("Node.js 18+", "pass", out.strip())
        return _make_result(
            "Node.js 18+",
            "fail",
            f"Found {out.strip()} but 18+ is required",
            fix="Install Node.js 18 or newer: https://nodejs.org/",
        )
    return _make_result(
        "Node.js 18+",
        "fail",
        "Node.js not found on PATH",
        fix="Install Node.js 18 or newer and ensure it is on PATH",
    )


async def _check_git() -> dict:
    code, out, _ = await _run_cmd("git", "--version")
    if code == 0:
        return _make_result("Git", "pass", out.strip() if out else "found")
    return _make_result(
        "Git",
        "fail",
        "Git not found on PATH",
        fix="Install Git: https://git-scm.com/downloads",
    )


def _check_ports(ports: list[int] | None = None, mode: str = "compose") -> list[dict]:
    if ports is None:
        ports = [5433, 8000, 3005, 8001, 7100, 8080, 16251, 6070]
    hint = (
        "Stop the process using the port or change the port in the deployer config"
        if mode == "native"
        else "Stop the process using the port or change the port mapping in docker-compose.yml"
    )
    results = []
    for port in ports:
        if _check_port_free(port):
            results.append(
                _make_result(f"Port {port}", "pass", f"Port {port} is available")
            )
        else:
            results.append(
                _make_result(
                    f"Port {port}",
                    "fail",
                    f"Port {port} is already in use",
                    fix=f"{hint} (port {port})",
                )
            )
    return results


def _check_disk() -> dict:
    disk = psutil.disk_usage("/")
    free_gb = disk.free / (1024 ** 3)
    if free_gb >= 20:
        return _make_result(
            "Disk Space",
            "pass",
            f"{free_gb:.1f} GB free (minimum 20 GB required)",
        )
    return _make_result(
        "Disk Space",
        "fail",
        f"Only {free_gb:.1f} GB free — 20 GB required",
        fix="Free up disk space before deploying",
    )


def _check_memory() -> dict:
    mem = psutil.virtual_memory()
    total_gb = mem.total / (1024 ** 3)
    if total_gb >= 16:
        return _make_result(
            "Memory",
            "pass",
            f"{total_gb:.1f} GB total RAM",
        )
    if total_gb >= 8:
        return _make_result(
            "Memory",
            "warn",
            f"{total_gb:.1f} GB total RAM — 16 GB recommended for best performance",
            fix="Consider upgrading to 16 GB RAM for a smooth experience",
        )
    return _make_result(
        "Memory",
        "fail",
        f"Only {total_gb:.1f} GB total RAM — minimum 8 GB required",
        fix="Upgrade to at least 8 GB RAM",
    )


async def run_checks(mode: str) -> list[dict]:
    """Run prerequisite checks for the given deployment mode.

    Args:
        mode: "compose", "k8s", or "native"

    Returns:
        List of check result dicts with keys: name, status, message, fix.
    """
    results: list[dict] = []

    if mode == "native":
        python_result, node_result, git_result = await asyncio.gather(
            _check_python(),
            _check_node(),
            _check_git(),
        )
        results.extend([python_result, node_result, git_result])
        from config_store import load_config

        saved = load_config()
        native_ports = [
            int(saved.get("frontend_port", 3005)),
            int(saved.get("backend_port", 8100)),
            int(saved.get("gitnexus_port", 7100)),
        ]
        results.extend(_check_ports(ports=native_ports, mode="native"))
    elif mode == "k8s":
        docker_result = await _check_docker()
        results.append(docker_result)
        kubectl_result, helm_result, cluster_result = await asyncio.gather(
            _check_kubectl(),
            _check_helm(),
            _check_k8s_cluster(),
        )
        results.extend([kubectl_result, helm_result, cluster_result])
        results.extend(_check_ports())
    else:
        docker_result, compose_result = await asyncio.gather(
            _check_docker(),
            _check_docker_compose(),
        )
        results.extend([docker_result, compose_result])
        results.extend(_check_ports())

    results.append(_check_disk())
    results.append(_check_memory())

    return results
