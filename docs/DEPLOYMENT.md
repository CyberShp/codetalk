---
feature_ids:
  - deployment
  - workbench-v2
topics:
  - native-deployment
  - migration
  - rollback
doc_kind: operations-guide
created: 2026-07-14
---

# CodeTalk Lightweight — 部署文档

> 版本: 2.1 | 分支: feat | Sprint 4

## 1. 系统要求

| 组件 | 版本要求 |
|------|---------|
| Python | 3.11+ |
| Node.js | 18+ (推荐 20 LTS) |
| Git | 2.x |
| GitNexus | 最新版（放入 PATH 或配置绝对路径） |

## 2. 端口规划

| 服务 | 端口 | 说明 |
|------|------|------|
| 前端 (Next.js) | 3003 | 用户界面 |
| 后端 API (FastAPI) | 3004 | REST API |
| GitNexus | 7100 | 代码图谱服务 |

> **本地默认端口**: 3003 / 3004。测试或多实例运行需要换端口时，请显式设置 `CODETALK_FRONTEND_PORT` / `CODETALK_BACKEND_PORT`。

## 2.5 一键部署向导

对于全新安装，推荐使用内置的**部署向导**（Deployer），无需手动配置：

```bash
cd deployer
# Windows
start.bat
# Linux/macOS
./start.sh
```

浏览器打开 http://localhost:9000，向导将自动完成当前本机部署流程：

| 步骤 | 内容 |
|------|------|
| 1 | 运行环境检查（Python 3.11+、Node.js 18+、Git） |
| 2 | 后端依赖安装（创建 .venv311 虚拟环境） |
| 3 | 前端依赖安装（npm install） |
| 4 | GitNexus 安装（可选，可关闭） |
| 5 | 配置文件生成（自动写入 backend/.env） |
| 6 | 服务启动（backend、frontend，可选 GitNexus / CGC） |
| 7 | 健康检查（等待所有服务就绪） |

部署向导支持自定义：
- **端口配置**：前端、后端、GitNexus 端口
- **工作目录**：代码仓库存储路径
- **临时文件目录**：统一承载 Agent、模型、构建和测试临时文件；默认跟随工作目录的 `tmp` 子目录
- **组件选择**：是否安装 GitNexus / CGC 等增强组件

例如工作目录填写 `/Volumes/Media/codetalk-runtime` 后，临时文件目录会自动填写 `/Volumes/Media/codetalk-runtime/tmp`。部署器会把该路径以 `CODETALK_TEMP_DIR`、`TEMP`、`TMP` 和 `TMPDIR` 传给 backend、frontend、GitNexus、CGC 以及安装子进程，避免大体积 Agent 运行数据写入系统盘。

当前产品不再部署或管理旧 Wiki 组件；如果历史配置文件里仍有旧 Wiki 路径、端口或环境变量，部署器会在生成配置时清理。

部署完成后，页面自动跳转至「启动管理页」，可查看服务状态并管理服务生命周期。

## 3. 快速部署

### 3.1 克隆代码

```bash
git clone https://github.com/CyberShp/codetalk.git
cd codetalk
git checkout feat
```

### 3.2 后端部署

```bash
cd backend

# 创建虚拟环境
python -m venv .venv311
# Windows:
.venv311\Scripts\activate
# Linux/macOS:
# source .venv311/bin/activate

# 安装依赖
pip install -r requirements.txt

# 创建数据目录
mkdir -p data/outputs data/tiktoken_cache

# 配置环境变量（可选，使用 .env 文件）
cp .env.example .env
# 编辑 .env 设置各项参数
```

### 3.3 前端部署

```bash
cd frontend

# 安装依赖
npm install

# 创建环境文件（可选）
echo "NEXT_PUBLIC_API_URL=http://localhost:3004" > .env.local
```

### 3.4 启动服务

**后端:**
```bash
cd backend
.venv311\Scripts\activate     # Windows
uvicorn app.main:app --host 0.0.0.0 --port 3004 --reload
```

> **Workbench V2 单进程约束：** 当前版本只支持一个后端应用进程访问同一 `DATA_DIR`。
> 不要添加 `--workers`，也不要让多个 Uvicorn/Gunicorn 实例共享该目录。迁移备份和
> Attempt 编号使用进程内锁；多进程部署必须等数据库级租约/事务协调实现后再启用。
> `--reload` 只用于本地开发，生产环境去掉该参数并保持一个 worker。

