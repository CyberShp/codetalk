# CodeTalks 产品架构 v2 — 工具分工与信息架构

> 作者: 布偶猫/宪宪 | 审阅: 缅因猫(GPT-5.4) | 日期: 2026-04-15
> 触发: 铲屎官反馈 — GitNexus AI 能力被低估、deepwiki 单页不够、AI 对话窗口产品形态没到位

## 1. 核心发现

### 1.1 GitNexus — 已验证 vs 待验证能力

| 端点 | 能力 | 当前状态 |
|------|------|----------|
| `/api/graph` | 知识图谱（节点+边+社区+流程） | ✅ 已接入、已验证 |
| `/api/analyze` | 仓库索引触发 | ✅ 已接入、已验证 |
| `/api/file` | 文件代码切片 | ✅ 代理接入、已验证 |
| `/api/query` | BM25 + 语义混合搜索 | ⚠️ Phase 3C spec 提及，bridge 版本未验证 |
| `/api/context` | 360° 符号分析（调用者/被调用者/依赖） | ⚠️ Phase 3C spec 提及，bridge 版本未验证 |
| `/api/impact` | 爆炸半径 / 影响面分析 | ⚠️ Phase 3C spec 提及，bridge 版本未验证 |
| `/api/process` | 独立流程路径端点 | ⚠️ Phase 3C spec 提及，bridge 版本未验证 |

**重要纠正 1**: GitNexus 的 HTTP bridge API 本身**不提供 AI/Chat 端点**。GitHub README 中提到的 "AI-powered chat interface" 和 "Graph RAG agent" 是 GitNexus 自带前端的功能（Cypher/agent 仍在 WIP），不是 bridge API 的一部分。

**重要纠正 2**: `/api/query`、`/api/context`、`/api/impact`、`/api/process` 这四个端点来源于 Phase 3C spec 的推测，**尚未在当前 bridge 版本中实测确认**。在验证前不能作为产品规划的确定输入。

### 1.2 deepwiki-open — 我们只用了 MVP 模式

| 端点 | 能力 | 当前状态 |
|------|------|----------|
| `/chat/completions/stream` | RAG 流式问答 | ✅ 文档生成 + Chat |
| `/local_repo/structure` | 仓库结构获取 | ✅ prepare() |
| `GET /api/wiki_cache` | 获取结构化多页 Wiki | ❌ 未接入 |
| `POST /api/wiki_cache` | 存储 Wiki 缓存 | ❌ 未接入 |
| `POST /export/wiki` | Wiki 导出（MD/JSON） | ❌ 未接入 |
| `GET /api/processed_projects` | 已处理项目列表 | ❌ 未接入 |
| `GET /models/config` | LLM 模型配置 | ❌ 未接入 |

**关键发现**: 多页 Wiki 生成不是一个 API 调用——是 deepwiki 的 Next.js 前端**编排多次** `/chat/completions/stream` 调用来实现的。每个 WikiPage 是一次独立的流式调用。

## 2. 产品分工（三层模型）

```
┌──────────────────────────────────────────────────────────┐
│  CodeTalks — 统一编排层                                    │
│  chat/workspace/orchestration                             │
│  决定把哪种上下文喂给哪种模型                                │
│                                                          │
│  ┌─────────────────────┐  ┌─────────────────────────┐   │
│  │  deepwiki-open      │  │  GitNexus               │   │
│  │  ═══════════════    │  │  ═════════              │   │
│  │  叙事型文档引擎      │  │  结构型代码智能引擎      │   │
│  │                     │  │                         │   │
│  │  • 多页面 Wiki 生成  │  │  已验证:                │   │
│  │  • 章节目录导航      │  │  • 知识图谱可视化       │   │
│  │  • RAG 检索/问答     │  │  • 文件代码切片         │   │
│  │  • 文档缓存 + 导出   │  │                         │   │
│  │  • Mermaid 架构图    │  │  待验证:                │   │
│  │                     │  │  • 代码搜索 (query)     │   │
│  │                     │  │  • 符号上下文 (context)  │   │
│  │                     │  │  • 影响面分析 (impact)   │   │
│  └─────────────────────┘  └─────────────────────────┘   │
│                                                          │
│  两个工具都是 上下文提供者，CodeTalks 决定编排方式          │
└──────────────────────────────────────────────────────────┘
```

