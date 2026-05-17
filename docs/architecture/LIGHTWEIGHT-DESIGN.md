# CodeTalk Lightweight — 架构设计文档

> 版本: 1.0 | 分支: feat | 作者: Opus (架构师)

## 1. 目标

将 CodeTalk 从重型容器编排平台精简为**轻量级代码分析工具**，可在内网无 Docker 环境下运行。

### 1.1 约束

- **内网部署**: 无法联网下载依赖（npm 有内网源，pip 需离线包）
- **无 Docker**: 所有工具以本地进程方式运行
- **小上下文 LLM**: 内网 AI 模型（如 minimax-2.5）仅 128K-192K 上下文
- **tiktoken 离线**: 需预缓存 tiktoken 编码文件，设置 `TIKTOKEN_CACHE_DIR`

### 1.2 保留的工具

| 工具 | 运行方式 | 用途 |
|------|---------|------|
| GitNexus | `gitnexus serve --port 7100` 本地进程 | 代码图谱、业务流程、社区发现 |
| DeepWiki-Open | Python + Node 本地前后端 | RAG 代码知识库生成 |
| Zoekt（可选） | 本地进程 | 代码搜索 |

### 1.3 移除的功能

当前版本的所有功能均不保留。从零设计。

---

## 2. 系统架构

```
┌──────────────────────────────────────────────────────┐
│                     用户浏览器                         │
│                  Next.js 前端 (:3005)                  │
└─────────────────────┬────────────────────────────────┘
                      │ HTTP REST
┌─────────────────────▼────────────────────────────────┐
│                FastAPI 后端 (:8100)                    │
│  ┌───────────┐ ┌──────────┐ ┌──────────────────────┐ │
│  │ 任务管理   │ │ 设置管理  │ │   分析编排引擎        │ │
│  │ /api/tasks │ │/api/     │ │  AnalysisPipeline    │ │
│  │           │ │ settings │ │                      │ │
│  └───────────┘ └──────────┘ └──────────┬───────────┘ │
│                                        │              │
│  ┌─────────────┐  ┌──────────────────┐ │              │
│  │ LLM Client  │  │ 进程管理器       │  │              │
│  │ Anthropic /  │  │ ProcessManager  │  │              │
│  │ OpenAI 兼容  │  │ (spawn/health)  │  │              │
│  └──────┬──────┘  └───────┬─────────┘ │              │
└─────────┼─────────────────┼───────────┘              │
          │                 │                           │
          ▼                 ▼                           │
   ┌────────────┐   ┌─────────────┐  ┌──────────────┐  │
   │ 内网 AI API │   │GitNexus:7100│  │DeepWiki-Open │  │
   │ (LLM)      │   │(本地进程)    │  │:8091(API)    │  │
   └────────────┘   └─────────────┘  │:3001(UI)     │  │
                                     └──────────────┘  │
```

### 2.1 端口规划

| 服务 | 端口 | 说明 |
|------|------|------|
| 前端 | 3005 | Next.js dev server |
| 后端 API | 8100 | FastAPI |
| GitNexus | 7100 | gitnexus serve |
| DeepWiki-Open API | 8091 | Python API server |
| DeepWiki-Open UI | 3001 | Next.js (DeepWiki 自带) |
| **禁用** | 3003, 3004 | Cat Cafe 保留端口 |

### 2.2 数据存储

SQLite 替代 PostgreSQL，单文件数据库：

```
data/
├── codetalk.db          # SQLite 主数据库
├── repos/               # 代码仓库（symlink 或拷贝）
├── outputs/             # 分析输出文件
│   └── {task_id}/
│       ├── 01-项目与模块地图.md
│       ├── 02-关键业务流程分析.md
│       ├── 03-源码定向阅读记录.md
│       ├── 04-测试设计输入.md
│       ├── 05-需求与设计理解.md     (可选)
│       └── 06-需求设计代码追踪.md   (可选)
└── tiktoken_cache/      # tiktoken 预缓存
```

