# Phase 2: deepwiki-open Adapter 实现

**前置依赖：Phase 1A 完成（BaseToolAdapter 已定义）**
**依赖 Phase 0 的 `docs/deepwiki-api-actual.md`**
**完成后解锁：Phase 4**
**预估复杂度：中**

## 铁律提醒
> CodeTalks 绝不编写任何分析逻辑。
> deepwiki adapter 的 analyze() 只允许：(a) HTTP 调用 deepwiki API (b) 响应格式转换
> 所有文档生成、RAG、Mermaid 图表生成由 deepwiki 引擎完成

## 目标

基于 Phase 0 验证的真实 API，实现 deepwiki adapter。同时完善 Task Engine 使其能真正运行分析任务。

## 重要前提

**先读 `docs/deepwiki-api-actual.md`**，使用 Phase 0 验证的真实端点和格式，不要用文档假设。

## Phase 0 发现（影响设计）

**deepwiki 没有 `POST /api/wiki/generate` 端点。** Wiki 生成由前端编排。

可用端点：
- `GET /health` → 健康检查
- `GET /local_repo/structure?path=...` → 仓库文件树 + README
- `POST /chat/completions/stream` → **核心：RAG Q&A，流式返回 Markdown**
- `GET /api/wiki_cache` → 读取已缓存的 Wiki
- `POST /export/wiki` → 导出 Wiki
- `GET /models/config` → 可用 LLM Provider/Model 列表

**Adapter 策略：使用 `/chat/completions/stream` 做 RAG 分析（方案 A）**

## 步骤

### 1. 实现 DeepwikiAdapter (`backend/app/adapters/deepwiki.py`)

