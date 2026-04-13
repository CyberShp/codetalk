# Phase 2A: 后端核心框架

**前置依赖：Phase 1 完成**
**可与 Phase 2B (前端骨架) 并行**
**完成后解锁：Phase 3 所有 Adapter 任务**

## 任务目标

实现后端的核心框架：BaseToolAdapter 抽象类、SQLAlchemy models、Pydantic schemas、所有 API 路由骨架、Task Engine 编排器、Git/Source 服务。

## 步骤

### 1. SQLAlchemy Models (`backend/app/models/`)

为 Phase 1 创建的每张表编写 SQLAlchemy ORM model：
- `project.py` → Project model
- `repository.py` → Repository model
- `task.py` → AnalysisTask, ToolRun, TaskLog models
- `llm_config.py` → LLMConfig model

所有 model 用 UUID 主键，JSONB 字段用 `sqlalchemy.dialects.postgresql.JSONB`。

### 2. Pydantic Schemas (`backend/app/schemas/`)

为每个 model 编写 Request/Response schema：
- `ProjectCreate`, `ProjectUpdate`, `ProjectResponse`
- `RepositoryCreate` (包含 source_type 枚举: git_url/local_path/zip_upload), `RepositoryResponse`
- `TaskCreate` (包含 task_type 枚举: full_repo/file_paths/mr_diff, tools 列表, ai_enabled), `TaskResponse`
- `ToolRunResponse`
- `LLMConfigCreate`, `LLMConfigResponse` (api_key 不返回明文)

### 3. BaseToolAdapter 抽象类 (`backend/app/adapters/base.py`)

这是整个项目最关键的文件。定义：

```python
from abc import ABC, abstractmethod
from typing import List, Optional, AsyncIterator
from dataclasses import dataclass, field
from enum import Enum

class ToolCapability(Enum):
    CODE_SEARCH = "code_search"
    CALL_GRAPH = "call_graph"
    DEPENDENCY_GRAPH = "dependency_graph"
    TAINT_ANALYSIS = "taint_analysis"
    SECURITY_SCAN = "security_scan"
    DOCUMENTATION = "documentation"
    KNOWLEDGE_GRAPH = "knowledge_graph"
    ARCHITECTURE_DIAGRAM = "architecture_diagram"
    POINTER_ANALYSIS = "pointer_analysis"
    AST_ANALYSIS = "ast_analysis"

@dataclass
class ToolHealth:
    is_healthy: bool
    container_status: str
    version: Optional[str] = None
    last_check: str = ""

@dataclass
class AnalysisRequest:
    repo_local_path: str           # /data/repos/{name}
    target_files: Optional[List[str]] = None  # None = full repo
    task_type: str = "full_repo"
    options: dict = field(default_factory=dict)

@dataclass
class UnifiedResult:
    tool_name: str
    capability: ToolCapability
    data: dict              # 结构化结果
    raw_output: str = ""    # 工具原始输出
    diagrams: List[dict] = field(default_factory=list)  # [{type, content}]
    metadata: dict = field(default_factory=dict)

class BaseToolAdapter(ABC):
    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
    def capabilities(self) -> List[ToolCapability]: ...

    @abstractmethod
    async def health_check(self) -> ToolHealth: ...

    @abstractmethod
    async def prepare(self, request: AnalysisRequest) -> None:
        """预处理：索引仓库、导入 CPG 等"""
        ...

    @abstractmethod
    async def analyze(self, request: AnalysisRequest) -> UnifiedResult:
        """执行分析。只允许 HTTP 调用 + 响应转换，禁止任何分析逻辑。"""
        ...

    @abstractmethod
    async def stream_logs(self, run_id: str) -> AsyncIterator[str]:
        """实时流式输出日志"""
        ...

    async def cleanup(self, request: AnalysisRequest) -> None:
        """可选的清理操作"""
        pass
```

### 4. Adapter 注册表 (`backend/app/adapters/__init__.py`)

```python
from .base import BaseToolAdapter

# Phase 3 中各 adapter 实现后注册到这里
TOOL_REGISTRY: dict[str, BaseToolAdapter] = {}

def register_adapter(adapter: BaseToolAdapter):
    TOOL_REGISTRY[adapter.name()] = adapter

def get_adapter(name: str) -> BaseToolAdapter:
    return TOOL_REGISTRY[name]

def get_all_adapters() -> list[BaseToolAdapter]:
    return list(TOOL_REGISTRY.values())
```

