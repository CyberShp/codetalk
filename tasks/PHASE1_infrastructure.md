# Phase 1: 基础设施搭建

**前置依赖：无（第一个执行的任务）**
**完成后解锁：Phase 2 的所有任务**

## 任务目标

搭建整个项目的基础骨架：目录结构、Docker Compose、数据库 schema、后端入口、前端初始化。

## 步骤

### 1. 创建项目目录结构

按照 CLAUDE.md 中的目录结构创建所有目录和 `__init__.py` 文件。

### 2. 编写 `docker-compose.yml`

定义以下服务：

| 服务 | 镜像 | 端口 | 卷 |
|------|------|------|-----|
| `postgres` | `postgres:16` | 5432 | `pg_data:/var/lib/postgresql/data` |
| `backend` | build: `./backend` | 8000 | `code_volume:/data/repos` |
| `frontend` | build: `./frontend` | 3000 | — |
| `zoekt` | `ghcr.io/sourcegraph/zoekt-webserver` | 6070 | `zoekt_index:/data/index`, `code_volume:/data/repos:ro` |
| `deepwiki` | `ghcr.io/asyncfuncai/deepwiki-open` | 8001 | `deepwiki_data:/root/.adalflow` |
| `joern` | build: `./docker/joern` | 8080 | `code_volume:/data/repos:ro` |
| `codecompass` | build: `./docker/codecompass` | 6251 | `code_volume:/data/repos:ro` |
| `gitnexus` | build: `./docker/gitnexus` | 7100 | `code_volume:/data/repos:ro` |

关键：
- 共享 `code_volume` 挂载到所有工具容器（只读），后端可读写
- 所有工具服务依赖 `backend`（network 互通）
- 创建 `.env.example` 包含：POSTGRES_PASSWORD, OPENAI_API_KEY, ANTHROPIC_API_KEY 等

### 3. 编写 `.env.example`

```
POSTGRES_USER=codetalks
POSTGRES_PASSWORD=changeme
POSTGRES_DB=codetalks
DATABASE_URL=postgresql://codetalks:changeme@postgres:5432/codetalks

# LLM (optional)
OPENAI_API_KEY=
ANTHROPIC_API_KEY=
OLLAMA_BASE_URL=http://host.docker.internal:11434
```

### 4. 后端基础

**`backend/requirements.txt`:**
```
fastapi==0.115.*
uvicorn[standard]==0.34.*
sqlalchemy==2.0.*
alembic==1.14.*
asyncpg==0.30.*
psycopg2-binary==2.9.*
pydantic==2.10.*
httpx==0.28.*
websockets==14.*
python-multipart==0.0.*
cryptography==44.*
docker==7.*
```

**`backend/Dockerfile`:**
基于 `python:3.12-slim`，安装依赖，运行 uvicorn

**`backend/app/config.py`:**
用 pydantic-settings 从环境变量读取配置（DATABASE_URL 等）

**`backend/app/database.py`:**
SQLAlchemy async engine + sessionmaker

**`backend/app/main.py`:**
FastAPI 实例，挂载 CORS，include routers，启动时检查数据库连接

### 5. 数据库 Migration

用 Alembic 创建初始 migration，包含以下表：

```sql
-- projects
CREATE TABLE projects (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(255) NOT NULL,
    description TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- repositories
CREATE TABLE repositories (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id UUID REFERENCES projects(id) ON DELETE CASCADE,
    name VARCHAR(255) NOT NULL,
    source_type VARCHAR(20) NOT NULL,  -- 'git_url', 'local_path', 'zip_upload'
    source_uri TEXT NOT NULL,
    local_path TEXT,
    branch VARCHAR(255) DEFAULT 'main',
    last_indexed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- analysis_tasks
CREATE TABLE analysis_tasks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    repository_id UUID REFERENCES repositories(id) ON DELETE CASCADE,
    task_type VARCHAR(30) NOT NULL,    -- 'full_repo', 'file_paths', 'mr_diff'
    status VARCHAR(20) DEFAULT 'pending',
    target_spec JSONB NOT NULL,
    tools JSONB NOT NULL,
    ai_enabled BOOLEAN DEFAULT FALSE,
    progress INTEGER DEFAULT 0,
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- tool_runs
CREATE TABLE tool_runs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    task_id UUID REFERENCES analysis_tasks(id) ON DELETE CASCADE,
    tool_name VARCHAR(50) NOT NULL,
    status VARCHAR(20) DEFAULT 'pending',
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    result JSONB,
    error TEXT,
    log_path TEXT
);

-- task_logs
CREATE TABLE task_logs (
    id BIGSERIAL PRIMARY KEY,
    tool_run_id UUID REFERENCES tool_runs(id) ON DELETE CASCADE,
    timestamp TIMESTAMPTZ DEFAULT NOW(),
    level VARCHAR(10),
    message TEXT
);

-- llm_configs
CREATE TABLE llm_configs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    provider VARCHAR(50) NOT NULL,
    model_name VARCHAR(100) NOT NULL,
    api_key_encrypted TEXT,
    base_url TEXT,
    is_default BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
```

### 6. 前端初始化

```bash
cd /Volumes/Media/codetalk
npx create-next-app@latest frontend --typescript --tailwind --eslint --app --src-dir
```

然后将 Kinetic Shadow Framework 的色彩体系移植到 `tailwind.config.ts`。参考 `/Users/shepard/Downloads/stitch_graybox_ui/code.html` 中的 Tailwind 配置。

关键颜色 token：
- surface: #10141A, surface-container-low: #181C22, surface-container: #1C2026, surface-container-high: #262A31
- primary: #A4E6FF, on-primary: #003544, primary-container: #00687F
- secondary: #ECFFE3, tertiary: #FFD1CD
- on-surface: #DFE2EB, on-surface-variant: #BFC5D0

字体：Space Grotesk, Inter, JetBrains Mono (Google Fonts CDN)

### 7. 工具 Dockerfile 占位

在 `docker/joern/`, `docker/codecompass/`, `docker/gitnexus/` 各创建空 Dockerfile 占位，Phase 3 实现时填充。

## 验收标准

- [ ] `docker-compose up postgres backend` 能启动，后端连接数据库成功
- [ ] `alembic upgrade head` 成功创建所有表
- [ ] `docker-compose up frontend` 能启动，访问 localhost:3000 看到空白 Next.js 页面
- [ ] Tailwind config 包含所有 Kinetic Shadow Framework 色彩 token
