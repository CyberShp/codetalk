from __future__ import annotations

from pathlib import Path


def test_windows_backend_locks_and_unlocks_one_durable_byte(
    tmp_path: Path,
    monkeypatch,
) -> None:
    import app.services.interprocess_file_lock as lock_module

    calls: list[tuple[int, int]] = []

    class FakeMsvcrt:
        LK_NBLCK = 1
        LK_UNLCK = 2

        @staticmethod
        def locking(_fd: int, mode: int, size: int) -> None:
            calls.append((mode, size))

    monkeypatch.setattr(lock_module, "_fcntl", None)
    monkeypatch.setattr(lock_module, "_msvcrt", FakeMsvcrt)

    with lock_module.exclusive_file_lock(tmp_path / "attempt.lock"):
        assert (tmp_path / "attempt.lock").read_bytes() == b"\0"

    assert calls == [(FakeMsvcrt.LK_NBLCK, 1), (FakeMsvcrt.LK_UNLCK, 1)]


def test_missing_platform_lock_backend_fails_closed(tmp_path: Path, monkeypatch) -> None:
    import app.services.interprocess_file_lock as lock_module

    monkeypatch.setattr(lock_module, "_fcntl", None)
    monkeypatch.setattr(lock_module, "_msvcrt", None)

    try:
        with lock_module.exclusive_file_lock(tmp_path / "attempt.lock"):
            raise AssertionError("lock body must not run")
    except lock_module.InterprocessFileLockUnavailable:
        pass
    else:
        raise AssertionError("missing platform lock backend must fail closed")


def test_attempt_state_modules_share_the_cross_platform_process_lock() -> None:
    services = Path(__file__).parents[1] / "app" / "services"
    for name in (
        "node_checkpoint.py",
        "child_session.py",
        "workbench_task_run_events.py",
    ):
        source = (services / name).read_text(encoding="utf-8")
        assert "from app.services.interprocess_file_lock import exclusive_file_lock" in source
        assert "import fcntl" not in source
