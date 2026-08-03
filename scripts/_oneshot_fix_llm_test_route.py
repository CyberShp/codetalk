from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def write(path: str, text: str) -> None:
    (ROOT / path).write_text(text, encoding="utf-8")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one match, got {count}")
    return text.replace(old, new, 1)


settings_path = "backend/app/api/settings.py"
settings = read(settings_path)
settings = replace_once(
    settings,
    '@router.post("/llm/test")\nasync def test_llm_connection(',
    '@router.post("/llm/test")\n@router.post("/llm/actions/test-connection")\nasync def test_llm_connection(',
    "LLM test route decorators",
)
settings = settings.replace(
    '    """Test the same deployment-authorized inference route used at runtime."""',
    '    """Test the selected model using General Settings transport."""',
    1,
)
write(settings_path, settings)

api_path = "frontend/src/lib/api.ts"
api = read(api_path)
api = replace_once(
    api,
    'request<{ success: boolean; message: string }>("/api/settings/llm/test", {',
    'request<{ success: boolean; message: string }>("/api/settings/llm/actions/test-connection", {',
    "frontend LLM test endpoint",
)
write(api_path, api)

openai_path = "backend/app/llm/openai_compat.py"
openai = read(openai_path)
openai = replace_once(
    openai,
    "            self._require_approved_model_endpoint(chat_url)\n",
    "",
    "OpenAI stale policy call",
)
openai = replace_once(
    openai,
    '''            if chat_resp.status_code < 400:\n                return True, "连接成功（已验证实际推理接口）"\n            if chat_resp.status_code < 500:\n                return False, f"服务可达，但聊天接口认证或配置失败 (HTTP {chat_resp.status_code})"\n            return False, f"聊天接口服务端错误 (HTTP {chat_resp.status_code})"\n''',
    '''            if chat_resp.status_code < 400:\n                return True, "连接成功（已验证实际推理接口）"\n            if chat_resp.status_code == 405:\n                return False, (\n                    "模型服务返回 HTTP 405（Method Not Allowed）。"\n                    "请检查协议类型与 Base URL；OpenAI 兼容地址应填写服务根地址，"\n                    "不要填写具体的 /chat/completions 路径。"\n                )\n            if chat_resp.status_code < 500:\n                return False, f"服务可达，但聊天接口认证或配置失败 (HTTP {chat_resp.status_code})"\n            return False, f"聊天接口服务端错误 (HTTP {chat_resp.status_code})"\n''',
    "OpenAI 405 diagnostic",
)
write(openai_path, openai)

anthropic_path = "backend/app/llm/anthropic.py"
anthropic = read(anthropic_path)
anthropic = replace_once(
    anthropic,
    "            self._require_approved_model_endpoint(url)\n",
    "",
    "Anthropic stale policy call",
)
anthropic = replace_once(
    anthropic,
    '''            if resp.status_code < 400:\n                return True, "连接成功"\n            if resp.status_code < 500:\n                return False, f"服务可达，但认证或接口失败 (HTTP {resp.status_code})"\n            return False, f"服务端错误 (HTTP {resp.status_code})"\n''',
    '''            if resp.status_code < 400:\n                return True, "连接成功"\n            if resp.status_code == 405:\n                return False, (\n                    "模型服务返回 HTTP 405（Method Not Allowed）。"\n                    "请检查协议类型与 Base URL；Anthropic 地址应填写服务根地址，"\n                    "不要填写具体的 /messages 路径。"\n                )\n            if resp.status_code < 500:\n                return False, f"服务可达，但认证或接口失败 (HTTP {resp.status_code})"\n            return False, f"服务端错误 (HTTP {resp.status_code})"\n''',
    "Anthropic 405 diagnostic",
)
write(anthropic_path, anthropic)

# Static contract checks before build/test.
assert '@router.post("/llm/actions/test-connection")' in settings
assert '"/api/settings/llm/actions/test-connection"' in api
assert "_require_approved_model_endpoint" not in openai
assert "_require_approved_model_endpoint" not in anthropic

# One-shot files must not remain in the product branch.
(ROOT / ".github/workflows/_temporary_ui_llm_test_fix.yml").unlink(missing_ok=True)
Path(__file__).unlink(missing_ok=True)
print("fixed LLM test route and removed stale policy calls")
