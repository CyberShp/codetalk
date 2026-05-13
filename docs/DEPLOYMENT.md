# CodeTalk Lightweight — 部署文档

> 版本: 2.0 | 分支: feat

## 1. 系统要求

| 组件 | 版本要求 |
|------|---------|
| Python | 3.11+ |
| Node.js | 18+ (推荐 20 LTS) |
| Git | 2.x |
| GitNexus | 最新版（放入 PATH 或配置绝对路径） |
| DeepWiki-Open | 最新版（可选，需本地安装） |

## 2. 端口规划

| 服务 | 端口 | 说明 |
|------|------|------|
| 前端 (Next.js) | 3005 | 用户界面 |
| 后端 API (FastAPI) | 8100 | REST API |
| GitNexus | 7100 | 代码图谱服务 |
| DeepWiki-Open API | 8001 | Wiki 生成 API |
| DeepWiki-Open UI | 3000 | DeepWiki 自带界面 |

> **禁用端口**: 3003, 3004 (Cat Cafe 保留)

## 3. 快速部署

### 3.1 克隆代码

```bash
git clone https://github.com/sr1shepard/codetalk.git
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
echo "NEXT_PUBLIC_API_URL=http://localhost:8100" > .env.local
```

### 3.4 启动服务

**后端:**
```bash
cd backend
.venv311\Scripts\activate     # Windows
uvicorn app.main:app --host 0.0.0.0 --port 8100 --reload
```

**前端:**
```bash
cd frontend
npm run dev
```

访问 http://localhost:3005 即可使用。

## 4. 环境变量

### 4.1 后端 (.env)

```env
# 数据存储
DATA_DIR=data
SQLITE_DB=data/codetalk.db

# 工具地址
GITNEXUS_BASE_URL=http://localhost:7100
DEEPWIKI_API_URL=http://localhost:8001
DEEPWIKI_UI_URL=http://localhost:3000

# 工具管理
GITNEXUS_PORT=7100
DEEPWIKI_API_PORT=8001
DEEPWIKI_UI_PORT=3000
DEEPWIKI_PATH=                   # DeepWiki-Open 安装目录
GITNEXUS_BIN=gitnexus            # GitNexus 二进制路径
TOOL_HEALTH_INTERVAL=30          # 健康检查间隔(秒)

# CORS（内网部署需添加客户端 IP）
CORS_ORIGINS=http://localhost:3005,http://127.0.0.1:3005
```

### 4.2 前端 (.env.local)

```env
NEXT_PUBLIC_API_URL=http://localhost:8100
```

> 内网部署时，将 `localhost` 替换为服务器 IP，例如:
> `NEXT_PUBLIC_API_URL=http://192.168.50.195:8100`
> `CORS_ORIGINS=http://192.168.50.195:3005,http://localhost:3005`

## 5. 工具部署

### 5.1 GitNexus

```bash
# 确保 gitnexus 在 PATH 中
gitnexus --version

# 或指定绝对路径
GITNEXUS_BIN=/usr/local/bin/gitnexus
```

GitNexus 由后端 ProcessManager 自动管理，也可通过 UI「工具状态」页面手动启停。

### 5.2 DeepWiki-Open（可选）

```bash
# 克隆 DeepWiki-Open
git clone https://github.com/AsyncFuncAI/deepwiki-open.git
cd deepwiki-open

# 安装 Python API 依赖
cd api && pip install -r requirements.txt && cd ..

# 安装前端依赖
npm install

# 设置 DEEPWIKI_PATH 环境变量指向此目录
DEEPWIKI_PATH=/path/to/deepwiki-open
```

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

1. **配置 AI**: 设置页 → 添加 LLM 配置 → 测试连接
2. **启动工具**: 工具状态页 → 启动 GitNexus（和 DeepWiki）
3. **创建分析**: 新建分析 → 填写仓库路径、任务名称 → 选择工具 → 开始
4. **查看结果**: 任务详情 → 报告查看 → 导出

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
netstat -ano | findstr :8100

# 检查 Python 版本
python --version  # 需要 3.11+

# 检查依赖
pip list | findstr fastapi
```

### 前端无法连接后端
- 检查 `CORS_ORIGINS` 是否包含前端地址
- 检查防火墙是否放行 8100 端口
- 检查 `.env.local` 中 `NEXT_PUBLIC_API_URL` 是否正确

### 工具进程启动失败
- 检查 GitNexus 二进制是否在 PATH 中
- 检查 `DEEPWIKI_PATH` 是否指向正确目录
- 查看后端日志中的错误信息

### AI 分析报错
- 确认 LLM 配置正确（API Key、Base URL）
- 测试连接功能验证配置
- 检查网络代理设置
- 小模型可能超时，增加 `MAX_TOKENS_PER_CALL` 阈值
