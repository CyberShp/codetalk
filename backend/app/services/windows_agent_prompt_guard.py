"""Keep Agent prompts out of Windows batch-shim command lines.

Windows npm installations commonly expose tools such as OpenCode through a
``.cmd`` shim.  Batch shims re-parse ``%*`` using ``cmd.exe``; Markdown prompts
containing characters such as ``&``, ``|`` or parentheses can therefore fail
with ``The syntax of the command is incorrect`` before the provider starts.

``stream_agent_runtime`` already writes every prompt to a temporary UTF-8 file
and exposes its path through ``CODETALK_AGENT_PROMPT_FILE``.  This temporary
qualification guard makes Windows argv-based providers receive only a short,
cmd-safe instruction that tells the Agent to read that file.  Stdin transports
are unaffected because they do not consume the computed prompt argument.
"""

from __future__ import annotations

import logging
import os
from types import ModuleType
from typing import Any

logger = logging.getLogger(__name__)

WINDOWS_PROMPT_FILE_BOOTSTRAP = (
    "Read the full UTF-8 task from the file path stored in "
    "CODETALK_AGENT_PROMPT_FILE and execute it as the only user request."
)


def install_windows_agent_prompt_guard(
    bridge_module: ModuleType,
    *,
    platform_name: str | None = None,
) -> bool:
    """Force Windows argv transports to use the existing prompt file.

    The installation is idempotent.  On non-Windows hosts it is a no-op.  If
    prompt-file creation failed, the original helper retains its existing
    fail-closed behavior for oversized prompts and direct behavior for short
    prompts.
    """

    if (platform_name or os.name) != "nt":
        return False
    if getattr(bridge_module, "_windows_agent_prompt_guard_installed", False):
        return True

    original = bridge_module._prompt_argument_or_file_bootstrap

    def guarded_prompt_argument(
        prompt: str,
        *,
        prompt_file_path: str | None,
    ) -> str:
        if prompt_file_path:
            return WINDOWS_PROMPT_FILE_BOOTSTRAP
        return original(prompt, prompt_file_path=prompt_file_path)

    bridge_module._prompt_argument_or_file_bootstrap = guarded_prompt_argument
    bridge_module._windows_agent_prompt_guard_installed = True
    bridge_module._windows_agent_prompt_guard_original = original
    logger.warning(
        "Installed Windows Agent prompt-file guard for argv-based CLI providers"
    )
    return True
