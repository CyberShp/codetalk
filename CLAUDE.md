# CodeTalks 项目规范

## 工作流程（每个终端必读）

当用户让你执行某个任务文件（如 `tasks/PHASE3E_adapter_joern.md`）时：

1. **先读取本文件（CLAUDE.md）的核心约束**，牢记禁止事项
2. **读取指定的任务文件**，理解具体步骤和验收标准
3. **读取已有代码**（如 `backend/app/adapters/base.py`），理解现有接口和约定
4. **执行任务**，严格按照任务文件中的步骤
5. **完成后自查验收标准**，逐条确认

如果你发现任务需要编写分析逻辑（解析代码、构建图、检测漏洞、搜索文本、生成文档），**立即停止并提醒用户**，这违反了核心约束。所有分析必须由外部工具完成。

## 任务文件目录

所有可执行的任务在 `tasks/` 目录下。每个文件开头标注了前置依赖和并行关系。用户自行决定执行顺序和并行度。

## 核心约束（铁律）

**CodeTalks 是纯编排+可视化层。所有代码分析由开源工具执行，CodeTalks 绝不编写任何分析逻辑。**

### 禁止事项
1. 禁止在后端编写源码解析逻辑（正则匹配代码、AST 遍历、import 解析）
2. 禁止在后端计算调用图、依赖图、任何图结构 — 调用 CodeCompass/Joern/GitNexus
3. 禁止在后端做安全分析、漏洞检测、污点追踪 — 调用 Joern
4. 禁止在后端生成文档 — 调用 deepwiki-open
5. 禁止在后端实现文本搜索 — 调用 Zoekt
6. Adapter 的 `analyze()` 只允许：(a) HTTP/RPC 调用工具 (b) 响应格式转换

### 验证方法
移除工具的 Docker 容器后，adapter 应报连接错误，而非静默产出结果。

## 技术栈
- 前端：Next.js + Tailwind CSS (Kinetic Shadow Framework 暗色主题)
- 后端：Python FastAPI
- 数据库：PostgreSQL
- 部署：Docker Compose
- 工具：Zoekt, CodeCompass, GitNexus, deepwiki-open, Joern

## 架构

```
Browser → Next.js(:3000) → FastAPI(:8000) → ┬─ Zoekt(:6070)
                                              ├─ CodeCompass(:6251)
                                              ├─ GitNexus(:7100)
                                              ├─ deepwiki(:8001)
                                              └─ Joern(:8080)
                                   ↕
                              PostgreSQL(:5432)
```

本地 host-run 端口和重启规矩见 [docs/LOCAL_RUNTIME_RULES.md](docs/LOCAL_RUNTIME_RULES.md)。不要把 Docker 网络地址直接套到 host-run 进程上。

## 目录结构

```
codetalk/
  docker-compose.yml
  .env.example
  backend/
    requirements.txt
    alembic.ini
    alembic/
    app/
      main.py
      config.py
      database.py
      models/          # SQLAlchemy models
      schemas/         # Pydantic schemas
      api/             # FastAPI routers
      adapters/        # 工具适配器（核心！）
        base.py        # BaseToolAdapter ABC
        zoekt.py
        codecompass.py
        gitnexus.py
        deepwiki.py
        joern.py
      services/        # 业务逻辑
        task_engine.py
        git_service.py
        source_manager.py
        ai_service.py
  frontend/
    src/
      app/             # Next.js App Router 页面
      components/      # React 组件
      lib/             # API client, WebSocket, types
  docker/              # 自建工具镜像的 Dockerfile
    codecompass/
    gitnexus/
    joern/
```

## UI 设计
复用 Downloads 中 stitch 系列的 Kinetic Shadow Framework 设计语言：
- 颜色：surface(#10141A), primary(#A4E6FF), secondary(#ECFFE3), tertiary(#FFD1CD)
- 字体：Space Grotesk(标题), Inter(UI), JetBrains Mono(代码/日志)
- 无 1px 边框，用背景色层级区分面板
- 毛玻璃效果：backdrop-filter: blur(12px)

## 工具能力矩阵

| 工具 | 能力 | API | 端口 |
|------|------|-----|------|
| Zoekt | 代码搜索 | JSON HTTP | 6070 |
| CodeCompass | 调用图/依赖图/指针分析 (C/C++/Python) | Thrift | 6251 |
| GitNexus | 知识图谱/AST/依赖 | HTTP (bridge mode) | 7100 |
| deepwiki-open | 文档生成/RAG问答 | REST | 8001 |
| Joern | CPG/污点分析/安全扫描/调用图 | HTTP+CPGQL | 8080 |


<!-- CAT-CAFE-GOVERNANCE-START -->
> Pack version: 1.3.0 | Provider: claude

## Cat Cafe Governance Rules (Auto-managed)

### Hard Constraints (immutable)
- **Public local defaults**: use frontend 3003 and API 3004 to avoid colliding with another local runtime.
- **Redis port 6399** is Cat Cafe's production Redis. Never connect to it from external projects. Use 6398 for dev/test.
- **No self-review**: The same individual cannot review their own code. Cross-family review preferred.
- **Identity is constant**: Never impersonate another cat. Identity is a hard constraint.

### Collaboration Standards
- A2A handoff uses five-tuple: What / Why / Tradeoff / Open Questions / Next Action
- Vision Guardian: Read original requirements before starting. AC completion ≠ feature complete.
- Review flow: quality-gate → request-review → receive-review → merge-gate
- Skills are available via symlinked cat-cafe-skills/ — load the relevant skill before each workflow step
- Shared rules: See cat-cafe-skills/refs/shared-rules.md for full collaboration contract

### Quality Discipline (overrides "try simplest approach first")
- **Bug: find root cause before fixing**. No guess-and-patch. Steps: reproduce → logs → call chain → confirm root cause → fix
- **Uncertain direction: stop → search → ask → confirm → then act**. Never "just try it first"
- **"Done" requires evidence** (tests pass / screenshot / logs). Bug fix = red test first, then green

### Knowledge Engineering
- Documents use YAML frontmatter (feature_ids, topics, doc_kind, created)
- Three-layer info architecture: CLAUDE.md (≤100 lines) → Skills (on-demand) → refs/
- Backlog: BACKLOG.md (hot) → Feature files (warm) → raw docs (cold)
- Feature lifecycle: kickoff → discussion → implementation → review → completion
- SOP: See docs/SOP.md for the 6-step workflow
<!-- CAT-CAFE-GOVERNANCE-END -->