**前端:**
```bash
cd frontend
npm run dev
```

访问 http://localhost:3003 即可使用。

## 4. 环境变量

### 4.1 后端 (.env)

```env
# 数据存储
DATA_DIR=data
SQLITE_DB=data/codetalk.db

# 运行时临时目录；未设置时默认使用 <DATA_DIR>/tmp
CODETALK_TEMP_DIR=/Volumes/Media/codetalk-runtime/tmp

# Workbench V2 默认开启；仅在一个发布周期内用于旧入口回滚
WORKBENCH_V2_ENABLED=true

# 工具地址
GITNEXUS_BASE_URL=http://localhost:7100

# 工具管理
GITNEXUS_PORT=7100
GITNEXUS_BIN=gitnexus            # GitNexus 二进制路径
GITNEXUS_INDEX_QUEUE_MAX=8        # 最多等待的索引任务数（1-100）
TOOL_HEALTH_INTERVAL=30          # 健康检查间隔(秒)

# CORS（内网部署需添加客户端 IP）
CORS_ORIGINS=http://localhost:3003,http://127.0.0.1:3003
```

### 4.2 Workbench V2 迁移、备份与回滚

Workbench V2 默认启用。后端首次打开已有 Workbench SQLite 并执行 V2 迁移前，使用 SQLite Backup API 在数据库同目录生成一次经过 `PRAGMA quick_check` 验证的备份：

```text
DATA_DIR/workbench/workflows.pre-workbench-v2.<UTC timestamp>.bak
```

迁移是附加且幂等的：旧 `workflow_definitions` 表、旧运行目录、事件、artifact、语义和证据数据不会被删除。备份或迁移失败会阻止启动，不会静默带着半迁移数据继续运行。

上线前检查：

```bash
curl http://127.0.0.1:3004/health
curl http://127.0.0.1:3004/api/workbench/release
ls -lh "$DATA_DIR/workbench"/workflows.pre-workbench-v2.*.bak
```

默认发布状态应为：

```json
{"workbench_v2_enabled":true}
```

需要立即恢复旧页面时，不需要恢复数据库。修改后端环境并重启：

```env
WORKBENCH_V2_ENABLED=false
```

此时 `/workbench`、`/workbench/designer` 和 `/workbench/semantic` 渲染旧 Workbench；旧 API 和数据继续原位读取。恢复 V2 时改回 `true` 并重启后端。

只有数据库文件本身损坏且经过维护窗口审批时，才考虑从 `.bak` 恢复。恢复前必须停止后端、再备份当前数据库及 `-wal`/`-shm` 文件；不要在运行中的 SQLite 上用普通文件复制覆盖。功能开关回滚不需要执行此破坏性操作。

### 4.2 前端 (.env.local)

```env
NEXT_PUBLIC_API_URL=http://localhost:3004
```

> 内网部署时，将 `localhost` 替换为服务器 IP，例如:
> `NEXT_PUBLIC_API_URL=http://192.168.50.195:3004`
> `CORS_ORIGINS=http://192.168.50.195:3003,http://localhost:3003`

## 5. 工具部署

### 5.1 GitNexus

```bash
# 确保 gitnexus 在 PATH 中
gitnexus --version

# 或指定绝对路径
GITNEXUS_BIN=/usr/local/bin/gitnexus
```

GitNexus 由后端 ProcessManager 自动管理。当前产品已移除旧 Wiki 页面、路由和进程管理；不要再为新部署配置旧 Wiki 端口或路径。

连续索引由后端有界队列串行调度，默认最多等待 8 个工作区；队列已满时新请求会被明确拒绝并提示稍后重试。遇到 GitNexus `429` 时，客户端指数退避上限为 30 秒，服务端 `Retry-After` 则会被优先遵守（安全上限 1 小时）；工作空间页会显示排队、索引、重试倒计时和冷却状态。不要通过重复点击创建并行索引进程。

### 5.2 外部 Agent 隔离

macOS 默认尝试 `sandbox-exec`，Linux 默认尝试 bubblewrap。工作区为只读范围，只有当前 run 的 artifact 目录允许写入；传给子进程的环境变量经过白名单过滤，网络策略和实际降级原因写入审计文件。