---

## 3. 数据模型

### 3.1 SQLite 表

```sql
CREATE TABLE tasks (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    repo_path TEXT NOT NULL,
    status TEXT DEFAULT 'pending',
    tools TEXT DEFAULT '[]',
    requirements_doc TEXT,
    design_doc TEXT,
    progress INTEGER DEFAULT 0,
    error_message TEXT,
    created_at TEXT,
    updated_at TEXT
);

CREATE TABLE llm_configs (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    api_type TEXT NOT NULL,
    base_url TEXT NOT NULL,
    api_key TEXT NOT NULL,
    model TEXT NOT NULL,
    max_tokens INTEGER DEFAULT 4096,
    temperature REAL DEFAULT 0.3,
    config_json TEXT,
    is_chat_model BOOLEAN DEFAULT TRUE,
    is_embedding_model BOOLEAN DEFAULT FALSE,
    created_at TEXT
);

CREATE TABLE settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
```

---

## 4. API 设计

### 4.1 任务管理

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | /api/tasks | 创建分析任务 |
| GET | /api/tasks | 获取任务列表 |
| GET | /api/tasks/{id} | 获取任务详情 |
| DELETE | /api/tasks/{id} | 删除任务 |
| GET | /api/tasks/{id}/output | 获取输出列表 |
| GET | /api/tasks/{id}/output/{filename} | 获取输出内容 |
| GET | /api/tasks/{id}/export?format=md | 导出（md/docx/xml） |

### 4.2 设置管理

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | /api/settings/llm | 获取 LLM 配置列表 |
| POST | /api/settings/llm | 创建 LLM 配置 |
| PUT | /api/settings/llm/{id} | 更新 |
| DELETE | /api/settings/llm/{id} | 删除 |
| POST | /api/settings/llm/test | 测试连接 |
| GET | /api/settings/general | 通用设置 |
| PUT | /api/settings/general | 更新通用设置 |

### 4.3 工具状态

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | /api/tools/status | 工具进程状态 |
| POST | /api/tools/{name}/restart | 重启进程 |

### 4.4 WebSocket

| 路径 | 说明 |
|------|------|
| /ws/tasks/{id}/logs | 实时任务日志流 |

---

## 5. 分析编排引擎

### 5.1 Pipeline 模式

AI 仅作为文本生成函数。后端控制每次调用的上下文大小（不超过 40K tokens）。

### 5.2 流程

```
Phase 0: 准备
├── 验证代码路径
├── 非 git 仓库则 git init
└── GitNexus 索引

Phase 1: 数据采集（无 AI）
├── GitNexus 图谱（nodes/edges/processes/communities）
├── DeepWiki RAG embedding + wiki 生成
└── 结构数据存入 task context

Phase 2: 逐模块分析（每次 ≤40K tokens）
├── 对每个 community:
│   ├── GitNexus 模块文件 + 调用关系
│   ├── DeepWiki 模块 wiki 内容
│   └── AI 生成模块摘要（JSON）
└── 存储模块摘要

Phase 3: 报告生成（每份独立调用）
├── 01-项目与模块地图
├── 02-业务流程分析
├── 03-源码阅读记录
├── 04-测试设计输入
├── 05-需求理解（可选）
└── 06-代码追踪（可选）

Phase 4: 交叉增强
├── GitNexus processes → DeepWiki wiki 目录
└── DeepWiki 摘要 → GitNexus 图谱节点描述
```

### 5.3 Token 预算

```
MAX_TOKENS_PER_CALL = 40000
MAX_OUTPUT_TOKENS = 8192
SUMMARY_MAX_WORDS = 200
```

### 5.4 错误恢复

- 单步失败不中断流水线
- 失败步骤标记 skipped
- 报告标注缺失部分
- 支持手动重试

---

## 6. LLM Client

### 6.1 双协议支持

