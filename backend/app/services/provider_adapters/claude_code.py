"""Claude Code CLI provider adapter."""

from app.services.provider_adapters.cli_base import CliProviderAdapter
from app.services.provider_adapters.contracts import ProviderCapabilities


class ClaudeCliAdapter(CliProviderAdapter):
    provider = "claude"
    default_command = "claude"
    prompt_transport = "claude_print_arg"
    output_mode = "stream_json"
    provider_capabilities = ProviderCapabilities(
        streaming=True,
        tool_call=True,
        session_resume=True,
        structured_output=True,
        mcp=True,
        skills=True,
        cancellation=True,
    )


ClaudeCodeAdapter = ClaudeCliAdapter