```python
import re
import httpx
from .base import BaseToolAdapter, ToolCapability, ToolHealth, AnalysisRequest, UnifiedResult

class DeepwikiAdapter(BaseToolAdapter):
    def __init__(self, base_url: str = "http://deepwiki:8001"):
        self.base_url = base_url
        self.client = httpx.AsyncClient(base_url=base_url, timeout=300)

    def name(self) -> str:
        return "deepwiki"

    def capabilities(self) -> list[ToolCapability]:
        return [ToolCapability.DOCUMENTATION, ToolCapability.ARCHITECTURE_DIAGRAM]

    async def health_check(self) -> ToolHealth:
        try:
            resp = await self.client.get("/health")
            data = resp.json()
            return ToolHealth(
                is_healthy=data.get("status") == "healthy",
                container_status="running",
            )
        except Exception:
            return ToolHealth(is_healthy=False, container_status="error")

    async def prepare(self, request: AnalysisRequest) -> None:
        # 验证仓库路径在 deepwiki 容器中可访问
        resp = await self.client.get(
            "/local_repo/structure",
            params={"path": request.repo_local_path}
        )
        if resp.status_code != 200:
            raise RuntimeError(f"deepwiki cannot access repo at {request.repo_local_path}")
        # prepare 的产出：确认仓库可读，缓存文件树供后续使用
        self._file_tree = resp.json().get("file_tree", "")
        self._readme = resp.json().get("readme", "")

    async def analyze(self, request: AnalysisRequest) -> UnifiedResult:
        # 构建分析 prompt
        # 注意：所有文档生成由 deepwiki 的 RAG pipeline 完成
        # 这里只构造请求 → HTTP 调用 → 响应格式转换
        target_desc = "the entire repository"
        if request.target_files:
            target_desc = f"these files: {', '.join(request.target_files)}"

        prompt = (
            f"Analyze {target_desc} and generate comprehensive documentation. "
            f"Include: architecture overview, key components, data flow, "
            f"and Mermaid diagrams where appropriate."
        )

        # 调用 deepwiki RAG chat endpoint (SSE streaming)
        # 使用 repo_url 格式：deepwiki 需要 owner/repo 格式或本地路径
        chat_request = {
            "repo_url": request.repo_local_path,  # deepwiki 会处理本地路径
            "messages": [{"role": "user", "content": prompt}],
            "provider": request.options.get("provider", "openai"),
            "model": request.options.get("model"),
            "language": request.options.get("language", "en"),
        }

        if request.target_files:
            chat_request["included_files"] = ",".join(request.target_files)

        # 收集流式响应
        full_content = ""
        async with self.client.stream(
            "POST", "/chat/completions/stream",
            json=chat_request,
            timeout=300
        ) as response:
            async for chunk in response.aiter_text():
                full_content += chunk

        # 从 Markdown 中提取 Mermaid 图表
        # 注意：这不是分析逻辑，是响应格式转换
        diagrams = _extract_mermaid_blocks(full_content)

        return UnifiedResult(
            tool_name="deepwiki",
            capability=ToolCapability.DOCUMENTATION,
            data={"markdown": full_content, "file_tree": self._file_tree},
            raw_output=full_content,
            diagrams=diagrams,
            metadata={"provider": chat_request.get("provider"), "model": chat_request.get("model")},
        )

    async def stream_logs(self, run_id: str) -> AsyncIterator[str]:
        yield "deepwiki: analysis started"
        yield "deepwiki: generating documentation via RAG..."
        # 实际流式日志可以在 analyze() 中通过回调推送
        yield "deepwiki: completed"


def _extract_mermaid_blocks(markdown: str) -> list[dict]:
    """从 Markdown 中提取 ```mermaid 代码块。这是响应格式转换，不是分析逻辑。"""
    pattern = r'```mermaid\s*\n(.*?)\n```'
    blocks = re.findall(pattern, markdown, re.DOTALL)
    return [{"type": "mermaid", "content": block.strip()} for block in blocks]
```

### 2. 注册 Adapter

```python
# backend/app/adapters/__init__.py
from .deepwiki import DeepwikiAdapter
register_adapter(DeepwikiAdapter())
```

### 3. 实现 Task Engine (`backend/app/services/task_engine.py`)

将 v1 计划的 Task Engine 伪代码变为真实代码：

```python
async def run_task(task_id: UUID, db: AsyncSession):
    task = await get_task(db, task_id)
    await update_status(db, task_id, "running")

    try:
        # 1. 解析代码来源
        repo = await get_repo(db, task.repository_id)
        local_path = await source_manager.resolve_source(repo)

        # 2. 构建 AnalysisRequest
        request = AnalysisRequest(
            repo_local_path=local_path,
            target_files=task.target_spec.get("files"),
            task_type=task.task_type,
        )

        # 3. 获取 adapter（MVP 只有 deepwiki）
        adapters = []
        for name in task.tools:
            if name == "deepwiki" and not task.ai_enabled:
                continue  # deepwiki 需要 AI
            adapters.append(get_adapter(name))

        # 4. 创建 tool_run 记录
        tool_runs = await create_tool_runs(db, task_id, adapters)

        # 5. 逐个 prepare + analyze
        results = []
        for adapter, run in zip(adapters, tool_runs):
            try:
                await adapter.prepare(request)
                result = await adapter.analyze(request)
                await update_tool_run(db, run.id, "completed", result)
                results.append(result)
            except Exception as e:
                await update_tool_run(db, run.id, "failed", error=str(e))

        # 6. AI 总结（如果开启且有结果）
        if task.ai_enabled and results:
            llm_config = await get_default_llm(db)
            if llm_config:
                summary = await ai_service.summarize_results(results, llm_config)
                await save_summary(db, task_id, summary)

        # 7. 完成
        await update_status(db, task_id, "completed")

    except Exception as e:
        await update_status(db, task_id, "failed", error=str(e))
```

### 4. 实现 Source Manager 核心方法

```python
async def resolve_source(repo: Repository) -> str:
    if repo.source_type == "git_url":
        return await clone_or_pull(repo)
    elif repo.source_type == "local_path":
        validate_path(repo.source_uri)
        return repo.source_uri
    elif repo.source_type == "zip_upload":
        return repo.local_path  # Phase 3 处理上传
```

### 5. 实现 AI Service 基础

```python
async def summarize_results(results: list[UnifiedResult], config: LLMConfig) -> str:
    # 用 httpx 直接调用 LLM API
    # 支持 OpenAI / Anthropic / Ollama
    # 将 UnifiedResult 转为 prompt → 调用 API → 返回文本
    ...
```

## 验收标准

- [ ] `GET /api/tools` 返回 deepwiki adapter（状态=online）
- [ ] DeepwikiAdapter.health_check() 正确检测 deepwiki 容器状态
- [ ] DeepwikiAdapter.analyze() 通过 HTTP 调用 deepwiki 并返回 UnifiedResult
- [ ] UnifiedResult.data 包含 Markdown 文档内容
- [ ] UnifiedResult.diagrams 包含 Mermaid 图表列表
- [ ] 移除 deepwiki 容器后，health_check 报 unhealthy，analyze 报连接错误
- [ ] **analyze() 中零行文档生成逻辑 — 缅因猫 review 确认**
- [ ] Task Engine 能完整执行一个 deepwiki 分析任务