| 类 | API 格式 | 认证 | 适配模型 |
|----|---------|------|---------|
| AnthropicClient | Messages API | x-api-key | Claude 系列 |
| OpenAICompatClient | Chat Completions | Bearer token | minimax, deepseek, qwen |

### 6.2 配置方式

用户可通过表单或 JSON 配置。两种入口最终存储为同一数据结构。

### 6.3 SSL 与代理

支持三种代理模式：不走代理、系统代理、自定义代理。
SSL 证书通过文件路径配置，传递给 httpx 的 verify 参数。

---

## 7. 进程管理器

```python
TOOL_REGISTRY = {
    "gitnexus": {
        "command": ["gitnexus", "serve", "--port", "7100", "--host", "0.0.0.0"],
        "health_url": "http://localhost:7100/api/info",
    },
    "deepwiki-api": {
        "command": ["python", "-m", "api.main"],
        "health_url": "http://localhost:8091/health",
        "cwd": "{DEEPWIKI_PATH}/api",
        "env": {"TIKTOKEN_CACHE_DIR": "{DATA_DIR}/tiktoken_cache"},
    },
    "deepwiki-ui": {
        "command": ["npm", "run", "start"],
        "health_url": "http://localhost:3001",
        "cwd": "{DEEPWIKI_PATH}",
    },
}
```

---

## 8. 前端页面

| 页面 | 路径 | 功能 |
|------|------|------|
| 仪表盘 | / | 任务列表、工具状态 |
| 新建分析 | /tasks/new | 选文件夹、上传文档、选工具、命名 |
| 任务详情 | /tasks/{id} | 进度、日志、结果 |
| 结果查看 | /tasks/{id}/report | Markdown 渲染 |
| 导出 | /tasks/{id}/export | 格式选择下载 |
| 设置 | /settings | AI 配置、代理、SSL |
| 工具状态 | /tools | 进程状态、重启 |

### 8.1 要求

- 全中文界面
- Next.js App Router + Tailwind CSS
- 视觉设计由 Gemini 负责

---

## 9. 目录结构

```
codetalk/
├── backend/
│   ├── app/
│   │   ├── main.py
│   │   ├── config.py
│   │   ├── database.py
│   │   ├── api/
│   │   │   ├── tasks.py
│   │   │   ├── settings.py
│   │   │   ├── tools.py
│   │   │   └── export.py
│   │   ├── services/
│   │   │   ├── analysis_pipeline.py
│   │   │   ├── process_manager.py
│   │   │   ├── report_generator.py
│   │   │   └── export_service.py
│   │   ├── llm/
│   │   │   ├── base.py
│   │   │   ├── anthropic.py
│   │   │   ├── openai_compat.py
│   │   │   └── factory.py
│   │   ├── adapters/
│   │   │   ├── gitnexus.py
│   │   │   └── deepwiki.py
│   │   └── prompts/
│   │       ├── templates.py
│   │       └── schemas.py
│   └── requirements.txt
├── frontend/
│   ├── src/app/
│   │   ├── page.tsx
│   │   ├── tasks/new/page.tsx
│   │   ├── tasks/[id]/page.tsx
│   │   ├── tasks/[id]/report/page.tsx
│   │   ├── tasks/[id]/export/page.tsx
│   │   ├── settings/page.tsx
│   │   └── tools/page.tsx
│   ├── src/lib/
│   │   ├── api.ts
│   │   └── types.ts
│   └── src/components/
├── data/
├── docs/architecture/
└── CLAUDE.md
```

---

## 10. Sprint 规划

### Sprint 1: 骨架 + 基础 CRUD ✅
- 后端: FastAPI 入口、SQLite、任务 CRUD、设置 API
- 前端: 项目初始化、仪表盘、新建分析、设置页

### Sprint 2: 工具集成 ✅
- GitNexus adapter + 进程管理
- DeepWiki adapter + 进程管理