### 边界规则

1. **deepwiki 不做代码结构分析** — 它只读文件内容做 RAG
2. **GitNexus 不做 AI 推理** — 它只返回结构化数据（图、路径、搜索结果）
3. **Chat 当前先走 deepwiki，长期由 CodeTalks 统一编排** — 当前实现复用 deepwiki 的 RAG chat 通道；长期目标是 CodeTalks 自己做 chat orchestration，deepwiki 和 GitNexus 都只是上下文提供者。这样避免 deepwiki 运行时瓶颈（如 embedding 阻塞）连锁影响其他能力
4. **CodeTalks 是编排层** — 组合两个工具的输出，不自己做分析；但 chat/workspace 的路由和上下文组装是 CodeTalks 的职责

## 3. 信息架构方案

### 3.1 任务详情页 — 目标布局

```
┌──────────────────────────────────────────────────────────┐
│  Task: SPDK 全仓分析         ▼ 任务信息（可折叠）         │
├──────────┬───────────────────────────────────────────────┤
│          │  [ 文档 ]  [ 图谱 ]  [ 发现 ]  [ 摘要 ]       │
│  Wiki    │                                               │
│  目录    │  ┌─────────────────────────────────────────┐  │
│          │  │                                         │  │
│ ▸ 概览   │  │   当前页面内容 (Markdown + Mermaid)      │  │
│ ▸ 架构   │  │                                         │  │
│   ├ 设计 │  │   — 系统设计图 (Mermaid)                 │  │
│   └ 数据流│  │   — 组件说明                             │  │
│ ▸ 核心组件│  │   — 代码示例                             │  │
│ ▸ 实现细节│  │                                         │  │
│          │  └─────────────────────────────────────────┘  │
│  ────────│                                               │
│  导出    │  上一页 / 下一页                               │
│  [MD][JSON]                                               │
├──────────┴───────────────────────────────────────────────┤
│  AI 对话工作台                                    [展开▲] │
│  ┌──────────────────────────────────────────────────────┐│
│  │ 🤖 这个仓库使用了事件驱动架构...                       ││
│  │ 👤 NVMe 驱动的初始化流程是什么？                       ││
│  │ 🤖 NVMe 驱动初始化经过以下步骤: 1) 控制器发现...       ││
│  ├──────────────────────────────────────────────────────┤│
│  │ [输入问题...]                              [发送]    ││
│  └──────────────────────────────────────────────────────┘│
└──────────────────────────────────────────────────────────┘
```

### 3.2 Tab 职责定义

| Tab | 数据源 | 内容 |
|-----|--------|------|
| **文档** | deepwiki wiki_cache | 多页 Wiki + TOC 侧栏 + Mermaid 图 |
| **图谱** | GitNexus /api/graph | 知识图谱 + 代码面板（搜索/上下文/影响面待 bridge 验证后评估） |
| **发现** | GitNexus intelligence | 社区聚类 + 流程路径 + 跨社区分析 |
| **摘要** | LLM (via task_engine) | AI 摘要（综合所有工具结果） |

### 3.3 AI 对话工作台设计

**从浮球升级为底部可展开面板**:

- 默认折叠，显示一行提示："AI 助手就绪，点击展开对话"
- 展开后占据底部 40% 高度（可拖拽调整）
- 上下文感知：当前 tab + 当前 wiki page 自动注入上下文
- 支持引用：用户可以框选文档/图谱内容，点击"询问 AI"

**与浮球的区别**:

| 维度 | 当前浮球 | 升级后工作台 |
|------|---------|------------|
| 位置 | 固定右下角，悬浮 | 底部集成面板 |
| 大小 | 320×500px | 全宽，高度可调 |
| 上下文 | 无 | 自动感知当前页面/选中内容 |
| 使用感 | "辅助工具" | "一等公民工作区" |

