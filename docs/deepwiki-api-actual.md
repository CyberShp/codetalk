# deepwiki-open 真实 API 文档

> Phase 0 验证产出 | 验证日期: 2026-04-14
> Docker 镜像: ghcr.io/asyncfuncai/deepwiki-open:latest
> 架构: Next.js 前端 (port 3000) + FastAPI 后端 (port 8001)

## 关键发现

**v1 计划中假设的 `POST /api/wiki/generate` 端点不存在。**

Wiki 生成是由 Next.js 前端编排的（多次调用 `/chat/completions/stream`），不是单一 API 调用。

## 架构

```
deepwiki-open 容器
├── Next.js 前端 (port 3000) — 编排 Wiki 生成，调用后端 API
├── FastAPI 后端 (port 8001) — RAG Q&A、Wiki 缓存、仓库结构
└── 存储: ~/.adalflow/repos/ + databases/ + wikicache/
```

## 真实 API 端点列表 (port 8001)

### 基础

| 端点 | 方法 | 说明 | 已验证 |
|------|------|------|--------|
| `/` | GET | 欢迎页 + 端点列表 | ✅ 200 |
| `/health` | GET | 健康检查 | ✅ `{"status":"healthy"}` |
| `/docs` | GET | Swagger UI | ✅ 200 |
| `/openapi.json` | GET | OpenAPI 3.1 完整 schema | ✅ |

### 核心 — RAG Q&A (CodeTalks 主要使用的端点)

**`POST /chat/completions/stream`** — 流式 RAG 对话

请求体 (`ChatCompletionRequest`):
```json
{
  "repo_url": "https://github.com/owner/repo",  // 必填
  "messages": [                                  // 必填
    {"role": "user", "content": "Explain the architecture"}
  ],
  "filePath": "src/main.py",         // 可选：聚焦特定文件
  "token": "ghp_xxx",                // 可选：私有仓库 access token
  "type": "github",                  // github | gitlab | bitbucket
  "provider": "openai",              // google | openai | openrouter | ollama | bedrock | azure | dashscope
  "model": "gpt-4o",                // 模型 ID
  "language": "en",                  // en | zh | ja | es | kr | vi
  "excluded_dirs": "node_modules,dist",  // 排除目录（逗号分隔）
  "excluded_files": "*.min.js",          // 排除文件（逗号分隔）
  "included_dirs": "src,lib",            // 仅包含目录（逗号分隔）
  "included_files": "*.py,*.ts"          // 仅包含文件（逗号分隔）
}
```

响应：`text/event-stream`，流式返回生成内容（Markdown + Mermaid）

**`WS /ws/chat`** — WebSocket 版本的 RAG 对话（同样参数）

### 仓库结构

**`GET /local_repo/structure?path=/data/repos/myrepo`** — 获取仓库文件树

响应:
```json
{
  "file_tree": "src/main.py\nsrc/utils.py\nREADME.md",  // 换行分隔的文件路径
  "readme": "# My Repo\n..."                              // README 内容
}
```

### Wiki 缓存管理

**`GET /api/wiki_cache`** — 获取已缓存的 Wiki
- 参数: `owner`, `repo`, `repo_type`, `language` (全部必填)
- 响应: `WikiCacheData` 或 `null`

**`POST /api/wiki_cache`** — 存储 Wiki 缓存
- 请求体: `WikiCacheRequest` (repo, language, wiki_structure, generated_pages, provider, model)

**`DELETE /api/wiki_cache`** — 删除 Wiki 缓存

**`GET /api/processed_projects`** — 列出已处理的项目
- 响应: `ProcessedProjectEntry[]`

### 导出

**`POST /export/wiki`** — 导出 Wiki
- 请求体: `WikiExportRequest` (repo_url, pages: WikiPage[], format: "markdown" | "json")
- 响应: 下载文件

### 配置

**`GET /models/config`** — 获取可用的 LLM Provider 和模型列表

已确认的 Provider:
- dashscope (qwen-plus, qwen-turbo, deepseek-r1)
- google (gemini-2.5-flash, gemini-2.5-pro)
- openai (gpt-5, gpt-5-nano, ...)
- openrouter
- ollama
- bedrock
- azure
- 全部支持 `supportsCustomModel: true`

**`GET /lang/config`** — 语言配置
**`GET /auth/status`** — 认证状态
**`POST /auth/validate`** — 验证授权码

## 关键数据模型

### WikiPage
```json
{
  "id": "string",
  "title": "string",
  "content": "string (Markdown, may contain Mermaid blocks)",
  "filePaths": ["string"],
  "importance": "high | medium | low",
  "relatedPages": ["string"]
}
```

### WikiStructureModel
```json
{
  "id": "string",
  "title": "string",
  "description": "string",
  "pages": [WikiPage],
  "sections": [WikiSection] | null,
  "rootSections": ["string"] | null
}
```

## CodeTalks Adapter 设计建议

### 方案 A: RAG Q&A 模式 (推荐 MVP)

使用 `/chat/completions/stream`：
1. `prepare()`: 调用 `/local_repo/structure` 获取仓库结构
2. `analyze()`: 调用 `/chat/completions/stream` 发送分析请求，流式收集结果
3. 结果中的 Markdown + Mermaid 直接作为 UnifiedResult 返回

优点：简单、单次调用
缺点：无结构化 Wiki 页面

### 方案 B: Wiki 缓存模式 (未来增强)

1. 通过 deepwiki 前端（或复制其 prompt 策略）生成 Wiki
2. 通过 `/api/wiki_cache` 读取结构化 Wiki
3. 展示多页面文档 + 目录导航

优点：丰富的结构化文档
缺点：需要理解前端的 Wiki 生成编排逻辑

### 方案 C: deepwiki 前端嵌入 (备选)

将 deepwiki 前端 (port 3001) 嵌入 iframe。
适合不想自己编排的场景。

## 注意事项

1. deepwiki 需要至少一个 LLM API key（OPENAI_API_KEY 或 GOOGLE_API_KEY）
2. `/chat/completions/stream` 的 `provider` 和 `model` 参数允许运行时切换 LLM — 与 CodeTalks 的 AI 配置功能天然匹配
3. 本地仓库需要 volume mount 到容器内，通过 `/local_repo/structure` 读取
4. 流式响应需要 SSE 或 WebSocket 处理
