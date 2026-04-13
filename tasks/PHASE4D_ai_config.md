# Phase 4D: AI 配置管理

**前置依赖：Phase 2A + 2B 完成**
**可与其他 Phase 4 任务并行**

## 任务目标

实现 LLM Provider 配置和 AI 全局开关功能。

## 步骤

### 1. 后端 — Settings API (`backend/app/api/settings.py`)

```python
@router.get("/api/settings/llm")
async def get_llm_configs():
    """获取所有 LLM 配置（api_key 脱敏）"""
    configs = await get_all_llm_configs()
    return [LLMConfigResponse(
        id=c.id,
        provider=c.provider,
        model_name=c.model_name,
        api_key_masked=mask_key(c.api_key_encrypted),  # "sk-...xxxx"
        base_url=c.base_url,
        is_default=c.is_default,
    ) for c in configs]

@router.post("/api/settings/llm")
async def save_llm_config(config: LLMConfigCreate):
    """保存 LLM 配置（api_key 加密存储）"""
    encrypted_key = encrypt_key(config.api_key)
    # 保存到数据库
    ...

@router.put("/api/settings/llm/{id}/default")
async def set_default_llm(id: UUID):
    """设为默认 LLM"""
    ...

@router.delete("/api/settings/llm/{id}")
async def delete_llm_config(id: UUID):
    ...
```

### 2. API Key 加密 (`backend/app/utils/crypto.py`)

使用 cryptography.fernet 对称加密：

```python
from cryptography.fernet import Fernet
import os

# 从环境变量读取加密密钥
FERNET_KEY = os.environ.get("FERNET_KEY", Fernet.generate_key())
fernet = Fernet(FERNET_KEY)

def encrypt_key(plain: str) -> str:
    return fernet.encrypt(plain.encode()).decode()

def decrypt_key(encrypted: str) -> str:
    return fernet.decrypt(encrypted.encode()).decode()

def mask_key(encrypted: str) -> str:
    decrypted = decrypt_key(encrypted)
    if len(decrypted) > 8:
        return decrypted[:3] + "..." + decrypted[-4:]
    return "***"
```

在 `.env.example` 中添加 `FERNET_KEY`。

### 3. AI 服务 (`backend/app/services/ai_service.py`)

```python
async def summarize_results(
    results: list[UnifiedResult],
    llm_config: LLMConfig
) -> str:
    """用配置的 LLM 总结分析结果"""
    decrypted_key = decrypt_key(llm_config.api_key_encrypted)

    # 构建 prompt
    prompt = build_summary_prompt(results)

    if llm_config.provider == "openai":
        return await call_openai(prompt, llm_config.model_name, decrypted_key)
    elif llm_config.provider == "anthropic":
        return await call_anthropic(prompt, llm_config.model_name, decrypted_key)
    elif llm_config.provider == "ollama":
        return await call_ollama(prompt, llm_config.model_name, llm_config.base_url)
    else:
        # 自定义 OpenAI 兼容接口
        return await call_openai_compatible(prompt, llm_config)

async def call_openai(prompt, model, api_key):
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json={"model": model, "messages": [{"role": "user", "content": prompt}]},
        )
        return resp.json()["choices"][0]["message"]["content"]

# 类似实现 call_anthropic, call_ollama
```

### 4. deepwiki LLM 配置同步

当用户更改 LLM 配置时，需要同步到 deepwiki 容器：
- 方案 A：重启 deepwiki 容器并传入新的环境变量
- 方案 B：如果 deepwiki 支持运行时配置，通过 API 更新

暂用方案 A：
```python
@router.post("/api/settings/llm/sync-deepwiki")
async def sync_to_deepwiki():
    """同步 LLM 配置到 deepwiki 容器"""
    config = await get_default_llm()
    # 通过 Docker SDK 重启 deepwiki 容器，更新环境变量
    ...
```

### 5. 前端 — Settings 页面

**LLM Provider 配置区域：**
- Provider 下拉：OpenAI / Anthropic / Ollama / Custom (OpenAI compatible)
- Model Name：文本输入（预设常用模型名如 gpt-4o, claude-sonnet-4-20250514）
- API Key：密码输入框
- Base URL：当选择 Ollama 或 Custom 时显示
- "Test Connection" 按钮：发送测试请求验证配置有效
- "Set as Default" / "Delete" 按钮
- 多个 LLM 配置列表

**AI 全局开关：**
- 大号 Toggle 开关
- 说明文字："启用 AI 后，deepwiki 将生成文档，分析结果将获得 AI 总结"
- 关闭时：创建任务时 deepwiki 选项灰显不可选，ai_enabled 默认 false

**Test Connection 实现：**
```python
@router.post("/api/settings/llm/test")
async def test_llm_connection(config: LLMConfigCreate):
    """测试 LLM 连接"""
    try:
        response = await ai_service.test_connection(config)
        return {"success": True, "message": response}
    except Exception as e:
        return {"success": False, "error": str(e)}
```

## 验收标准

- [ ] 可添加/编辑/删除 LLM Provider 配置
- [ ] API Key 加密存储，前端仅显示脱敏版本
- [ ] Test Connection 按钮可验证配置有效性
- [ ] AI 开关关闭时 deepwiki 不可选，任务不调用 LLM
- [ ] AI 开关开启时分析结果附带 LLM 总结
- [ ] 支持 OpenAI / Anthropic / Ollama / Custom 四种 provider
