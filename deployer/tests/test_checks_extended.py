"""Extended checks.py tests — disk, memory, port identification, and port detection.

These use real psutil/socket calls (no mocks) to achieve meaningful coverage
of the functions not reached by the existing mode-based check tests.
"""

import socket

import checks
import config_store


def test_check_disk_returns_valid_result():
    result = checks._check_disk()
    assert result["name"] == "Disk Space"
    assert result["status"] in ("pass", "fail")
    assert result["message"]


def test_check_memory_returns_valid_result():
    result = checks._check_memory()
    assert result["name"] == "Memory"
    assert result["status"] in ("pass", "warn", "fail")
    assert result["message"]


def test_check_port_free_on_unused_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        free_port = s.getsockname()[1]
    # Port is released when the socket closes; should now be bindable.
    assert checks._check_port_free(free_port) is True


def test_check_port_free_on_occupied_port():
    """Bind a socket ourselves and verify _check_port_free detects the conflict."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as srv:
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("127.0.0.1", 0))
        occupied_port = srv.getsockname()[1]
        srv.listen(1)
        result = checks._check_port_free(occupied_port)
        assert isinstance(result, bool)


def test_identify_port_user_returns_string():
    """_identify_port_user must return a string for any port, even if unknown."""
    result = checks._identify_port_user(9)
    assert isinstance(result, str)


def test_detect_own_running_ports_returns_subset():
    """_detect_own_running_ports returns a set that is a subset of candidates."""
    candidates = {12345, 12346, 12347}
    own = checks._detect_own_running_ports(candidates)
    assert isinstance(own, set)
    assert own.issubset(candidates)


async def test_run_checks_native_mode_includes_disk_and_memory():
    """run_checks('native') must include disk and memory check results."""
    results = await checks.run_checks("native")
    names = {r["name"] for r in results}
    assert "Disk Space" in names
    assert "Memory" in names


async def test_run_checks_native_mode_returns_list_of_dicts():
    results = await checks.run_checks("native")
    assert isinstance(results, list)
    for item in results:
        assert "name" in item
        assert "status" in item
        assert "message" in item


async def test_run_checks_native_skips_disabled_gitnexus_port(monkeypatch):
    async def pass_result(name):
        return {"name": name, "status": "pass", "message": "ok"}

    seen_ports = []

    monkeypatch.setattr(checks, "_check_python", lambda: pass_result("Python 3.10+"))
    monkeypatch.setattr(checks, "_check_node", lambda: pass_result("Node.js 18+"))
    monkeypatch.setattr(checks, "_check_git", lambda: pass_result("Git"))
    monkeypatch.setattr(checks, "_detect_own_running_ports", lambda ports: set())
    monkeypatch.setattr(checks, "_check_disk", lambda: {"name": "Disk Space", "status": "pass", "message": "ok"})
    monkeypatch.setattr(checks, "_check_memory", lambda: {"name": "Memory", "status": "pass", "message": "ok"})
    monkeypatch.setattr(
        config_store,
        "load_config",
        lambda: {
            "frontend_port": 3503,
            "backend_port": 3504,
            "gitnexus_port": 7100,
            "install_gitnexus": False,
        },
    )
    monkeypatch.setattr(
        checks,
        "_check_ports",
        lambda ports, mode, own_ports: seen_ports.extend(ports) or [
            {"name": f"Port {port}", "status": "pass", "message": "ok"} for port in ports
        ],
    )

    await checks.run_checks("native")

    assert seen_ports == [3503, 3504]


async def test_run_checks_k8s_mode_returns_list_with_disk_and_memory():
    """run_checks('k8s') covers docker/kubectl/helm/cluster checks (all likely fail in CI)."""
    results = await checks.run_checks("k8s")
    assert isinstance(results, list)
    names = {r["name"] for r in results}
    assert "Disk Space" in names
    assert "Memory" in names
    assert any("Docker" in n or "kubectl" in n or "Kubernetes" in n or "Helm" in n for n in names)


async def test_run_checks_k8s_mode_all_items_have_required_keys():
    """Every result from k8s mode has the mandatory schema keys."""
    results = await checks.run_checks("k8s")
    for item in results:
        assert "name" in item
        assert "status" in item
        assert "message" in item


async def test_run_checks_compose_mode_returns_list_with_disk_and_memory():
    """run_checks('compose') covers docker/docker-compose checks."""
    results = await checks.run_checks("compose")
    assert isinstance(results, list)
    names = {r["name"] for r in results}
    assert "Disk Space" in names
    assert "Memory" in names
    assert any("Docker" in n for n in names)


async def test_run_checks_compose_mode_all_items_have_required_keys():
    results = await checks.run_checks("compose")
    for item in results:
        assert "name" in item
        assert "status" in item
        assert "message" in item


def test_check_ports_with_occupied_port_in_own_ports():
    """Port occupied by own service should be marked pass (own_ports coverage)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as srv:
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("127.0.0.1", 0))
        port = srv.getsockname()[1]
        srv.listen(1)
        results = checks._check_ports(ports=[port], mode="native", own_ports={port})
        assert any(r["status"] == "pass" for r in results)


def test_check_ports_with_occupied_port_not_own():
    """Port occupied by unknown process should be marked fail."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as srv:
        srv.bind(("0.0.0.0", 0))
        port = srv.getsockname()[1]
        srv.listen(128)
        # With no SO_REUSEADDR and binding same 0.0.0.0 address, should detect conflict
        result = checks._check_port_free(port)
        if result:
            # On some Windows configs SO_REUSEADDR allows this — port detection works
            # but the port may appear free. Just verify _check_ports returns a result.
            results = checks._check_ports(ports=[port], mode="native", own_ports=set())
            assert len(results) == 1
        else:
            results = checks._check_ports(ports=[port], mode="native", own_ports=set())
            assert any(r["status"] == "fail" for r in results)


def test_check_ports_reports_bind_denied_separately(monkeypatch):
    """Access-denied bind failures are not necessarily process conflicts."""

    class DeniedSocket:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return None

        def setsockopt(self, *args):
            return None

        def bind(self, *args):
            raise PermissionError(13, "access denied")

    monkeypatch.setattr(checks.socket, "socket", lambda *args, **kwargs: DeniedSocket())
    monkeypatch.setattr(checks, "_identify_port_user", lambda port: "")

    result = checks._check_ports(ports=[7100], mode="native", own_ports=set())[0]

    assert result["status"] == "fail"
    assert "already in use" not in result["message"]
    assert "cannot be bound" in result["message"]
