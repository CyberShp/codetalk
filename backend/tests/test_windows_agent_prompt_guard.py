from __future__ import annotations

from types import SimpleNamespace

from app.services.windows_agent_prompt_guard import (
    WINDOWS_PROMPT_FILE_BOOTSTRAP,
    install_windows_agent_prompt_guard,
)


def test_windows_guard_keeps_cmd_metacharacters_out_of_agent_argv() -> None:
    original_calls: list[tuple[str, str | None]] = []

    def original(prompt: str, *, prompt_file_path: str | None) -> str:
        original_calls.append((prompt, prompt_file_path))
        return prompt

    bridge = SimpleNamespace(_prompt_argument_or_file_bootstrap=original)
    installed = install_windows_agent_prompt_guard(bridge, platform_name="nt")

    result = bridge._prompt_argument_or_file_bootstrap(
        "# Step (01)\nread A & B | write <report>",
        prompt_file_path=r"C:\temp\codetalk-agent-prompt.md",
    )

    assert installed is True
    assert result == WINDOWS_PROMPT_FILE_BOOTSTRAP
    assert not any(character in result for character in "&|<>()^%!")
    assert original_calls == []


def test_windows_guard_falls_back_when_prompt_file_creation_failed() -> None:
    def original(prompt: str, *, prompt_file_path: str | None) -> str:
        assert prompt_file_path is None
        return prompt

    bridge = SimpleNamespace(_prompt_argument_or_file_bootstrap=original)
    install_windows_agent_prompt_guard(bridge, platform_name="nt")

    assert bridge._prompt_argument_or_file_bootstrap(
        "small direct prompt",
        prompt_file_path=None,
    ) == "small direct prompt"


def test_prompt_guard_is_noop_off_windows() -> None:
    original = lambda prompt, *, prompt_file_path: prompt
    bridge = SimpleNamespace(_prompt_argument_or_file_bootstrap=original)

    assert install_windows_agent_prompt_guard(bridge, platform_name="posix") is False
    assert bridge._prompt_argument_or_file_bootstrap is original