- `EXTERNAL_AGENT_SANDBOX_MODE=auto`：可用时隔离，不可用时显示中文降级提示。
- `EXTERNAL_AGENT_SANDBOX_MODE=required`：无支持的隔离器时拒绝启动。
- `EXTERNAL_AGENT_SANDBOX_MODE=off`：仅用于受控调试环境。

Windows 本轮保留 `.cmd` 解析和 transport 自动化覆盖，但未声明完成 Windows 实机隔离验收。

### 5.3 tiktoken 离线缓存

内网环境无法下载 tiktoken 编码文件，需提前准备：

```bash
# 在有网络的机器上执行
python -c "import tiktoken; tiktoken.encoding_for_model('gpt-4')"

# 将缓存文件拷贝到内网
# 默认位置: ~/.cache/tiktoken_v1/ 或 %LOCALAPPDATA%\tiktoken_v1\
# 拷贝到: data/tiktoken_cache/

# 确保环境变量设置
TIKTOKEN_CACHE_DIR=data/tiktoken_cache
```

### 5.4 补充部署

已完成初始部署后，可通过 deployer UI 单独安装 GitNexus，无需重新走全流程：

1. 打开 http://localhost:9000，进入「启动管理页」
2. 找到对应组件，点击「补充安装」，填写路径后启动
3. 向导自动完成依赖安装、进程启动、配置更新

**API 端点：**

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | /api/deploy/supplement/gitnexus | 安装并启动 GitNexus |

补充部署会自动：
1. 安装工具依赖（pip / npm）
2. 启动工具进程
3. 更新 `backend/.env` 写入新端口配置
4. 重启后端进程，使其热加载新配置
5. 执行健康检查，通过 SSE 实时推送进度

## 6. AI 配置

通过 UI「设置」页面配置 LLM，支持两种 API 协议：

### 6.1 Anthropic Messages API (Claude 系列)

| 字段 | 示例值 |
|------|--------|
| API 类型 | anthropic |
| Base URL | https://api.anthropic.com |
| API Key | sk-ant-xxx |
| 模型 | claude-sonnet-4-20250514 |

### 6.2 OpenAI 兼容 API (minimax, deepseek, qwen 等)

| 字段 | 示例值 |
|------|--------|
| API 类型 | openai_compat |
| Base URL | https://api.minimax.chat |
| API Key | your-api-key |
| 模型 | minimax-2.5 |

### 6.3 代理与 SSL

- **代理模式**: 不走代理 / 系统代理 / 自定义代理
- **SSL 证书**: 内网如有自签名证书，填写证书文件路径

## 7. 使用流程

1. **配置 AI**：设置页添加 LLM 或外部 Agent，并执行连接/命令探测。
2. **创建工作空间**：在浏览器选择真实仓库目录，确认源码可读；可选 GitNexus/CGC 不可用时允许明确降级。
3. **发布工作流**：在 `/workflows` 用向导和画布定义输入、Agent、Skills、MCP、输出和 DAG，验证、编译、试运行后发布不可变版本。
4. **创建 Task**：在 `/tasks/new` 选择已发布版本和已有工作空间，填写命名输入；默认继承执行配置，只保存明确覆盖。
5. **观察 Attempt**：在 `/tasks/{taskId}/runs/{runId}` 查看真实节点、增量事件、执行/质量/交付状态、中文失败原因和可下载 artifact。
6. **沉淀资产**：在语义库和证据库管理可复用测试用例、来源和 source slice。

完整用户流程见 [`WORKBENCH_V2_USER_GUIDE.md`](WORKBENCH_V2_USER_GUIDE.md)。

### 7.1 提示词模板

系统内置一个默认分析模板（7 步分析法），用户也可创建自定义模板。

- **模板选择器**: 新建分析页面提供下拉选择，包含系统默认和用户自建模板
- **即时编辑**: 选择模板后可在文本框中修改，修改仅影响本次分析
- **保存模板**: 对用户自建模板可直接保存修改
- **保存为新模板**: 将当前编辑内容另存为新模板（需命名）
- **占位符**: 模板中的 `{analysis_focus}` 会被自动替换为用户填写的"分析内容"
- **占位符保护**: 若用户删除了模板中的 `{analysis_focus}` 占位符，系统会自动将分析内容作为 `## 分析目标` 前置到提示词中，确保分析目标不丢失
- **系统模板**: 系统默认模板不可修改或删除
- **空模板校验**: 模板内容不可为空（API 层校验 `min_length=1`）

