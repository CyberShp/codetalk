from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
path = ROOT / "frontend/src/app/settings/page.tsx"
text = path.read_text(encoding="utf-8")

anchor = '''const MANAGED_AGENT_TRANSPORTS = new Set<AgentRuntimeCreate["prompt_transport"]>([\n  "claude_print_arg",\n  "codex_exec_json",\n  "opencode_run_arg",\n]);\n'''
addition = anchor + '''\nfunction userFacingLlmTestResult(message: string): string {\n  return String(message || "");\n}\n'''

if "function userFacingLlmTestResult(" in text:
    raise RuntimeError("userFacingLlmTestResult already exists")
if text.count(anchor) != 1:
    raise RuntimeError(f"settings anchor matches={text.count(anchor)}")

text = text.replace(anchor, addition, 1)
if text.count("userFacingLlmTestResult(") != 3:
    raise RuntimeError(
        f"expected definition plus two calls, got {text.count('userFacingLlmTestResult(')}"
    )
path.write_text(text, encoding="utf-8")
Path(__file__).unlink(missing_ok=True)
print("restored userFacingLlmTestResult")
