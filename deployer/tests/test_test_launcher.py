from pathlib import Path


def test_test_launcher_selects_supported_python_and_uses_deployer_venv():
    script = Path("test.sh").read_text(encoding="utf-8")

    assert "python3.12 python3.11 python3.10 python3 python" in script
    assert "sys.version_info >= (3, 10)" in script
    assert "start._ensure_venv_compatible()" in script
    assert "start._install_dependencies()" in script
    assert 'exec ".venv/bin/python" -m pytest "$@"' in script
