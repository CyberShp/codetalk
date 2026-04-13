# Phase 0: 验证 deepwiki-open Docker + API

**前置依赖：无（第一个执行）**
**完成后解锁：Phase 1A, 1B**
**预估复杂度：低**

## 铁律提醒
> CodeTalks 绝不编写任何分析逻辑。本阶段只是验证工具能跑通。

## 目标

在写任何代码之前，验证 deepwiki-open 的 Docker 镜像能运行、API 端点真实可用、响应格式符合预期。**不验证就不动手写代码。**

这是 v1 失败的教训：先假设工具 API 再写 adapter，结果工具跑不起来全白费。

## 步骤

### 1. 拉取并运行 deepwiki-open

```bash
docker pull ghcr.io/asyncfuncai/deepwiki-open:latest

# 最小启动（需要至少一个 LLM key）
docker run -d --name deepwiki-test \
  -p 8001:8001 -p 3001:3001 \
  -e OPENAI_API_KEY=${OPENAI_API_KEY} \
  ghcr.io/asyncfuncai/deepwiki-open:latest
```

### 2. 探索真实 API

不要依赖文档假设，用 curl 逐个探测：

```bash
# 健康检查
curl -s http://localhost:8001/health

# 列出所有路由（如果有 OpenAPI）
curl -s http://localhost:8001/openapi.json | jq '.paths | keys'

# 尝试已知端点
curl -s http://localhost:8001/api/wiki_cache
curl -s -X POST http://localhost:8001/api/wiki/generate \
  -H "Content-Type: application/json" \
  -d '{"repo_url": "https://github.com/example/small-repo"}'

# 尝试本地仓库端点
curl -s -X POST http://localhost:8001/local_repo/structure \
  -H "Content-Type: application/json" \
  -d '{"path": "/some/test/path"}'
```

### 3. 记录真实 API 行为

创建 `docs/deepwiki-api-actual.md`，记录：

| 项目 | 预期（旧计划） | 实际 |
|------|-------------|------|
| 健康检查端点 | `GET /health` | ? |
| Wiki 生成端点 | `POST /api/wiki/generate` | ? |
| RAG 问答端点 | `POST /chat/completions/stream` | ? |
| 本地仓库结构 | `POST /local_repo/structure` | ? |
| Wiki 缓存 | `GET /api/wiki_cache` | ? |
| 请求格式 | JSON body | ? |
| 响应格式 | JSON with markdown + mermaid | ? |
| 本地仓库支持 | 通过 path 参数 | ? |
| 生成耗时 | 未知 | ? |

### 4. 用一个小仓库测试全流程

找一个小型 C/C++ 开源仓库（或用你自己的项目），验证：
- Wiki 生成是否成功
- 输出是否包含 Markdown 文档
- 输出是否包含 Mermaid 图表
- 本地路径方式是否可用（volume mount）

### 5. 清理

```bash
docker stop deepwiki-test && docker rm deepwiki-test
```

## 验收标准

- [ ] deepwiki-open Docker 镜像拉取成功并正常启动
- [ ] 至少确认 3 个以上真实可用的 API 端点
- [ ] 记录了实际的请求/响应格式（不是猜测）
- [ ] 确认本地仓库路径方式是否可行
- [ ] 创建了 `docs/deepwiki-api-actual.md` 文档

## 决策门

| 结果 | 下一步 |
|------|--------|
| API 全部可用 | 进入 Phase 1A/1B |
| 部分 API 不可用 | 记录差异，调整 Phase 2 adapter 设计 |
| 完全不可用 | 升级到铲屎官，考虑换工具 |
