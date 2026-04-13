# Phase 3D: deepwiki-open Adapter

**前置依赖：Phase 2A 完成（BaseToolAdapter 已定义）**
**可与其他 Phase 3 任务并行**
**注意：此 adapter 依赖 LLM API key，AI 关闭时跳过**

## 任务目标

实现 deepwiki-open 文档生成工具的 adapter。

## deepwiki-open 概述

AI 驱动的代码库 Wiki 生成器，自动创建结构化文档，包含 Mermaid 图表、RAG 问答功能。

GitHub: https://github.com/AsyncFuncAI/deepwiki-open

## 步骤

### 1. Docker 配置

deepwiki-open 有官方镜像，需要传入 LLM API keys：

```yaml
deepwiki:
  image: ghcr.io/asyncfuncai/deepwiki-open:latest
  ports:
    - "8001:8001"
    - "3001:3001"  # frontend (optional)
  volumes:
    - deepwiki_data:/root/.adalflow
  environment:
    - OPENAI_API_KEY=${OPENAI_API_KEY}
    - ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}
```

### 2. 研究 deepwiki-open API

先研究其源码了解 API 端点：
```bash
git clone https://github.com/AsyncFuncAI/deepwiki-open.git /tmp/deepwiki-research
```

已知端点（需确认）：
- `POST /api/wiki/generate` — 生成 Wiki
- `POST /chat/completions/stream` — RAG 问答（流式）
- `GET /api/wiki_cache` — 获取缓存的 Wiki
- `POST /local_repo/structure` — 处理本地仓库结构
- `GET /health` — 健康检查

### 3. 实现 Adapter (`backend/app/adapters/deepwiki.py`)

```python
class DeepwikiAdapter(BaseToolAdapter):
    def __init__(self, base_url: str = "http://deepwiki:8001"):
        self.base_url = base_url
        self.client = httpx.AsyncClient(base_url=base_url, timeout=300)  # 生成可能很慢

    def name(self) -> str:
        return "deepwiki"

    def capabilities(self) -> List[ToolCapability]:
        return [
            ToolCapability.DOCUMENTATION,
            ToolCapability.ARCHITECTURE_DIAGRAM,
        ]

    async def health_check(self) -> ToolHealth:
        # GET /health
        ...

    async def prepare(self, request: AnalysisRequest) -> None:
        # POST /local_repo/structure 处理仓库结构
        # 传入仓库本地路径
        ...

    async def analyze(self, request: AnalysisRequest) -> UnifiedResult:
        # 1. 调用 wiki 生成端点，获取文档和 Mermaid 图表
        # 2. 如果有 target_files，可能只生成相关文件的文档
        # 3. 返回：
        #    - data: 生成的文档内容（Markdown 格式）
        #    - diagrams: Mermaid 图表列表 [{type: "mermaid", content: "..."}]
        #
        # 所有文档生成由 deepwiki 的 RAG pipeline 完成
        # adapter 只做 HTTP 调用
        ...

    async def stream_logs(self, run_id: str) -> AsyncIterator[str]:
        # 监控 wiki 生成的流式响应
        ...
```

### 4. LLM 配置传递

deepwiki 需要 LLM API key。有两种方式：
1. 环境变量（docker-compose 中配置）— 简单但不灵活
2. 通过 API 传递 — 需要研究 deepwiki 是否支持运行时配置 LLM

当前方案：通过 docker-compose 环境变量传入。当用户在 codetalks settings 中更改 LLM provider 时，可能需要重启 deepwiki 容器。

### 5. AI 开关逻辑

在 Task Engine 中（不在 adapter 中）处理：
- 如果 `task.ai_enabled == False`，Task Engine 不调用 deepwiki adapter
- deepwiki adapter 本身不关心 AI 开关，它总是需要 LLM 来工作

## 验收标准

- [ ] deepwiki 容器正常启动（需要有效的 LLM API key）
- [ ] prepare() 成功处理仓库结构
- [ ] analyze() 通过 deepwiki API 生成文档和图表
- [ ] Mermaid 图表正确提取到 diagrams 列表
- [ ] 移除容器后 adapter 报错
- [ ] analyze() 中无任何文档生成逻辑