### 5. Git 服务 (`backend/app/services/git_service.py`)

- `clone_repo(url: str, branch: str, target_dir: str)` — git clone 到 code_volume
- `pull_repo(local_path: str)` — git pull 更新
- `get_mr_diff(repo_url: str, mr_id: str) -> list[str]` — 通过 GitHub/GitLab API 获取 MR 变更文件列表
- `get_diff_content(local_path: str, base_ref: str, head_ref: str) -> str` — git diff 内容

### 6. Source Manager (`backend/app/services/source_manager.py`)

- `resolve_source(repo: Repository) -> str` — 根据 source_type 返回本地路径
  - `git_url`: 克隆到 `/data/repos/{project_id}/{repo_name}/`
  - `local_path`: 验证路径存在，返回原路径
  - `zip_upload`: 保存到 `/data/repos/uploads/`，解压，返回解压路径

### 7. AI 服务 (`backend/app/services/ai_service.py`)

- `summarize_results(results: list[UnifiedResult], llm_config: LLMConfig) -> str`
  - 当 AI 开启时，用配置的 LLM 对工具结果做总结
  - 支持 OpenAI、Anthropic、Ollama 三种 provider
  - 用 httpx 直接调用各 provider API，不引入 SDK 依赖
- AI 关闭时此服务不被调用

### 8. Task Engine (`backend/app/services/task_engine.py`)

核心编排逻辑：

```python
async def run_task(task: AnalysisTask):
    # 1. 解析代码来源
    repo = get_repo(task.repository_id)
    local_path = await source_manager.resolve_source(repo)

    # 2. 构建 AnalysisRequest
    request = AnalysisRequest(
        repo_local_path=local_path,
        target_files=task.target_spec.get("files"),
        task_type=task.task_type,
    )

    # 3. 如果是 MR 类型，获取变更文件
    if task.task_type == "mr_diff":
        changed_files = await git_service.get_mr_diff(
            task.target_spec["mr_url"], task.target_spec["mr_id"]
        )
        request.target_files = changed_files

    # 4. 获取要使用的 adapter
    adapters = [get_adapter(name) for name in task.tools]

    # 5. 并行 prepare
    await asyncio.gather(*[a.prepare(request) for a in adapters])

    # 6. 并行 analyze
    results = await asyncio.gather(*[a.analyze(request) for a in adapters])

    # 7. 存储结果到 tool_runs
    for result in results:
        save_tool_run(task.id, result)

    # 8. 可选 AI 总结
    if task.ai_enabled:
        summary = await ai_service.summarize_results(results, get_default_llm())
        save_ai_summary(task.id, summary)

    # 9. 更新任务状态
    update_task_status(task.id, "completed")
```

### 9. API 路由 (`backend/app/api/`)

**`projects.py`:**
- `GET /api/projects` — 列表
- `POST /api/projects` — 创建
- `GET /api/projects/{id}` — 详情
- `PUT /api/projects/{id}` — 更新
- `DELETE /api/projects/{id}` — 删除
- `GET /api/projects/{id}/repos` — 项目下仓库列表
- `POST /api/projects/{id}/repos` — 添加仓库

**`tasks.py`:**
- `POST /api/tasks` — 创建分析任务
- `GET /api/tasks` — 列表（支持 status/project_id 过滤）
- `GET /api/tasks/{id}` — 详情 + tool_runs
- `POST /api/tasks/{id}/cancel` — 取消
- `POST /api/tasks/{id}/retry` — 重试
- `GET /api/tasks/{id}/results` — 所有结果
- `GET /api/tasks/{id}/results/{tool}` — 特定工具结果

**`tools.py`:**
- `GET /api/tools` — 列表 + 健康状态
- `GET /api/tools/{name}/health` — 单个工具健康检查

**`settings.py`:**
- `GET /api/settings/llm` — LLM 配置
- `POST /api/settings/llm` — 保存 LLM 配置

**`ws.py`:**
- `WS /ws/tasks/{id}/logs` — WebSocket 实时日志流

**`router.py`:**
- 聚合所有子路由到顶级 router

## 验收标准

- [ ] 所有 API 路由可访问（返回空数据或占位响应）
- [ ] BaseToolAdapter 接口完整定义
- [ ] TaskEngine 逻辑完整（虽然还没有真实 adapter，但骨架可运行）
- [ ] git_service 能克隆仓库到 code_volume
- [ ] source_manager 支持三种来源类型
