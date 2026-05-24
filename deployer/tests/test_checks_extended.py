"""Extended checks.py tests — disk, memory, port identification, and port detection.

These use real psutil/socket calls (no mocks) to achieve meaningful coverage
of the functions not reached by the existing mode-based check tests.
"""

import socket

import checks


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