### Sprint 3: AI Pipeline ✅
- LLM Client 双协议（Anthropic + OpenAI 兼容）
- 分析编排引擎（AnalysisPipeline）
- Prompt 模板（中文 7 步分析法）
- 报告生成（6 份 Markdown）

### Sprint 4: 前端对接 + 导出 ✅
- 任务详情页、结果查看（含 Tab 切换）
- 导出功能
- 端到端测试

### 当前: 部署向导 + 产品化打磨 ✅
- 内置 Deployer 向导（7 步 SSE 流式部署，:9000）
- 补充部署（独立安装 DeepWiki / GitNexus，热重启 backend）
- 真实流式聊天（stream_complete，SSE 逐 token 推送）
- 报告内嵌 AI 问答面板（ReportChatPanel）
- DeepWiki 端口修正（8091/3001）、健康检查覆盖

---

## 11. 部署向导（Deployer）

独立 FastAPI 服务，端口 9000，为零基础用户提供 GUI 一键部署体验。

### 11.1 组件结构

```
deployer/
├── start.py          # 入口：启动 uvicorn + 打开浏览器
├── start.bat         # Windows 快捷启动脚本
├── server.py         # FastAPI API + SSE 流式部署
├── config_store.py   # .deploy-config.json 配置持久化
├── checks.py         # 前置端口检查（psutil）
├── deployers/
│   └── native.py     # NativeDeployer：7 步原生部署器
└── static/           # 前端静态页面（纯 HTML/JS）
    ├── index.html    # 配置表单页
    ├── deploy.html   # 部署进度页（SSE 消费）
    └── start.html    # 启动管理页
```

### 11.2 SSE 流式部署

部署全程通过 SSE（`/api/deploy/stream`）实时推送进度，每个步骤输出格式：

```json
{
  "step": "install_backend",
  "status": "running | done | error",
  "message": "正在安装依赖...",
  "progress": { "current": 2, "total": 7 }
}
```

### 11.3 配置持久化

配置存储于 `deployer/.deploy-config.json`，支持 camelCase（前端）↔ snake_case（后端）自动转换：

| 前端 key | 后端 key |
|----------|----------|
| portBackend | backend_port |
| portDeepwiki | deepwiki_api_port |
| deepwikiPath | deepwiki_path |
| installGitnexus | install_gitnexus |

### 11.4 补充部署

`POST /api/deploy/supplement/deepwiki` 调用链：
1. 从 `_deploy_state` 取旧 deployer，将其 `_processes` 复制到新 deployer
2. `supplement_deepwiki()` 检查 `_processes["backend"]` 进程句柄
3. 写入新 `.env` → 终止旧 backend → 重启 backend → 等待 5 秒
4. 健康检查 `http://localhost:{deepwiki_api_port}/health`

---

## 12. 报告问答（ReportChatPanel）

报告页面（`/tasks/{id}/report`）右侧内嵌 AI 对话面板，基于当前分析报告内容回答问题。

### 12.1 架构

```
ReportChatPanel (React)
  └── POST /api/tasks/{id}/chat   → StreamingResponse (SSE)
        ├── 加载报告文件（≤3000 chars/文件）作为 system prompt 上下文
        ├── 加载历史消息（task_chats 表）实现多轮对话
        └── llm.stream_complete() 真实流式推送
```

### 12.2 数据持久化

历史消息存储于 `task_chats` 表：

```sql
CREATE TABLE task_chats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL,
    role TEXT NOT NULL,        -- 'user' | 'assistant'
    content TEXT NOT NULL,
    created_at TEXT NOT NULL
);
```

### 12.3 流式协议

SSE 数据格式（与部署向导复用同一约定）：

```json
{ "content": "token chunk", "done": false }
{ "content": "", "done": true }
{ "content": "", "done": true, "error": "生成失败，请重试" }
```

助手回复通过 `finally` 块保证在流结束或客户端断连时写入数据库，不丢失内容。
