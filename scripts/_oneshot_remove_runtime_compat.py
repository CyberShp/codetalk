from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def write(path: str, text: str) -> None:
    (ROOT / path).write_text(text, encoding="utf-8")


def exact(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one match, got {count}")
    return text.replace(old, new, 1)


def sub(text: str, pattern: str, replacement: str, label: str, *, count: int = 1) -> str:
    result, matched = re.subn(pattern, replacement, text, count=count, flags=re.S)
    if matched != count:
        raise RuntimeError(f"{label}: expected {count} matches, got {matched}")
    return result


# Local/internal HTTP helpers: use the configured URL directly, with no CodeTalk admission layer.
write(
    "backend/app/utils/local_client.py",
    '''"""Factory for HTTP clients used by local tool services."""\n\nimport httpx\n\n\ndef local_http_client(\n    base_url: str,\n    timeout: float = 30.0,\n    connect_timeout: float = 5.0,\n) -> httpx.AsyncClient:\n    """Return an AsyncClient for the configured tool service."""\n    return httpx.AsyncClient(\n        base_url=base_url,\n        timeout=httpx.Timeout(timeout, connect=connect_timeout),\n        trust_env=False,\n    )\n''',
)

component = read("backend/app/services/component_manager.py")
component = exact(component, "from app.services.runtime_environment import require_runtime_url\n", "", "component import")
component = component.replace("        require_runtime_url(base)\n", "")
component = component.replace("        require_runtime_url(host)\n", "")
write("backend/app/services/component_manager.py", component)

process = read("backend/app/services/process_manager.py")
process = exact(
    process,
    "from app.services.runtime_environment import NetworkEgressBlocked, require_runtime_url\n",
    "",
    "process import",
)
process = process.replace(
    "            # A managed-tool health URL is still an outbound request.  Keep\n"
    "            # the shared health client from becoming an exemption to the\n"
    "            # deployment-owned tool admission policy.\n"
    "            require_runtime_url(health_url)\n",
    "",
)
process = sub(
    process,
    r"        except NetworkEgressBlocked as exc:\n.*?            return \{\*\*mp\.to_dict\(\), \"healthy\": False\}\n",
    "",
    "process policy exception",
)
process = process.replace("                require_runtime_url(fallback_url)\n", "")
write("backend/app/services/process_manager.py", process)

task_engine = read("backend/app/services/task_engine.py")
task_engine = exact(
    task_engine,
    "from app.services.runtime_environment import require_runtime_model_request_url\n",
    "",
    "task engine import",
)
task_engine = sub(
    task_engine,
    r"        # This legacy summary path is still a model-provider call\.  It must use\n"
    r"        # the same deployment-owned egress contract as the V3 model adapters\.\n"
    r"        require_runtime_model_request_url\([^\n]+\)\n",
    "",
    "task engine model approval",
)
write("backend/app/services/task_engine.py", task_engine)

# Remove stale policy wording and the now-unused request URL helper from the LLM factory.
factory = read("backend/app/llm/factory.py")
factory = exact(
    factory,
    "from app.llm.endpoint import normalize_openai_compat_base_url\n",
    "",
    "factory endpoint import",
)
factory = sub(
    factory,
    r"\n\ndef _model_request_url\(.*?(?=\n\ndef _create_runtime_llm_client)",
    "",
    "factory request URL helper",
)
factory = sub(
    factory,
    r"    \"\"\"Create an LLM client after authorizing its actual model request route\.\n\n"
    r"    The settings probe calls this same function, while the provider repeats the\n"
    r"    same narrow check immediately before transport\.  A saved model URL records\n"
    r"    user intent; only this deployment-level policy approves egress\.\n"
    r"    \"\"\"",
    '    """Create an LLM client using the model and General Settings transport."""',
    "factory policy docstring",
)
write("backend/app/llm/factory.py", factory)

# Agent sandbox audit no longer accepts or records a network-policy context.
sandbox = read("backend/app/services/agent_sandbox.py")
sandbox = exact(sandbox, '    network_context = runtime.get("network_context")\n', "", "sandbox context")
sandbox = exact(
    sandbox,
    '        "network_policy": network_context.snapshot() if network_context is not None else None,\n',
    "",
    "sandbox audit policy",
)
write("backend/app/services/agent_sandbox.py", sandbox)

# Workbench harness: use its assembled environment directly and remove policy artifacts/metadata.
harness = read("backend/app/services/agent_run_harness.py")
harness = exact(
    harness,
    "from app.services.runtime_environment import resolve_agent_network_context\n",
    "",
    "harness import",
)
harness = sub(
    harness,
    r"\n\ndef _harness_network_policy_error\(context: Any\) -> str:\n.*?(?=\n\ndef _json_sha256)",
    "",
    "harness policy error",
)
harness = exact(
    harness,
    '''        network_context = resolve_agent_network_context(\n            requires_network=bool(run_payload.get("requires_network", True)),\n            environment=env,\n        )\n        self._write_json("network_policy.json", network_context.snapshot())\n        if not network_context.allowed:\n            raise RuntimeError(_harness_network_policy_error(network_context))\n        env = dict(network_context.sanitized_environment)\n''',
    "",
    "harness policy block",
)
harness = harness.replace('                    "network_context": network_context,\n', "")
harness = sub(
    harness,
    r'                    "intranet_require_os_sandbox": bool\(\n                        network_context\.requires_os_network_isolation\n                    \),\n',
    "",
    "harness sandbox policy field",
)
harness = harness.replace('            execution_input["network_policy"] = network_context.snapshot()\n', "")
harness = harness.replace('                "network_policy": network_context.snapshot(),\n', "")
harness = harness.replace('            "network_policy.json",\n', "")
write("backend/app/services/agent_run_harness.py", harness)

# External discovery: build the environment directly and sandbox only filesystem/process access.
external = read("backend/app/services/external_agent_discovery.py")
external = exact(
    external,
    "from app.services.runtime_environment import AgentNetworkContext, resolve_agent_network_context\n",
    "",
    "external import",
)
external = sub(
    external,
    r"def _agent_process_env\(\n.*?(?=\n\ndef _resolve_provider_command_attempt)",
    '''def _agent_process_env(\n    provider: str,\n    repo_path: str | Path,\n    *,\n    artifact_dir: str | Path | None = None,\n) -> dict[str, str]:\n    env = filtered_agent_environment(external_agent_provider_env_hints(provider))\n    env["CODETALK_AGENT_READONLY"] = "1"\n    env["CODETALK_REPO_PATH"] = str(Path(repo_path).resolve())\n    if artifact_dir is not None:\n        resolved_artifact_dir = Path(artifact_dir).expanduser().resolve()\n        resolved_artifact_dir.mkdir(parents=True, exist_ok=True)\n        env["CODETALK_AGENT_ARTIFACT_DIR"] = str(resolved_artifact_dir)\n    elif not env.get("CODETALK_AGENT_ARTIFACT_DIR"):\n        runtime_temp_dir = settings.ensure_runtime_temp_path()\n        env["CODETALK_AGENT_ARTIFACT_DIR"] = tempfile.mkdtemp(\n            prefix="codetalk-agent-probe-",\n            dir=runtime_temp_dir,\n        )\n    if provider == "claude-code":\n        configured = str(getattr(settings, "claude_code_config_path", "") or "").strip()\n        if configured:\n            env["CCR_CONFIG_PATH"] = configured\n        elif not env.get("CCR_CONFIG_PATH"):\n            discovered = _existing_ccr_config_path()\n            if discovered:\n                env["CCR_CONFIG_PATH"] = discovered\n    return env\n\n\ndef _sandbox_external_agent_argv(\n    process_argv: list[str],\n    *,\n    env: dict[str, str],\n    cwd: str | Path,\n) -> tuple[list[str], dict[str, object]]:\n    artifact_dir = Path(env["CODETALK_AGENT_ARTIFACT_DIR"])\n    launch = prepare_agent_sandbox(\n        runtime={\n            "sandbox_mode": settings.external_agent_sandbox_mode,\n            "requires_network": True,\n            "sandbox_write_paths": settings.external_agent_sandbox_write_paths,\n            "sandbox_command": process_argv[0] if process_argv else "",\n        },\n        cwd=str(cwd),\n        artifact_dir=artifact_dir,\n    )\n    return [*launch.wrapper, *process_argv], launch.audit\n''',
    "external environment and sandbox helpers",
)
external = external.replace("env, network_context = _agent_process_env_with_network_context(", "env = _agent_process_env(")
external = external.replace("                    network_context=network_context,\n", "")
write("backend/app/services/external_agent_discovery.py", external)

# AI-thread Agent bridge: no policy context, no environment scrubbing, no policy result metadata.
bridge = read("backend/app/services/agent_cli_bridge.py")
bridge = sub(
    bridge,
    r"from app\.services\.runtime_environment import \(\n.*?\n\)\n",
    "",
    "bridge import",
)
bridge = sub(
    bridge,
    r"\n\ndef _runtime_requires_network\(runtime: dict\[str, Any\]\) -> bool:\n.*?(?=\n\ndef _network_policy_error)",
    "",
    "bridge network requirement helper",
)
bridge = sub(
    bridge,
    r"\n\ndef _network_policy_error\(context: Any\) -> str:\n.*?(?=\n\ndef _probe_network_result)",
    "",
    "bridge policy error",
)
bridge = sub(
    bridge,
    r"def _probe_network_result\(context: Any, \*\*payload: Any\) -> dict\[str, Any\]:\n    return \{\*\*payload, \"network_policy\": context\.snapshot\(\)\}\n",
    "def _probe_result(**payload: Any) -> dict[str, Any]:\n    return payload\n",
    "bridge probe result",
)
bridge = sub(
    bridge,
    r"def _sandbox_runtime_with_network_context\(\n.*?(?=\n\nasync def probe_agent_runtime)",
    '''def _sandbox_runtime(\n    *,\n    runtime: dict[str, Any],\n    command: str,\n    read_paths: list[str],\n    **extra: Any,\n) -> dict[str, Any]:\n    return {\n        **runtime,\n        "sandbox_mode": runtime.get("sandbox_mode") or settings.external_agent_sandbox_mode,\n        "requires_network": bool(runtime.get("requires_network", True)),\n        "sandbox_write_paths": runtime.get(\n            "sandbox_write_paths", settings.external_agent_sandbox_write_paths\n        ),\n        "sandbox_command": command,\n        "sandbox_read_paths": read_paths,\n        **extra,\n    }\n''',
    "bridge sandbox helper",
)
bridge = bridge.replace("_sandbox_runtime_with_network_context(", "_sandbox_runtime(")
bridge = bridge.replace("                context=network_context,\n", "")
bridge = bridge.replace("            context=network_context,\n", "")
bridge = exact(
    bridge,
    '''    network_context = resolve_agent_network_context(\n        requires_network=_runtime_requires_network(runtime),\n        environment=raw_env,\n    )\n    if not network_context.allowed:\n        _cleanup_owned_artifact_dir(owned_artifact_dir)\n        return {\n            "success": False,\n            "message": _network_policy_error(network_context),\n            "network_policy": network_context.snapshot(),\n        }\n    env = dict(network_context.sanitized_environment)\n''',
    "    env = dict(raw_env)\n",
    "bridge runtime probe environment",
)
bridge = exact(
    bridge,
    '''    network_context = resolve_agent_network_context(\n        requires_network=_runtime_requires_network(runtime),\n        environment=raw_env,\n    )\n    if not network_context.allowed:\n        _cleanup_owned_artifact_dir(owned_artifact_dir)\n        raise AgentRuntimeError(_network_policy_error(network_context))\n    env = dict(network_context.sanitized_environment)\n''',
    "    env = dict(raw_env)\n",
    "bridge stream environment",
)
bridge = bridge.replace("_probe_network_result(", "_probe_result(")
bridge = re.sub(r"_probe_result\(\n\s*network_context,\n", "_probe_result(\n", bridge)
bridge = bridge.replace("_probe_result(network_context, **", "_probe_result(**")
bridge = bridge.replace("                    network_context=network_context,\n", "")
bridge = bridge.replace("                network_context=network_context,\n", "")
bridge = bridge.replace(
    "    *, runtime: dict[str, Any], command: str, network_context: Any | None = None\n",
    "    *, runtime: dict[str, Any], command: str\n",
)
bridge = sub(
    bridge,
    r"    if network_context is None:\n        network_context = resolve_agent_network_context\(\n            requires_network=_runtime_requires_network\(runtime\),\n            environment=_build_env\(runtime\),\n        \)\n",
    "",
    "bridge Claude context fallback",
)
bridge = sub(
    bridge,
    r"    if network_context is None:\n        network_context = resolve_agent_network_context\(\n            requires_network=_runtime_requires_network\(runtime\),\n            environment=_build_env\(runtime, include_claude_auth=False\),\n        \)\n",
    "",
    "bridge Codex context fallback",
)
claude_start = bridge.index("async def _probe_claude_auth_in_runtime_sandbox")
codex_start = bridge.index("async def _probe_codex_model_in_runtime_sandbox")
claude_segment = bridge[claude_start:codex_start].replace(
    "            env = dict(network_context.sanitized_environment)\n",
    "            env = _build_env(runtime)\n",
)
next_start = bridge.index("def _claude_readiness_result", codex_start)
codex_segment = bridge[codex_start:next_start].replace(
    "            env = dict(network_context.sanitized_environment)\n",
    "            env = _build_env(runtime, include_claude_auth=False)\n",
)
bridge = bridge[:claude_start] + claude_segment + codex_segment + bridge[next_start:]
bridge = sub(
    bridge,
    r"    if settings\.intranet_network_mode:\n        env = scrub_intranet_agent_environment\(env\)\n",
    "",
    "bridge environment scrubbing",
)
write("backend/app/services/agent_cli_bridge.py", bridge)

# Remove the compatibility module itself.
(ROOT / "backend/app/services/runtime_environment.py").unlink(missing_ok=True)

# Remove this one-shot script from the product commit.
Path(__file__).unlink(missing_ok=True)

# Strict residual validation in runtime source.
for base in (ROOT / "backend/app", ROOT / "frontend/src"):
    for path in base.rglob("*"):
        if not path.is_file() or path.suffix not in {".py", ".ts", ".tsx"}:
            continue
        text = path.read_text(encoding="utf-8")
        forbidden = (
            "runtime_environment",
            "NetworkEgressBlocked",
            "resolve_agent_network_context",
            "scrub_intranet_agent_environment",
            "require_runtime_url",
            "require_runtime_model_request_url",
            "require_configured_model_request_url",
            "network_policy",
            "intranet_network_mode",
            "intranet_require_os_sandbox",
        )
        hits = [token for token in forbidden if token in text]
        if hits:
            raise RuntimeError(f"runtime policy compatibility remains in {path}: {hits}")

print("removed final runtime network-policy compatibility layer")
