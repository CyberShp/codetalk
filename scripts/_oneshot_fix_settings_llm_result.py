from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
path = ROOT / "frontend/src/app/settings/page.tsx"
text = path.read_text(encoding="utf-8")

anchor = '''const MANAGED_AGENT_TRANSPORTS = new Set<AgentRuntimeCreate["prompt_transport"]>([\n  "claude_print_arg",\n  "codex_exec_json",\n  "opencode_run_arg",\n]);\n'''
addition = anchor + '''\nfunction userFacingLlmTestResult(message: string): string {\n  return String(message || "");\n}\n'''
legacy_policy_notice = '''            {deploymentNetworkPolicy?.mode === "intranet" && (\n              <p className="rounded-lg border border-amber-500/20 bg-amber-500/10 px-3 py-2 text-xs leading-5 text-on-surface">\n                当前运行在公司内网环境。通用模型代理表单只影响模型客户端连接，CodeTalk 不额外配置出站边界。\n              </p>\n            )}\n'''

if "function userFacingLlmTestResult(" in text:
    raise RuntimeError("userFacingLlmTestResult already exists")
if text.count(anchor) != 1:
    raise RuntimeError(f"settings anchor matches={text.count(anchor)}")
if text.count(legacy_policy_notice) != 1:
    raise RuntimeError(
        f"legacy deploymentNetworkPolicy notice matches={text.count(legacy_policy_notice)}"
    )

text = text.replace(anchor, addition, 1)
text = text.replace(legacy_policy_notice, "", 1)

if text.count("userFacingLlmTestResult(") != 3:
    raise RuntimeError(
        f"expected definition plus two calls, got {text.count('userFacingLlmTestResult(')}"
    )
if "deploymentNetworkPolicy" in text:
    raise RuntimeError("deploymentNetworkPolicy residue remains in settings page")

path.write_text(text, encoding="utf-8")
Path(__file__).unlink(missing_ok=True)
print("restored LLM result helper and removed stale network-policy notice")
