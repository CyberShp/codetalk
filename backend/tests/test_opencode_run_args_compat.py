from app.services.agent_cli_bridge import _opencode_run_args


def test_opencode_run_args_use_current_permission_flag():
    args = _opencode_run_args([], "hello")

    assert args == [
        "run",
        "--dangerously-skip-permissions",
        "--format",
        "json",
        "hello",
    ]
    assert "--auto" not in args


def test_opencode_run_args_remove_legacy_auto_flag():
    args = _opencode_run_args(["run", "--auto"], "hello")

    assert "--auto" not in args
    assert "--dangerously-skip-permissions" in args
    assert args.count("--dangerously-skip-permissions") == 1


def test_opencode_run_args_preserve_existing_current_flag():
    args = _opencode_run_args(
        ["run", "--dangerously-skip-permissions", "--format", "json"],
        "hello",
    )

    assert args.count("--dangerously-skip-permissions") == 1
    assert "--auto" not in args
