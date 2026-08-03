from __future__ import annotations

import re
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def write(path: str, text: str) -> None:
    target = ROOT / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


def replace_once(text: str, old: str, new: str, *, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one match, got {count}")
    return text.replace(old, new, 1)


def sub_once(text: str, pattern: str, replacement: str, *, label: str, flags: int = 0) -> str:
    result, count = re.subn(pattern, replacement, text, count=1, flags=flags)
    if count != 1:
        raise RuntimeError(f"{label}: expected one match, got {count}")
    return result


def remove_method(text: str, name: str) -> str:
    pattern = rf"\n    (?:async )?def {re.escape(name)}\(.*?(?=\n    (?:async )?def |\nclass |\Z)"
    return re.sub(pattern, "", text, count=1, flags=re.S)


def remove_import_block(text: str, module: str) -> str:
    text = re.sub(
        rf"\nfrom {re.escape(module)} import \(.*?\n\)\n",
        "\n",
        text,
        flags=re.S,
    )
    text = re.sub(
        rf"\nfrom {re.escape(module)} import [^\n]+\n",
        "\n",
        text,
    )
    return text


# ---------------------------------------------------------------------------
# 1. Remove deployment/network-policy configuration and restore General Settings
#    as the sole source for proxy and PEM configuration.
# ---------------------------------------------------------------------------
config_path = "backend/app/config.py"
config = read(config_path)
config = sub_once(
    config,
    r"\n    # Network policy V2 is deployment-owned\..*?\n    external_agent_sandbox_write_paths:",
    "\n    external_agent_sandbox_write_paths:",
    label="remove network policy settings",
    flags=re.S,
)
write(config_path, config)

factory_path = "backend/app/llm/factory.py"
factory = remove_import_block(read(factory_path), "app.services.network_policy")
factory = sub_once(
    factory,
    r"def _resolve_proxy\(\n    general: dict\[str, str\],\n\) -> tuple\[str \| None, str \| None, bool\]:.*?(?=\n\ndef _model_request_url)",
    '''def _resolve_proxy(\n    general: dict[str, str],\n) -> tuple[str | None, str | None, bool]:\n    """Resolve model transport exclusively from General Settings.\n\n    ``proxy_mode=none`` is an explicit direct connection and therefore disables\n    environment proxy discovery.  ``ssl_cert_path`` is optional: when omitted,\n    HTTPX uses its normal platform/default CA store.\n    """\n    mode = str(general.get("proxy_mode") or "none").strip().lower()\n    ssl_cert_path = str(general.get("ssl_cert_path") or "").strip() or None\n\n    if mode == "none":\n        return None, ssl_cert_path, True\n    if mode == "custom":\n        proxy_url = str(general.get("proxy_url") or "").strip() or None\n        return proxy_url, ssl_cert_path, False\n    if mode == "system":\n        return None, ssl_cert_path, False\n\n    logger.warning("Unknown proxy_mode=%r; falling back to direct mode", mode)\n    return None, ssl_cert_path, True\n''',
    label="replace proxy resolver",
    flags=re.S,
)
factory = re.sub(
    r"\n    request_url = _model_request_url\(api_type, base_url\)\n    try:\n        require_runtime_model_request_url\(request_url\)\n    except NetworkEgressBlocked as exc:\n        raise RuntimeError\(\n            .*?\n        \) from exc",
    "",
    factory,
    count=1,
    flags=re.S,
)
factory = factory.replace('        "enforce_network_policy": True,\n', "")
factory = factory.replace('        "configured_model_endpoint": True,\n', "")
write(factory_path, factory)

runtime_error_impl = '''def _runtime_network_error(error: Exception) -> RuntimeError:\n    """Translate transport failures without inventing deployment policy."""\n    if isinstance(error, httpx.HTTPStatusError) and error.response.status_code == 403:\n        return RuntimeError(\n            "模型服务拒绝访问，请检查模型地址、API Key 和模型权限。技术代码：http_403"\n        )\n    text = str(error).lower()\n    if isinstance(error, httpx.ProxyError) or "proxy" in text:\n        return RuntimeError(\n            "代理连接失败，请检查通用设置中的代理模式和代理地址。技术代码：proxy_connection_failed"\n        )\n    if "certificate" in text or "cert_verify" in text or "ssl" in text:\n        return RuntimeError(\n            "TLS 证书校验失败，请检查通用设置中的 PEM 证书路径，"\n            "或检查模型服务返回的证书链。技术代码：tls_ca_verification_failed"\n        )\n    return RuntimeError(\n        "模型连接失败，请检查模型地址、凭据和当前网络。技术代码：model_connection_failed"\n    )\n'''

for llm_path in ("backend/app/llm/openai_compat.py", "backend/app/llm/anthropic.py"):
    text = remove_import_block(read(llm_path), "app.services.network_policy")
    text = sub_once(
        text,
        r"def _runtime_network_error\(error: Exception\) -> RuntimeError:.*?(?=\n\ndef |\n\nclass )",
        runtime_error_impl,
        label=f"replace runtime error in {llm_path}",
        flags=re.S,
    )
    text = text.replace("        enforce_network_policy: bool = False,\n", "")
    text = text.replace("        configured_model_endpoint: bool = False,\n", "")
    text = text.replace("        self._enforce_network_policy = enforce_network_policy\n", "")
    text = text.replace("        self._configured_model_endpoint = configured_model_endpoint\n", "")
    text = re.sub(r"\n        self\._require_approved_model_endpoint\([^\n]+\)", "", text)
    text = remove_method(text, "_require_approved_model_endpoint")
    write(llm_path, text)

embedding_path = "backend/app/llm/embedding_client.py"
embedding = remove_import_block(read(embedding_path), "app.services.network_policy")
embedding = re.sub(r"\n        require_runtime_model_request_url\(url\)", "", embedding)
embedding = replace_once(
    embedding,
    '''        else:\n            self._client = httpx.AsyncClient(\n                verify=verify,\n                trust_env=False,\n                timeout=httpx.Timeout(120, connect=15),\n                limits=pool_limits,\n            )''',
    '''        else:\n            self._client = httpx.AsyncClient(\n                verify=verify,\n                trust_env=True,\n                timeout=httpx.Timeout(120, connect=15),\n                limits=pool_limits,\n            )''',
    label="embedding system proxy behavior",
)
write(embedding_path, embedding)

# ---------------------------------------------------------------------------
# 2. Replace the policy module with a plain runtime-environment helper.
#    Existing Agent call sites retain a small context object only for process
#    launch metadata; it never approves, rejects, scrubs, or rewrites networking.
# ---------------------------------------------------------------------------
runtime_environment = '''"""Runtime environment passthrough helpers.\n\nCodeTalk does not implement network approval, endpoint allow-lists, proxy/CA\ninjection, or Agent egress decisions.  The operating system and the user's\nGeneral Settings own connectivity.\n"""\n\nfrom __future__ import annotations\n\nfrom dataclasses import dataclass\nfrom typing import Mapping\n\n\nclass NetworkEgressBlocked(RuntimeError):\n    """Legacy exception name retained only for import stability; never raised."""\n\n\n@dataclass(frozen=True)\nclass RuntimeEnvironmentContext:\n    sanitized_environment: dict[str, str]\n    requires_network: bool\n    allowed: bool = True\n    reason: str = "runtime_environment_passthrough"\n    remediation: str = "请检查运行环境、模型配置或 CLI 自身网络设置。"\n    requires_os_network_isolation: bool = False\n\n    def require_allowed(self) -> "RuntimeEnvironmentContext":\n        return self\n\n    def snapshot(self) -> dict[str, object]:\n        return {\n            "source": "runtime_environment",\n            "requires_network": self.requires_network,\n            "allowed": True,\n        }\n\n\ndef resolve_agent_network_context(\n    *,\n    requires_network: bool,\n    environment: Mapping[str, str] | None = None,\n) -> RuntimeEnvironmentContext:\n    return RuntimeEnvironmentContext(\n        sanitized_environment=dict(environment or {}),\n        requires_network=requires_network,\n    )\n\n\ndef scrub_intranet_agent_environment(environment: dict[str, str]) -> dict[str, str]:\n    return dict(environment)\n\n\ndef require_runtime_url(url: str) -> None:\n    return None\n\n\ndef require_runtime_model_request_url(url: str) -> None:\n    return None\n\n\ndef require_configured_model_request_url(url: str) -> None:\n    return None\n\n\ndef agent_network_is_permitted() -> bool:\n    return True\n'''
write("backend/app/services/runtime_environment.py", runtime_environment)

for path in (ROOT / "backend/app").rglob("*.py"):
    if path.name == "runtime_environment.py":
        continue
    text = path.read_text(encoding="utf-8")
    updated = text.replace(
        "from app.services.network_policy import",
        "from app.services.runtime_environment import",
    )
    if updated != text:
        path.write_text(updated, encoding="utf-8")

old_policy = ROOT / "backend/app/services/network_policy.py"
old_policy.unlink(missing_ok=True)

# ---------------------------------------------------------------------------
# 3. Remove the policy API and policy UI.
# ---------------------------------------------------------------------------
settings_api_path = "backend/app/api/settings.py"
settings_api = read(settings_api_path)
settings_api = remove_import_block(settings_api, "app.services.runtime_environment")
settings_api = sub_once(
    settings_api,
    r"\ndef _network_policy_migration_preview\(\).*?(?=\n# --- LLM Config schemas ---)",
    "\n",
    label="remove settings policy helpers",
    flags=re.S,
)
settings_api = re.sub(
    r"\n@router\.get\(\"/network-policy\"\).*?(?=\n_GENERAL_KEYS)",
    "\n",
    settings_api,
    count=1,
    flags=re.S,
)
settings_api = settings_api.replace(
    '        return "模型连接被运行环境拒绝。CodeTalk 不拦截模型地址，请检查模型配置、凭据或公司网络。"',
    '        return "模型连接失败，请检查模型配置、凭据或当前网络。"',
)
write(settings_api_path, settings_api)

page_path = "frontend/src/app/settings/page.tsx"
page = read(page_path)
page = page.replace("  DeploymentNetworkMigrationPreview,\n", "")
page = page.replace("  DeploymentNetworkPolicy,\n", "")
page = sub_once(
    page,
    r"\nconst NETWORK_MODE_LABEL:.*?(?=\nconst agentTransportLabel)",
    "\n",
    label="remove network policy panel",
    flags=re.S,
)
page = re.sub(r"\n  const \[deploymentNetworkPolicy, setDeploymentNetworkPolicy\].*?;", "", page)
page = re.sub(r"\n  const \[deploymentNetworkPolicyError, setDeploymentNetworkPolicyError\].*?;", "", page)
page = replace_once(
    page,
    "const [llmList, generalData, agentProviderData, runtimeData, networkPolicyResult] = await Promise.all([",
    "const [llmList, generalData, agentProviderData, runtimeData] = await Promise.all([",
    label="settings promise tuple",
)
page = re.sub(
    r"\n        api\.settings\.getNetworkPolicy\(\).*?\n          \}\)\),",
    "",
    page,
    count=1,
    flags=re.S,
)
page = re.sub(r"\n      setDeploymentNetworkPolicy\([^\n]+\);", "", page)
page = re.sub(r"\n      setDeploymentNetworkPolicyError\([^\n]+\);", "", page)
page = re.sub(
    r"\n      <DeploymentNetworkPolicyPanel\n        policy=\{deploymentNetworkPolicy\}\n        error=\{deploymentNetworkPolicyError\}\n      />\n",
    "\n",
    page,
    count=1,
)
write(page_path, page)

api_path = "frontend/src/lib/api.ts"
api_text = read(api_path)
api_text = re.sub(
    r"\n\s*getNetworkPolicy: \(\) =>\n\s*request<DeploymentNetworkPolicy>\(\"/api/settings/network-policy\"[^\n]*\),",
    "",
    api_text,
    count=1,
)
api_text = api_text.replace("  DeploymentNetworkPolicy,\n", "")
write(api_path, api_text)

types_path = "frontend/src/lib/types.ts"
types = read(types_path)
types = re.sub(
    r"\nexport interface DeploymentNetworkMigrationPreview \{.*?\n\}\n",
    "\n",
    types,
    count=1,
    flags=re.S,
)
types = re.sub(
    r"\nexport interface DeploymentNetworkPolicy \{.*?\n\}\n",
    "\n",
    types,
    count=1,
    flags=re.S,
)
write(types_path, types)

# ---------------------------------------------------------------------------
# 4. Permanently prevent Windows command-line overflow for Agent prompts.
# ---------------------------------------------------------------------------
bridge_path = "backend/app/services/agent_cli_bridge.py"
bridge = read(bridge_path)
bridge = replace_once(
    bridge,
    "def _prompt_argument_or_file_bootstrap(prompt: str, *, prompt_file_path: str | None) -> str:\n    if len(str(prompt or \"\").encode(\"utf-8\")) <= MAX_AGENT_ARG_PROMPT_BYTES:\n        return prompt",
    "def _prompt_argument_or_file_bootstrap(\n    prompt: str,\n    *,\n    prompt_file_path: str | None,\n    force_file: bool = False,\n) -> str:\n    if not force_file and len(str(prompt or \"\").encode(\"utf-8\")) <= MAX_AGENT_ARG_PROMPT_BYTES:\n        return prompt",
    label="bridge prompt bootstrap signature",
)
bridge = replace_once(
    bridge,
    "    prompt_argument = _prompt_argument_or_file_bootstrap(\n        prompt,\n        prompt_file_path=prompt_file_path,\n    )",
    "    prompt_argument = _prompt_argument_or_file_bootstrap(\n        prompt,\n        prompt_file_path=prompt_file_path,\n        force_file=(\n            os.name == \"nt\"\n            and prompt_transport in {\"argv_last\", \"claude_print_arg\", \"opencode_run_arg\"}\n        ),\n    )",
    label="bridge Windows file transport",
)
write(bridge_path, bridge)

harness_path = "backend/app/services/agent_run_harness.py"
harness = read(harness_path)
harness = replace_once(
    harness,
    "            and len(stdin_payload.encode(\"utf-8\")) > _MAX_ARG_PROMPT_BYTES\n        ):",
    "            and (\n                os.name == \"nt\"\n                or len(stdin_payload.encode(\"utf-8\")) > _MAX_ARG_PROMPT_BYTES\n            )\n        ):",
    label="harness Windows prompt file",
)
write(harness_path, harness)

external_path = "backend/app/services/external_agent_discovery.py"
external = read(external_path)
external = replace_once(
    external,
    "    candidates = [(primary_argv, primary_stdin, primary_transport, \"\")]\n    if primary_transport != \"argv\":\n        return candidates\n    stdin_argv = _strip_prompt_arg_transport_tokens(provider, argv)",
    "    candidates = [(primary_argv, primary_stdin, primary_transport, \"\")]\n    if primary_transport != \"argv\":\n        return candidates\n    stdin_argv = _strip_prompt_arg_transport_tokens(provider, argv)\n    if os.name == \"nt\" and stdin_argv != primary_argv:\n        return [(stdin_argv, prompt.encode(\"utf-8\"), \"stdin\", \"windows_argv_limit\")]",
    label="discovery Windows stdin transport",
)
write(external_path, external)

# Focused regression tests.
write(
    "backend/tests/test_general_settings_transport.py",
    '''from app.llm.factory import _resolve_proxy\n\n\ndef test_direct_mode_uses_only_general_settings_pem():\n    assert _resolve_proxy({\n        "proxy_mode": "none",\n        "proxy_url": "http://ignored.example:8080",\n        "ssl_cert_path": "C:/certs/model.pem",\n    }) == (None, "C:/certs/model.pem", True)\n\n\ndef test_direct_mode_without_pem_uses_default_ca_store():\n    assert _resolve_proxy({\n        "proxy_mode": "none",\n        "proxy_url": "",\n        "ssl_cert_path": "",\n    }) == (None, None, True)\n\n\ndef test_custom_proxy_and_pem_come_from_general_settings():\n    assert _resolve_proxy({\n        "proxy_mode": "custom",\n        "proxy_url": "http://127.0.0.1:7890",\n        "ssl_cert_path": "/certs/provider.pem",\n    }) == ("http://127.0.0.1:7890", "/certs/provider.pem", False)\n''',
)
write(
    "backend/tests/test_agent_prompt_file_transport.py",
    '''from app.services.agent_cli_bridge import _prompt_argument_or_file_bootstrap\n\n\ndef test_force_file_transport_uses_short_bootstrap(tmp_path):\n    prompt_file = tmp_path / "prompt.md"\n    prompt_file.write_text("x" * 100, encoding="utf-8")\n    result = _prompt_argument_or_file_bootstrap(\n        "x" * 100,\n        prompt_file_path=str(prompt_file),\n        force_file=True,\n    )\n    assert "CODETALK_AGENT_PROMPT_FILE" in result\n    assert len(result) < 500\n''',
)

# Remove obsolete policy-only tests and frontend contracts.
for obsolete in (
    "backend/tests/test_network_policy.py",
    "backend/tests/test_network_mode_migration.py",
    "backend/tests/test_agent_network_boundary.py",
    "backend/tests/test_task_engine_network_policy.py",
    "backend/tests/test_network_policy_settings_api.py",
    "frontend/scripts/network-policy-ui-contract.test.mjs",
    "frontend/scripts/phase7-settings-migration.test.mjs",
):
    (ROOT / obsolete).unlink(missing_ok=True)

# Remove one-shot automation from the resulting product commit.
for one_shot in (
    "scripts/_oneshot_remove_network_policy.py",
    ".github/workflows/_oneshot_remove_network_policy.yml",
    ".github/codetalk-fix.trigger",
):
    (ROOT / one_shot).unlink(missing_ok=True)

# Hard residual checks for runtime/product code.
for base in (ROOT / "backend/app", ROOT / "frontend/src"):
    for path in base.rglob("*"):
        if not path.is_file() or path.suffix not in {".py", ".ts", ".tsx"}:
            continue
        text = path.read_text(encoding="utf-8")
        forbidden = [
            "approved_ca_bundle_path",
            "approved_proxy_config_id",
            "deployment_egress_policy_id",
            "network_policy_v2_enabled",
            "intranet_allowed_hosts",
            "intranet_allowed_cidrs",
            "/api/settings/network-policy",
            "DeploymentNetworkPolicy",
        ]
        hits = [token for token in forbidden if token in text]
        if hits:
            raise RuntimeError(f"residual policy tokens in {path}: {hits}")

print("one-shot CodeTalk runtime cleanup completed")