**AI 管线集成**: 用户填写的"分析内容"和渲染后的提示词模板会贯穿整个分析流程：
- 报告与 artifact 生成使用分析目标生成更聚焦的技术文档
- 每个模块的 LLM 分析会接收分析目标作为上下文
- 所有 6 份报告生成时都会以用户的分析目标为导向

**API 端点**: `GET/POST/PUT/DELETE /api/prompts`

### 7.2 内网代理隔离

内网环境中系统代理变量（`HTTP_PROXY`/`HTTPS_PROXY`）可能干扰后端与本地工具（GitNexus、CGC、Joern 等）之间的通信。Sprint 4 引入了统一的 `local_http_client` 工厂函数，所有本地服务连接强制 `trust_env=False`，不受系统代理影响。

- **LLM 外部调用**仍尊重代理设置（"系统代理"模式保持 `trust_env=True`）
- **本地服务调用**（GitNexus、CGC、Joern 等）一律绕过代理
- 如遇 504/连接超时，检查代理变量是否误干扰了 localhost 请求

### 7.3 LLM 调试快照

分析过程中每次 LLM 调用的输入/输出会自动保存为 JSON 快照，便于排查 AI 分析质量问题。

- **快照目录**: `data/outputs/{task_id}/debug/`
- **文件命名**: `{phase}_{report_type}_{timestamp}.json`，每次分析生成独立文件，重跑不覆盖
- **API 端点**:
  - `GET /api/tasks/{task_id}/debug` — 列出该任务的所有调试文件
  - `GET /api/tasks/{task_id}/debug/{filename}` — 读取单个调试文件内容

### 7.4 并行模块分析

模块分析阶段（Phase 2）使用 `asyncio.Semaphore(3)` 并行执行，最多 3 个模块同时进行 LLM 分析，显著缩短大型项目的分析时间。

### 7.5 健康检查容错

GitNexus 健康检查在 `/api/info` 返回 5xx 时，自动降级到 `POST /api/analyze` 探测。只要探测成功即视为在线，避免因 info 接口异常导致工具误判为离线。

## 8. 生成的报告

| 序号 | 报告 | 说明 |
|------|------|------|
| 01 | 项目与模块地图 | 项目整体架构和模块划分 |
| 02 | 关键业务流程分析 | 核心业务逻辑流程 |
| 03 | 源码定向阅读记录 | 关键代码片段分析 |
| 04 | 测试设计输入 | 基于代码的测试建议 |
| 05 | 需求与设计理解 | 需求文档分析（需上传文档） |
| 06 | 需求设计代码追踪 | 需求→设计→代码追溯（需上传文档） |

## 9. 故障排查

### 后端无法启动
```bash
# 检查端口是否占用
netstat -ano | findstr :3004

# 检查 Python 版本
python --version  # 需要 3.11+

# 检查依赖
pip list | findstr fastapi
```

### 前端无法连接后端
- 检查 `CORS_ORIGINS` 是否包含前端地址
- 检查防火墙是否放行 3004 端口
- 检查 `.env.local` 中 `NEXT_PUBLIC_API_URL` 是否正确

### 工具进程启动失败
- 检查 GitNexus 二进制是否在 PATH 中
- 检查后端 `.env` 中的 GitNexus/CGC 路径和端口是否与部署器配置一致
- 当前产品不再部署或管理旧 Wiki 组件；不要配置旧 Wiki 路径或端口
- 查看后端日志中的错误信息

### AI 分析报错
- 确认 LLM 配置正确（API Key、Base URL）
- 测试连接功能验证配置
- 检查网络代理设置（LLM 调用尊重系统代理，本地服务调用不走代理）
- LLM 读取超时已调至 300 秒，连接超时 15 秒
- 查看调试快照：`GET /api/tasks/{task_id}/debug` 查看每次 LLM 调用的输入输出
- 若使用"不走代理"模式仍超时，检查 Base URL 是否可达

### GitNexus 显示离线但实际可用
- 后端健康检查会先尝试 `/api/info`，失败后降级到 `POST /api/analyze` 探测
- 若两个端点都失败，检查 GitNexus 进程是否存活及端口 7100 是否监听
- 检查系统代理变量是否干扰 localhost 请求（本地服务连接已强制绕过代理）