## 4. 实施优先级

### Phase A: deepwiki 多章节 Wiki（高优先）

**目标**: 文档从"一页长文"升级为"多页导航 Wiki"

**后端改动**:
1. 新增 Wiki 编排服务 — 参考 deepwiki 前端逻辑，多次调用 `/chat/completions/stream` 生成各页
2. 每页生成后通过 `POST /api/wiki_cache` 存储
3. 后续访问从 `GET /api/wiki_cache` 读取缓存
4. `POST /export/wiki` 支持导出

**前端改动**:
1. 文档 tab 增加 TOC 侧栏组件
2. WikiPage 渲染器（替代现有的扁平 Markdown 渲染）
3. 页间导航（上一页/下一页 + relatedPages 链接）
4. 导出按钮

**关键决策 — Wiki 编排方式**:

| 方案 | 描述 | 优劣 |
|------|------|------|
| A. 自编排 | 后端多次调 /chat/completions/stream，自建页面结构 | 控制力强，但要理解 prompt 策略 |
| B. 复用缓存 | 通过 deepwiki 前端(3001)触发生成，后端从 wiki_cache 读 | 简单，但依赖外部前端 |
| C. 混合 | 先用 B 快速上线，逐步迁移到 A | 渐进式，推荐 |

**推荐: 方案 C**

### Phase B: AI 对话工作台化（中优先）

**目标**: Chat 从浮球挂件升级为一等公民面板

**改动**:
1. 新增 `ChatWorkspace` 组件替代 `FloatingChat`
2. 底部可展开面板布局
3. 上下文注入：当前 wiki page 内容 / 选中文本
4. 消息历史持久化（localStorage 或后端）

### Phase C: GitNexus 扩展能力（待验证后启用）

**前提**: 先验证 bridge 版本是否真实暴露 `/api/query`、`/api/context`、`/api/impact` 端点。

**验证步骤** (Phase C 启动前必须完成):
1. `curl http://gitnexus:7100/api/query` — 确认端点存在、返回格式
2. `curl http://gitnexus:7100/api/context` — 同上
3. `curl http://gitnexus:7100/api/impact` — 同上
4. 如果端点不存在或返回 404，Phase C 降级为"等待 GitNexus 上游支持"

**验证通过后的实施计划**:
- 后端: GitNexus adapter 增加对应方法 + 新增代理端点
- 前端: 图谱 tab 增加搜索栏、节点右键菜单、影响面可视化
- AI 解读: 通过 CodeTalks chat orchestration 层，将 GitNexus 结构数据作为上下文喂给 LLM

## 5. 开放问题

1. **Wiki 编排 prompt 策略** — deepwiki 前端用什么 prompt 来决定页面结构？需要研究其 Next.js 源码 (`/app/[owner]/[repo]/page.tsx`)
2. **wiki_cache 的 owner/repo 参数** — 我们的仓库是本地路径，不是 GitHub URL。需要确认 cache key 的映射方式
3. **GitNexus bridge 端点验证** — `/api/query`、`/api/context`、`/api/impact` 在当前 bridge 版本中是否存在？Phase C 启动前必须实测确认（Phase 3C spec 也标注了"需研究确认"）
4. **Chat 上下文窗口大小** — 当前 wiki page 内容如果很长，注入到 chat 上下文会不会超 token 限制？
5. **Chat orchestration 演进路径** — 当前 chat 走 deepwiki proxy，何时以及如何迁移到 CodeTalks 自有编排？需要定义 deepwiki 作为"RAG 检索器"和"LLM 通道"两个角色的解耦点

## 6. 不做的事情

- ❌ 不在 CodeTalks 后端实现任何 AST 分析、图谱构建、RAG 检索
- ❌ 不 iframe 嵌入 deepwiki 前端（失去 UI 一致性）
- ❌ 不做实时协同编辑 Wiki（只读展示 + AI 生成）
- ❌ 不把未验证的 GitNexus 端点写入实施计划（先验证再规划）
