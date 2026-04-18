# Sprint 6: 高级 RAG 调优

**前置依赖**: Sprint 1-5 已完成（chat payload 增强、Deep Research、WebSocket、动态模型选择、Chat 面板独立化）
**对应计划**: `CODETALK_DEEPWIKI_INTEGRATION_PLAN.md` 阶段六（第 561-604 行）

---

## 目标

针对不同场景优化 DeepWiki RAG 检索参数，提升检索精准度和上下文质量。

---

## 6.1 embedder.json 参数调优

**文件**: `docker/deepwiki/embedder.json`

### 当前值 → 目标值

| 参数 | 当前 | 目标 | 原因 |
|------|------|------|------|
| `retriever.top_k` | 20 | 30 | 增大检索范围，提供更多上下文片段 |
| `text_splitter.chunk_size` | 350 | 500 | 增大 chunk，保留更多连续上下文 |
| `text_splitter.chunk_overlap` | 100 | 150 | 增大重叠，避免信息断裂 |

### 步骤

1. 修改 `docker/deepwiki/embedder.json` 中的三个参数
2. 重启 deepwiki 容器使配置生效
3. **重要**: 清除 FAISS 索引缓存（`docker exec deepwiki rm -rf /root/.adalflow/repo_cache/*` 或等效操作），让 deepwiki 基于新参数重建索引

### 验收标准

- [x] `retriever.top_k` = 30
- [x] `text_splitter.chunk_size` = 500
- [x] `text_splitter.chunk_overlap` = 150
- [x] deepwiki 容器可正常启动并响应 `/health`

---

## 6.2 智能文件过滤（excluded_dirs）

**问题**: 当前 `chat_payload.py` 和 `wiki_orchestrator.py` 均未传递 `excluded_dirs`，导致 DeepWiki RAG 检索包含 `node_modules`、`.git` 等噪声目录。

### 默认排除目录

```python
DEFAULT_EXCLUDED_DIRS = [
    "node_modules", ".git", "dist", "build", "__pycache__",
    ".next", "vendor", "coverage", ".nyc_output",
    ".venv", "venv", ".tox", "egg-info",
]
```

### 步骤

#### 6.2.1 后端 chat_payload.py

**文件**: `backend/app/services/chat_payload.py`

1. 在 `build_deepwiki_payload()` 函数签名中添加 `excluded_dirs: list[str] | None = None` 参数
2. 在 payload 构建中添加：
   ```python
   # 智能文件过滤 — 合并默认排除 + 调用方自定义
   effective_excluded = list(DEFAULT_EXCLUDED_DIRS)
   if excluded_dirs:
       effective_excluded.extend(d for d in excluded_dirs if d not in effective_excluded)
   payload["excluded_dirs"] = "\n".join(effective_excluded)
   ```
3. 在文件顶部定义 `DEFAULT_EXCLUDED_DIRS` 常量

#### 6.2.2 后端 wiki_orchestrator.py

**文件**: `backend/app/services/wiki_orchestrator.py`

1. 在 `_determine_structure()` 和 `_generate_page()` 的 payload 中添加 `excluded_dirs`
2. 复用 `chat_payload.py` 中的 `DEFAULT_EXCLUDED_DIRS`（从 chat_payload 导入）

#### 6.2.3 API 层传递

**文件**: `backend/app/api/repo_chat.py`

1. `ChatRequest` 已有 `excluded_dirs` 字段（Sprint 1 添加）
2. 确认 `build_deepwiki_payload()` 调用处传递了 `excluded_dirs` 参数
3. HTTP 和 WebSocket 两个端点都需检查

### 验收标准

- [x] `chat_payload.py` 的 payload 中包含 `excluded_dirs` 字段
- [x] wiki_orchestrator 的 structure 和 page 生成 payload 中包含 `excluded_dirs`
- [x] 默认排除列表至少包含 `node_modules`, `.git`, `__pycache__`, `.next`, `dist`, `build`
- [x] 调用方可传入额外排除目录，与默认列表合并

---

## 6.3 Embedding 模型自动选择

**现状**: `docker-compose.yml` 已有 `DEEPWIKI_EMBEDDER_TYPE` 环境变量，`component_manager.py` 已有 `embedder_type` 设置项和 UI select。`docker-compose.override.yml` 本地覆盖为 `ollama`。

### 步骤

1. **确认现有链路完整性**: 验证 Settings 页面的 embedder_type 选择能否正确写入 `.env` 或 `docker-compose.override.yml`
2. **添加 LLM provider → embedder type 推荐映射**:
   ```python
   PROVIDER_EMBEDDER_MAP = {
       "openai": "openai",
       "google": "google",
       "ollama": "ollama",
       "bedrock": "bedrock",
       "openrouter": "openai",  # OpenRouter 走 OpenAI embedding
       "custom": "openai",
   }
   ```
3. **前端提示**: 当用户在 Settings 中切换 LLM provider 时，提示推荐的 embedder type（非强制切换）

### 验收标准

- [x] Settings 页面可选择 embedder type（openai/ollama/google/bedrock）
- [x] 切换 LLM provider 时显示推荐的 embedder type
- [x] embedder type 变更后正确反映到 deepwiki 容器配置

---

## 注意事项

1. **FAISS 缓存**: 修改 embedder.json 参数或切换 embedder type 后，必须清除 FAISS 索引缓存。否则旧索引与新参数不匹配
2. **铁律**: 所有 RAG 检索由 DeepWiki FAISS retriever 执行，CodeTalks 只传递参数，不实现任何检索逻辑
3. **渐进式**: 参数调优可先在单个仓库上测试效果，确认质量提升后再推广
