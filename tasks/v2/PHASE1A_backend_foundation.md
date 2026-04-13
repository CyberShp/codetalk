# Phase 1A: 后端基础框架

**前置依赖：Phase 0 完成（deepwiki API 已验证）**
**可与 Phase 1B (前端基础) 并行**
**完成后解锁：Phase 2, Phase 3**
**预估复杂度：中**

## 铁律提醒
> CodeTalks 绝不编写任何分析逻辑。Adapter 的 analyze() 只允许 HTTP 调用 + 响应格式转换。

## 目标

搭建后端骨架：项目结构、Docker Compose（仅 postgres + backend + deepwiki）、数据库 schema、ORM models、Pydantic schemas、BaseToolAdapter、API 路由桩、基础服务。

**注意：此阶段 Docker Compose 只包含 postgres + backend + deepwiki，不包含其他工具。**

## 步骤

### 1. 项目目录结构

```
codetalk/
  docker-compose.yml
  .env.example
  backend/
    Dockerfile
    requirements.txt
    alembic.ini
    alembic/
    app/
      __init__.py
      main.py
      config.py
      database.py
      models/
        __init__.py
        project.py
        repository.py
        task.py
        llm_config.py
      schemas/
        __init__.py
        project.py
        repository.py
        task.py
        llm_config.py
      api/
        __init__.py
        router.py
        projects.py
        tasks.py
        tools.py
        settings.py
        ws.py
      adapters/
        __init__.py
        base.py
      services/
        __init__.py
        task_engine.py
        git_service.py
        source_manager.py
        ai_service.py
      utils/
        __init__.py
        crypto.py
  docs/
    deepwiki-api-actual.md   # Phase 0 产出
```

### 2. Docker Compose（最小集）

```yaml
services:
  postgres:
    image: postgres:16
    ports: ["5432:5432"]
    volumes: [pg_data:/var/lib/postgresql/data]
    environment:
      POSTGRES_USER: ${POSTGRES_USER:-codetalks}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:-changeme}
      POSTGRES_DB: ${POSTGRES_DB:-codetalks}

  backend:
    build: ./backend
    ports: ["8000:8000"]
    volumes: [code_volume:/data/repos]
    depends_on: [postgres]
    env_file: .env

  deepwiki:
    image: ghcr.io/asyncfuncai/deepwiki-open:latest
    ports: ["8001:8001"]
    volumes:
      - deepwiki_data:/root/.adalflow
      - code_volume:/data/repos:ro
    environment:
      OPENAI_API_KEY: ${OPENAI_API_KEY:-}
      ANTHROPIC_API_KEY: ${ANTHROPIC_API_KEY:-}

volumes:
  pg_data:
  code_volume:
  deepwiki_data:
```

**不包含：** zoekt, codecompass, gitnexus, joern（未来加）

### 3. .env.example

```
POSTGRES_USER=codetalks
POSTGRES_PASSWORD=changeme
POSTGRES_DB=codetalks
DATABASE_URL=postgresql+asyncpg://codetalks:changeme@postgres:5432/codetalks

OPENAI_API_KEY=
ANTHROPIC_API_KEY=
OLLAMA_BASE_URL=http://host.docker.internal:11434

FERNET_KEY=
```

### 4. 后端基础文件

**`backend/requirements.txt`:**
```
fastapi==0.115.*
uvicorn[standard]==0.34.*
sqlalchemy==2.0.*
alembic==1.14.*
asyncpg==0.30.*
psycopg2-binary==2.9.*
pydantic==2.10.*
pydantic-settings==2.*
httpx==0.28.*
websockets==14.*
python-multipart==0.0.*
cryptography==44.*
```

**`backend/app/config.py`:** pydantic-settings 读取环境变量
**`backend/app/database.py`:** SQLAlchemy async engine + sessionmaker
**`backend/app/main.py`:** FastAPI app, CORS, include routers, startup DB check

### 5. 数据库 Schema (Alembic Migration)

与 v1 计划中 Phase 1 定义的 6 张表相同：
- `projects` — 项目
- `repositories` — 仓库（source_type: git_url/local_path/zip_upload）
- `analysis_tasks` — 分析任务（task_type: full_repo/file_paths/mr_diff）
- `tool_runs` — 工具运行记录
- `task_logs` — 任务日志
- `llm_configs` — LLM 配置

详细 DDL 见 `tasks/PHASE1_infrastructure.md` 第 5 节。

### 6. SQLAlchemy Models

为每张表创建 ORM model。所有 model 用 UUID 主键。

### 7. Pydantic Schemas

Request/Response schemas：
- `ProjectCreate`, `ProjectUpdate`, `ProjectResponse`
- `RepositoryCreate`, `RepositoryResponse`
- `TaskCreate`, `TaskResponse`, `TaskDetailResponse`
- `ToolRunResponse`
- `LLMConfigCreate`, `LLMConfigResponse`（api_key 不返回明文）

### 8. BaseToolAdapter 抽象类

与 v1 计划 Phase 2A 第 3 节相同。这是核心接口，不要改动。

```python
class BaseToolAdapter(ABC):
    @abstractmethod
    def name(self) -> str: ...
    @abstractmethod
    def capabilities(self) -> list[ToolCapability]: ...
    @abstractmethod
    async def health_check(self) -> ToolHealth: ...
    @abstractmethod
    async def prepare(self, request: AnalysisRequest) -> None: ...
    @abstractmethod
    async def analyze(self, request: AnalysisRequest) -> UnifiedResult: ...
    @abstractmethod
    async def stream_logs(self, run_id: str) -> AsyncIterator[str]: ...
```

### 9. Adapter 注册表

```python
TOOL_REGISTRY: dict[str, BaseToolAdapter] = {}
def register_adapter(adapter: BaseToolAdapter): ...
def get_adapter(name: str) -> BaseToolAdapter: ...
```

### 10. API 路由桩

所有路由先返回空数据或 501 占位：
- `GET/POST /api/projects` — 桩
- `POST /api/tasks` — 桩
- `GET /api/tools` — 返回已注册 adapter 列表
- `GET/POST /api/settings/llm` — 桩
- `WS /ws/tasks/{id}/logs` — 桩

### 11. 基础服务桩

- `source_manager.py` — `resolve_source()` 方法桩
- `git_service.py` — `clone_repo()` 方法桩
- `task_engine.py` — `run_task()` 方法桩
- `ai_service.py` — `summarize_results()` 方法桩

## 验收标准

- [ ] `docker-compose up postgres backend` 启动成功
- [ ] `alembic upgrade head` 创建所有 6 张表
- [ ] `GET http://localhost:8000/api/tools` 返回空 adapter 列表
- [ ] `GET http://localhost:8000/api/projects` 返回空列表
- [ ] `docker-compose up deepwiki` 正常启动
- [ ] Backend Dockerfile 构建成功
