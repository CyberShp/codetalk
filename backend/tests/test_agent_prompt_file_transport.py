from app.services.agent_cli_bridge import _prompt_argument_or_file_bootstrap


def test_force_file_transport_uses_short_bootstrap(tmp_path):
    prompt_file = tmp_path / "prompt.md"
    prompt_file.write_text("x" * 100, encoding="utf-8")
    result = _prompt_argument_or_file_bootstrap(
        "x" * 100,
        prompt_file_path=str(prompt_file),
        force_file=True,
    )
    assert "CODETALK_AGENT_PROMPT_FILE" in result
    assert len(result) < 500
